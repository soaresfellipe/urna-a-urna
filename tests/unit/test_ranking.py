"""Testes unitários do ranking combinado (architecture.md §4.7).

Cobrem: ordenação global (q ascendente, z descendente), agrupamento dos
achados da mesma seção, severidade da seção (menor q / maior z; integridade
contribui q 0,0 e não tem z), frases explicativas com os valores observado e
de referência em pt-BR, e a serialização segura de z infinito.
"""

from __future__ import annotations

import json
import math

import pytest

from pipeline import ranking
from pipeline.erros import RankingError

LOCAL_999 = {
    "municipio_id": 97012,
    "municipio": "BRASÍLIA",
    "local_id": 101,
    "local": "ESCOLA A",
    "endereco": "SQS TESTE",
}


def _achado_divergencia(
    *,
    zona=1,
    secao=999,
    q=0.008,
    z=7.1,
    percentual=45.2,
    mediana=22.1,
    tamanho=48,
    tipo="local",
    candidato="BOLSONARO",
):
    return {
        "modulo": "divergencia",
        "zona": zona,
        "secao": secao,
        "cargo_id": 1,
        "cargo": "Presidente",
        "votavel": 22,
        "candidato": candidato,
        "votos_candidato": 452,
        "percentual": percentual,
        "mediana": mediana,
        "mad": 2.0,
        "z_robusto": z,
        "z_tamanho": 10.0,
        "diferenca_pp": percentual - mediana,
        "q_valor": q,
        "tipo_referencia": tipo,
        "tamanho_grupo": tamanho,
        "votos_validos": 1000,
        "local_id": 101,
        "local": "ESCOLA A",
    }


def _achado_cruzado(
    *,
    zona=1,
    secao=999,
    rotulo="Bolsonaro (22) × Ibaneis (15)",
    diferenca=12.4,
    mediana=2.1,
    q=0.010,
    z=5.3,
    nivel="zona",
    tamanho=355,
):
    return {
        "modulo": "cruzado",
        "rotulo": rotulo,
        "presidente": "Bolsonaro",
        "governador": "Ibaneis Rocha",
        "zona": zona,
        "secao": secao,
        "local_id": 101,
        "local": "ESCOLA A",
        "diferenca": diferenca,
        "percentual_presidente": 56.0,
        "percentual_governador": 40.0,
        "votos_validos_presidente": 1000,
        "votos_validos_governador": 900,
        "mediana": mediana,
        "mad": 2.0,
        "z_robusto": z,
        "z_tamanho": 7.0,
        "diferenca_pp": abs(diferenca - mediana),
        "q_valor": q,
        "nivel_referencia": nivel,
        "tamanho_grupo": tamanho,
    }


def _achado_integridade(*, zona=1, secao=12, regra="R1", observado=1234, esperado=1230):
    return {
        "regra": regra,
        "cargo_id": 1,
        "cargo": "Presidente",
        "zona": zona,
        "secao": secao,
        "votavel": None if regra != "R3" else 15,
        "votavel_nome": None if regra != "R3" else "IBANEIS ROCHA",
        "valor_observado": observado,
        "valor_esperado": esperado,
        "excecao_aplicada": None,
    }


def _ranquear(achados, *, locais=None, total=6748):
    return ranking.ranquear(
        achados,
        uf="DF",
        ano=2022,
        turno=1,
        locais_por_secao=locais if locais is not None else {(1, 999): LOCAL_999},
        total_secoes=total,
    )


# ---------------------------------------------------------------------------
# Ordenação global: q ascendente, z descendente
# ---------------------------------------------------------------------------


def test_ordenacao_global_q_ascendente_e_z_descendente():
    ranqueado = _ranquear(
        [
            _achado_divergencia(zona=1, secao=300, q=0.01, z=20.0),
            _achado_divergencia(zona=1, secao=100, q=0.008, z=7.0),
            _achado_divergencia(zona=1, secao=200, q=0.008, z=9.0),
        ]
    )
    ordem = [(e["zona"], e["secao"]) for e in ranqueado["sinalizacoes"]]
    # Menor q primeiro; empate de q resolvido pelo maior z.
    assert ordem == [(1, 200), (1, 100), (1, 300)]


def test_secao_nao_duplica_e_achados_da_mesma_secao_ficam_agrupados():
    ranqueado = _ranquear(
        [
            _achado_cruzado(q=0.004),
            _achado_divergencia(q=0.008, z=7.1),
        ]
    )
    assert len(ranqueado["sinalizacoes"]) == 1
    entrada = ranqueado["sinalizacoes"][0]
    assert (entrada["zona"], entrada["secao"]) == (1, 999)
    assert len(entrada["achados"]) == 2
    assert ranqueado["total_sinalizadas"] == 1
    assert ranqueado["total_achados"] == 2
    # Severidade da seção: menor q / maior z entre os achados.
    assert entrada["severidade"]["q"] == pytest.approx(0.004)
    assert entrada["severidade"]["z"] == pytest.approx(7.1)
    # Achados ordenados dentro da seção: menor q primeiro.
    assert entrada["achados"][0]["q"] <= entrada["achados"][1]["q"]


def test_integridade_contribui_q_zero_e_nao_tem_z_na_severidade():
    ranqueado = _ranquear(
        [
            _achado_divergencia(zona=2, secao=500, q=0.01, z=20.0),
            _achado_integridade(zona=1, secao=12),
        ]
    )
    primeira = ranqueado["sinalizacoes"][0]
    assert (primeira["zona"], primeira["secao"]) == (1, 12)
    assert primeira["severidade"]["q"] == 0.0
    assert primeira["severidade"]["z"] is None  # achado determinístico, sem z
    segundas = [(e["zona"], e["secao"]) for e in ranqueado["sinalizacoes"]]
    assert segundas == [(1, 12), (2, 500)]


def test_ranquear_sem_achados_devolve_lista_vazia():
    ranqueado = _ranquear([])
    assert ranqueado["sinalizacoes"] == []
    assert ranqueado["total_sinalizadas"] == 0
    assert ranqueado["total_achados"] == 0
    assert ranqueado["total_secoes"] == 6748


def test_entrada_usa_o_metadado_do_locais_e_identificadores_do_achado():
    ranqueado = _ranquear([_achado_divergencia()])
    entrada = ranqueado["sinalizacoes"][0]
    assert entrada["municipio"] == "BRASÍLIA"
    assert entrada["municipio_id"] == 97012
    assert entrada["local_id"] == 101
    assert entrada["local"] == "ESCOLA A"
    assert entrada["endereco"] == "SQS TESTE"


def test_entrada_sem_local_no_mapa_cai_no_campo_do_achado():
    ranqueado = _ranquear([_achado_divergencia(zona=3, secao=700)], locais={}, total=6748)
    entrada = ranqueado["sinalizacoes"][0]
    assert entrada["municipio"] is None
    assert entrada["local"] == "ESCOLA A"


# ---------------------------------------------------------------------------
# Frases explicativas (pt-BR, valores observado e de referência)
# ---------------------------------------------------------------------------


def test_frase_divergencia_traz_valores_observado_referencia_z_e_q():
    achado = ranking.achado_publico(_achado_divergencia())
    frase = achado["frase"]
    assert "BOLSONARO" in frase
    assert "45,2%" in frase  # observado
    assert "22,1%" in frase  # referência (mediana)
    assert "48" in frase  # tamanho do grupo leave-one-out
    assert "do mesmo local" in frase
    assert "z=7,1" in frase and "q=0,008" in frase


def test_frase_divergencia_com_referencia_de_zona():
    achado = ranking.achado_publico(_achado_divergencia(tipo="zona", mediana=30.0, tamanho=355))
    assert "da mesma zona" in achado["frase"]
    assert "355" in achado["frase"]


def test_frase_cruzado_traz_par_diferenca_mediana_z_e_q():
    achado = ranking.achado_publico(_achado_cruzado())
    frase = achado["frase"]
    assert "Par Bolsonaro (22) × Ibaneis (15)" in frase
    assert "diferença de 12,4 pp" in frase  # observado
    assert "mediana de 2,1 pp" in frase  # referência
    assert "na zona" in frase
    assert "z=5,3" in frase and "q=0,010" in frase


def test_frase_cruzado_com_referencia_de_municipio():
    achado = ranking.achado_publico(_achado_cruzado(nivel="municipio", tamanho=600))
    assert "no município" in achado["frase"]
    # O tamanho do grupo fica no achado (campo próprio), não na frase do
    # módulo 3 — o formato do design cita observado, referência, z e q.
    assert achado["tamanho_grupo"] == 600


def test_frase_integridade_continua_com_regra_e_valores():
    achado = ranking.achado_publico(_achado_integridade())
    frase = achado["frase"]
    assert "Presidente" in frase
    assert "1.234" in frase and "1.230" in frase
    assert "R1" in frase


def test_frase_com_valores_negativos_e_signo_preservado():
    achado = ranking.achado_publico(_achado_cruzado(diferenca=-4.0, mediana=-20.0, z=5.4))
    frase = achado["frase"]
    assert "diferença de -4,0 pp" in frase
    assert "mediana de -20,0 pp" in frase


# ---------------------------------------------------------------------------
# Serialização segura e erros
# ---------------------------------------------------------------------------


def test_z_infinito_vira_nulo_no_json_e_frase_mostra_infinidade():
    cru = _achado_cruzado()
    cru["z_robusto"] = math.inf  # MAD 0 com divergência (fixture homogênea)
    achado = ranking.achado_publico(cru)
    assert achado["z"] is None  # JSON não representa infinito
    assert achado["z_robusto"] is None
    assert "z=∞" in achado["frase"]
    # O documento emitido continua JSON estritamente válido.
    json.dumps(achado, ensure_ascii=False)


def test_modulo_desconhecido_falha_de_forma_orientada():
    achado = _achado_integridade()
    achado["modulo"] = "mistico"
    with pytest.raises(RankingError) as excinfo:
        ranking.achado_publico(achado)
    assert "mistico" in str(excinfo.value)


def test_relatar_imprime_resumo_da_lista(capsys):
    ranqueado = _ranquear([_achado_cruzado(), _achado_divergencia(q=0.008)])
    ranking.relatar(ranqueado)
    saida = capsys.readouterr().out
    assert "[ranking]" in saida
    assert "1 seção" in saida
    assert "2 achados" in saida
    assert "cruzado" in saida and "divergencia" in saida


def test_relatar_sem_sinalizacoes_mostra_lista_vazia(capsys):
    ranking.relatar(_ranquear([]))
    saida = capsys.readouterr().out
    assert "nenhuma seção" in saida
