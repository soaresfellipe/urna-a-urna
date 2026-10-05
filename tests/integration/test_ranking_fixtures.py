"""Integração do ranking combinado com fixtures sintéticas (VAL-RANK-001).

O mini-pleito é empacotado e ingerido pelo caminho REAL do pipeline; os três
módulos conferem de verdade e a emissão grava o sinalizacoes.json ranqueado:
achados de módulos distintos na mesma seção ficam agrupados (uma entrada por
seção, nunca duplicada) e a seção com menor q-valor aparece primeiro (empate
resolvido pelo maior z). Toda entrada carrega frases com os valores
observado, de referência, z e q do próprio achado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import cruzado, divergencia, emitir, ingest, integridade, totais
from pipeline.config import carregar_config
from tests.fixtures import gerador

# Local A (zona 1) com padrão que dá MAD 3 pp e mediana 39% no candidato 22:
# p22 cicla em 36..44% (v22 = 360, 380, ..., 440 de 1000 válidos) e o
# governador fica fixo em 60% de Ibaneis (v15 = 600 de 1000 válidos).
# d do par 1 (22 × 15) nos homogêneos: −24, −22, ..., −16 pp (mediana −22).
PRES_HOMOGEO = [{22: 360 + 20 * (i % 5), 13: 1000 - (360 + 20 * (i % 5))} for i in range(9)]
GOV_HOMOGEO = {15: 600, 43: 400}

# Seção plantada: p22 = 64,6% (25,6 pp acima da mediana 39 do local) e
# Ibaneis a 60% — d = 4,6 pp (26,6 pp acima da mediana −22 da zona).
# MAD 2–3 pp dá z robusto ≥ 5,7 (finito, ≥ 5) nos dois módulos; o erro
# combinado dá z de tamanho ≥ 11. Candidato 13 e o par 2 também são
# sinalizados (a plantada mexe nos dois lados), o que exercita o agrupamento.
PRES_PLANTADA = {22: 620, 13: 340}


def _cargo(cargo_id: int, votos: dict[int, int]):
    return gerador.pleito_do_cargo(cargo_id, aptos=sum(votos.values()) + 150, votos=dict(votos))


def _adicionar(
    pleito: gerador.PleitoSintetico,
    *,
    zona: int,
    secao: int,
    local_id: int,
    local_nome: str,
    pres: dict[int, int],
) -> None:
    pleito.adicionar_secao(
        zona=zona,
        secao=secao,
        local_id=local_id,
        local_nome=local_nome,
        local_endereco="SQS TESTE",
        cargos={
            gerador.CARGO_PRESIDENTE: _cargo(gerador.CARGO_PRESIDENTE, pres),
            gerador.CARGO_GOVERNADOR: _cargo(gerador.CARGO_GOVERNADOR, dict(GOV_HOMOGEO)),
        },
    )


def _pleito_com_achados_de_tres_modulos() -> gerador.PleitoSintetico:
    pleito = gerador.PleitoSintetico()
    for indice, pres in enumerate(PRES_HOMOGEO):
        _adicionar(
            pleito,
            zona=1,
            secao=indice + 1,
            local_id=101,
            local_nome="ESCOLA A",
            pres=pres,
        )
    # Achado de integridade (R1): comparecimento ≠ soma dos votos, sem tocar
    # nos percentuais (a soma dos votos segue válida para os módulos 2 e 3).
    _adicionar(
        pleito,
        zona=1,
        secao=888,
        local_id=101,
        local_nome="ESCOLA A",
        pres=dict(PRES_HOMOGEO[0]),
    )
    gerador.injetar_r1(
        pleito, zona=1, secao=888, cargo_id=gerador.CARGO_PRESIDENTE, comparecimento=800
    )
    # Seção plantada: gera achado de divergência (candidato 22 a 56%) E de
    # voto cruzado (d = −4 pp contra mediana −20 pp) na MESMA seção.
    _adicionar(
        pleito,
        zona=1,
        secao=999,
        local_id=101,
        local_nome="ESCOLA A",
        pres=dict(PRES_PLANTADA),
    )
    return pleito


def _executar_pleito(pasta: Path, pleito: gerador.PleitoSintetico) -> dict:
    config = carregar_config()
    gerador.empacotar(pasta, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=pasta)
    relatorio_totais = totais.conferir(uf="DF", turno=1, data_dir=pasta)
    resultado_integ = integridade.conferir(
        uf="DF", turno=1, ano=2022, data_dir=pasta, config=config
    )
    resultado_div = divergencia.conferir(uf="DF", turno=1, ano=2022, data_dir=pasta, config=config)
    resultado_cruz = cruzado.conferir(uf="DF", turno=1, ano=2022, data_dir=pasta, config=config)
    saida = pasta / "public" / "data"
    return emitir.emitir(
        uf="DF",
        ano=2022,
        turno=1,
        data_dir=pasta,
        out_dir=saida,
        config=config,
        relatorio_totais=relatorio_totais,
        resultado_integridade=resultado_integ,
        resultado_divergencia=resultado_div,
        resultado_cruzado=resultado_cruz,
    )


def _ler_json(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_pleito_com_achados_de_tres_modulos_gera_ranking(tmp_path):
    _executar_pleito(tmp_path, _pleito_com_achados_de_tres_modulos())
    documento = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")
    entradas = documento["sinalizacoes"]
    # Duas seções sinalizadas: (1, 888) só integridade; (1, 999) divergência
    # (candidatos 22 e 13) e cruzado (os dois pares) agrupados na MESMA
    # entrada — a seção nunca aparece duplicada na lista.
    assert documento["total_secoes"] == 11
    assert documento["total_sinalizadas"] == 2
    assert documento["total_achados"] == 5
    ordem = [(e["zona"], e["secao"]) for e in entradas]
    # Menor q primeiro: integridade é determinística (q 0,0) e vem antes dos
    # achados estatísticos da seção plantada.
    assert ordem == [(1, 888), (1, 999)]


def test_achados_de_modulos_distintos_ficam_agrupados_na_mesma_secao(tmp_path):
    _executar_pleito(tmp_path, _pleito_com_achados_de_tres_modulos())
    entradas = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")["sinalizacoes"]
    assert {e["secao"] for e in entradas} == {888, 999}  # sem seção duplicada
    plantada = next(e for e in entradas if e["secao"] == 999)
    assert {a["modulo"] for a in plantada["achados"]} == {"cruzado", "divergencia"}
    assert len(plantada["achados"]) == 4
    # Severidade: menor q / maior z entre os achados da seção.
    qs = [a["q"] for a in plantada["achados"]]
    zs = [a["z"] for a in plantada["achados"] if a["z"] is not None]
    assert plantada["severidade"]["q"] == min(qs)
    assert plantada["severidade"]["z"] == max(zs)


def test_toda_entrada_tem_frase_com_os_valores_do_proprio_achado(tmp_path):
    _executar_pleito(tmp_path, _pleito_com_achados_de_tres_modulos())
    entradas = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")["sinalizacoes"]
    por_modulo = {}
    for e in entradas:
        for a in e["achados"]:
            por_modulo.setdefault(a["modulo"], []).append(a["frase"])
    # Divergência (2 candidatos): percentuais observado e de referência,
    # grupo do local, z e q em cada frase.
    div = por_modulo["divergencia"]
    assert len(div) == 2
    assert any("64,6%" in f and "39,0%" in f for f in div)  # observado × mediana
    assert any("35,4%" in f and "61,0%" in f for f in div)
    assert all("10" in f for f in div)  # demais 10 seções do local (11 − a própria)
    assert all("do mesmo local" in f for f in div)
    assert all("z=" in f and "q=" in f for f in div)
    # Cruzado (2 pares): diferença observada, mediana da zona, par
    # identificado pelo rótulo do config, z e q.
    cruz = por_modulo["cruzado"]
    assert len(cruz) == 2
    assert any(
        "Par Bolsonaro (22) × Ibaneis (15)" in f
        and "diferença de 4,6 pp" in f
        and "mediana de -21,0 pp" in f
        and "na zona" in f
        for f in cruz
    )
    assert any("Par Lula (13) × Grass (43)" in f and "mediana de 21,0 pp" in f for f in cruz)
    assert all("z=" in f and "q=" in f for f in cruz)
    # Integridade: valores observado e esperado da regra.
    integ = por_modulo["integridade"]
    assert len(integ) == 1
    assert "R1" in integ[0]
    assert "1.000" in integ[0] and "800" in integ[0]


def test_tipos_e_niveis_de_referencia_registrados(tmp_path):
    _executar_pleito(tmp_path, _pleito_com_achados_de_tres_modulos())
    entradas = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")["sinalizacoes"]
    plantada = next(e for e in entradas if e["secao"] == 999)
    divergencia = next(a for a in plantada["achados"] if a["modulo"] == "divergencia")
    cruzado_pub = next(a for a in plantada["achados"] if a["modulo"] == "cruzado")
    assert divergencia["tipo_referencia"] == "local"
    assert divergencia["valor_observado"] == pytest.approx(64.583, rel=1e-3)
    assert divergencia["valor_referencia"] == pytest.approx(39.0)
    assert cruzado_pub["tipo_referencia"] == "zona"
    assert cruzado_pub["nivel_referencia"] == "zona"
    assert cruzado_pub["valor_observado"] == pytest.approx(4.583, rel=1e-3)
    assert cruzado_pub["valor_referencia"] == pytest.approx(-21.0)
