"""Integração do módulo 2 com fixtures sintéticas e percentuais plantados.

O mini-pleito é empacotado como zips + manifesto, ingerido pelo caminho REAL
de pipeline/ingest.py e analisado pelo módulo 2 de verdade (pipeline/
divergencia.py). Percentuais de candidato inflados são plantados e detectados
com os valores exatos; seções típicas do mesmo local NÃO podem ser
sinalizadas (sem falsos positivos).
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import RAIZ, divergencia, ingest
from pipeline.config import carregar_config
from tests.fixtures import gerador

# Pleito homogêneo de referência: validos 320; p22 = 45%, p13 = 48,75%,
# p15 = 6,25%.
PRES_HOMOGEO = {22: 144, 13: 156, 15: 20}
GOV_HOMOGEO = {15: 150, 43: 100}  # validos 250; p15 = 60%, p43 = 40%


def _cargo(cargo_id: int, votos: dict[int, int]):
    return gerador.pleito_do_cargo(cargo_id, aptos=sum(votos.values()) + 150, votos=dict(votos))


def _adicionar(
    pleito: gerador.PleitoSintetico,
    *,
    zona: int,
    secao: int,
    local_id: int,
    local_nome: str,
    pres: dict[int, int] | None = None,
    gov: dict[int, int] | None = None,
) -> None:
    cargos = {}
    if pres is not None:
        cargos[gerador.CARGO_PRESIDENTE] = _cargo(gerador.CARGO_PRESIDENTE, pres)
    if gov is not None:
        cargos[gerador.CARGO_GOVERNADOR] = _cargo(gerador.CARGO_GOVERNADOR, gov)
    pleito.adicionar_secao(
        zona=zona,
        secao=secao,
        local_id=local_id,
        local_nome=local_nome,
        local_endereco="SQS TESTE",
        cargos=cargos,
    )


def _homogeneas(
    pleito: gerador.PleitoSintetico,
    *,
    zona: int,
    local_id: int,
    local_nome: str,
    quantidade: int,
    primeira_secao: int = 1,
) -> None:
    for indice in range(quantidade):
        _adicionar(
            pleito,
            zona=zona,
            secao=primeira_secao + indice,
            local_id=local_id,
            local_nome=local_nome,
            pres=PRES_HOMOGEO,
            gov=GOV_HOMOGEO,
        )


def _analisar(pasta: Path, pleito: gerador.PleitoSintetico) -> dict:
    gerador.empacotar(pasta, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=pasta)
    return divergencia.conferir(
        uf="DF", turno=1, ano=2022, data_dir=pasta, config=carregar_config()
    )


def _pleito_local_homogeo_com_plantada() -> gerador.PleitoSintetico:
    """Local com 9 seções homogêneas e 1 plantada com o candidato 22 a 75%."""
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=1, local_id=101, local_nome="ESCOLA A", quantidade=9)
    _adicionar(
        pleito,
        zona=1,
        secao=999,
        local_id=101,
        local_nome="ESCOLA A",
        pres={22: 390, 13: 110, 15: 20},  # validos 520; p22 = 75% (27 pp acima)
        gov=GOV_HOMOGEO,
    )
    return pleito


def test_percentual_inflado_e_detectado(tmp_path):
    resultado = _analisar(tmp_path, _pleito_local_homogeo_com_plantada())
    achados = resultado["achados"]
    # O candidato 22 (75% × 48%) e o 13 (21,2% × 52%) divergem na seção plantada.
    assert {(a["zona"], a["secao"]) for a in achados} == {(1, 999)}
    assert {a["votavel"] for a in achados} == {22, 13}
    assert all(a["cargo_id"] == gerador.CARGO_PRESIDENTE for a in achados)
    achado = next(a for a in achados if a["votavel"] == 22)
    assert achado["percentual"] == pytest.approx(75.0)
    assert achado["mediana"] == pytest.approx(45.0)
    assert achado["mad"] == pytest.approx(0.0)
    assert achado["diferenca_pp"] == pytest.approx(30.0, abs=1e-6)
    assert math.isinf(achado["z_robusto"])  # MAD 0 com divergência → z infinito
    assert achado["z_tamanho"] == pytest.approx(13.7502, rel=1e-4)
    assert achado["q_valor"] <= 0.01
    assert achado["tipo_referencia"] == "local"
    assert achado["tamanho_grupo"] == 9  # leave-one-out: 10 seções do local menos a própria
    assert achado["votos_validos"] == 520
    assert resultado["secoes_sinalizadas"] == 1
    assert resultado["secoes_nao_analisadas"] == 0


def test_secoes_homogeneas_do_local_nao_sao_sinalizadas(tmp_path):
    resultado = _analisar(tmp_path, _pleito_local_homogeo_com_plantada())
    secoes_achadas = {(a["zona"], a["secao"]) for a in resultado["achados"]}
    assert secoes_achadas == {(1, 999)}


def test_violacao_de_um_so_criterio_nao_sinaliza(tmp_path):
    # Diferença de 7 pp (< 8 pp) com z robusto infinito e z de tamanho 8,9:
    # só a diferença mínima falha — e a sinalização exige TODOS os critérios.
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=1, local_id=101, local_nome="ESCOLA A", quantidade=9)
    _adicionar(
        pleito,
        zona=1,
        secao=999,
        local_id=101,
        local_nome="ESCOLA A",
        pres={22: 2080, 13: 1900, 15: 20},  # validos 4000; p22 = 52% (7 pp acima)
        gov=GOV_HOMOGEO,
    )
    resultado = _analisar(tmp_path, pleito)
    assert resultado["achados"] == []
    assert resultado["secoes_sinalizadas"] == 0
    assert resultado["secoes_nao_analisadas"] == 0


def test_leave_one_out_nao_inclui_a_propria_secao(tmp_path):
    # Local com 5 seções de padrão crescente (20/25/30/35/40%) e a própria a
    # 90%: a mediana do achado é a dos 5 VIZINHOS (30%), não a dos 6 valores
    # (32,5%) — prova de que a seção sai da própria referência.
    pleito = gerador.PleitoSintetico()
    for indice, v22 in enumerate((200, 250, 300, 350, 400)):
        _adicionar(
            pleito,
            zona=6,
            secao=10 + indice,
            local_id=601,
            local_nome="ESCOLA F",
            pres={22: v22, 13: 950 - v22, 15: 50},  # validos 1000; p22 = 20..40%
            gov=GOV_HOMOGEO,
        )
    _adicionar(
        pleito,
        zona=6,
        secao=999,
        local_id=601,
        local_nome="ESCOLA F",
        pres={22: 900, 13: 50, 15: 50},  # validos 1000; p22 = 90%
        gov=GOV_HOMOGEO,
    )
    resultado = _analisar(tmp_path, pleito)
    achado = next(a for a in resultado["achados"] if a["votavel"] == 22 and a["secao"] == 999)
    assert achado["mediana"] == pytest.approx(30.0)
    assert achado["mad"] == pytest.approx(5.0)
    assert achado["diferenca_pp"] == pytest.approx(60.0, abs=1e-6)
    assert achado["z_robusto"] == pytest.approx(8.0932, rel=1e-3)  # 0,60 / (1,4826 × 0,05)
    assert achado["tamanho_grupo"] == 5


def test_fallback_para_zona_quando_o_local_e_pequeno(tmp_path):
    # VAL-DIV-002: local D com 4 seções (< 6) usa a zona (32 ≥ 30) como
    # referência; o local C (28 ≥ 6) segue com referência local.
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=1, local_id=301, local_nome="ESCOLA C", quantidade=27)
    _adicionar(
        pleito,
        zona=1,
        secao=900,
        local_id=301,
        local_nome="ESCOLA C",
        pres={22: 390, 13: 110, 15: 20},
        gov=GOV_HOMOGEO,
    )
    _homogeneas(
        pleito, zona=1, local_id=302, local_nome="ESCOLA D", quantidade=3, primeira_secao=501
    )
    _adicionar(
        pleito,
        zona=1,
        secao=950,
        local_id=302,
        local_nome="ESCOLA D",
        pres={22: 390, 13: 110, 15: 20},
        gov=GOV_HOMOGEO,
    )
    resultado = _analisar(tmp_path, pleito)
    assert resultado["secoes_sinalizadas"] == 2
    por_secao = {(a["zona"], a["secao"]): a for a in resultado["achados"] if a["votavel"] == 22}
    local_c = por_secao[(1, 900)]
    assert local_c["tipo_referencia"] == "local"
    assert local_c["tamanho_grupo"] == 27  # 28 seções do local menos a própria
    assert local_c["mediana"] == pytest.approx(45.0)
    local_d = por_secao[(1, 950)]
    assert local_d["tipo_referencia"] == "zona"
    assert local_d["tamanho_grupo"] == 31  # 32 seções da zona menos a própria
    assert local_d["mediana"] == pytest.approx(45.0)


def test_flutuacao_plausivel_em_secao_pequena_nao_sinalizada(tmp_path):
    # VAL-DIV-003: 40 votos válidos, 14,5 pp acima da mediana — z de tamanho
    # ~1,8 (< 6,0) barra a sinalização; a seção é analisada, não sinalizada.
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=2, local_id=201, local_nome="ESCOLA B", quantidade=9)
    _adicionar(
        pleito,
        zona=2,
        secao=109,
        local_id=201,
        local_nome="ESCOLA B",
        pres={22: 25, 13: 15},  # validos 40; p22 = 62,5% (14,5 pp acima)
        gov=GOV_HOMOGEO,
    )
    resultado = _analisar(tmp_path, pleito)
    assert resultado["achados"] == []
    assert resultado["secoes_nao_analisadas"] == 0
    assert resultado["secoes_analisadas"] == 10


def test_secao_abaixo_do_minimo_de_votos_nao_e_analisada(tmp_path):
    # VAL-INT-006: 20 votos válidos (< 30) → "não analisada" com motivo,
    # nunca achado; a contagem de seções fecha.
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=2, local_id=201, local_nome="ESCOLA B", quantidade=9)
    _adicionar(
        pleito,
        zona=2,
        secao=109,
        local_id=201,
        local_nome="ESCOLA B",
        pres={22: 12, 13: 8},  # validos 20
        gov={15: 15, 43: 5},  # validos 20
    )
    resultado = _analisar(tmp_path, pleito)
    assert resultado["achados"] == []
    assert resultado["secoes_total"] == 10
    assert resultado["secoes_analisadas"] == 9
    assert resultado["secoes_nao_analisadas"] == 1
    registros = [r for r in resultado["nao_analisadas"] if r["zona"] == 2 and r["secao"] == 109]
    assert len(registros) == 2  # um por cargo
    assert {r["cargo"] for r in registros} == {"Presidente", "Governador"}
    assert all(r["motivo"] == "votos_validos_abaixo_do_minimo" for r in registros)
    assert resultado["nao_analisadas_por_motivo"] == {
        "votos_validos_abaixo_do_minimo": 2,
        "zona_sem_secoes_suficientes": 0,
    }


def test_zona_sem_secoes_suficientes_nao_analisa(tmp_path):
    # VAL-DIV-002/VAL-INT-006: local com 5 seções (< 6) cai para a zona, que
    # também tem menos de 30 → nada é analisado e o motivo fica registrado.
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=3, local_id=401, local_nome="ESCOLA E", quantidade=5)
    resultado = _analisar(tmp_path, pleito)
    assert resultado["achados"] == []
    assert resultado["secoes_sinalizadas"] == 0
    assert resultado["secoes_total"] == 5
    assert resultado["secoes_analisadas"] == 0
    assert resultado["secoes_nao_analisadas"] == 5
    assert len(resultado["nao_analisadas"]) == 10  # 5 seções × 2 cargos
    assert all(r["motivo"] == "zona_sem_secoes_suficientes" for r in resultado["nao_analisadas"])
    assert resultado["testes"] == 0


def test_contagens_de_secoes_fecham_no_total(tmp_path):
    # Analisadas + não analisadas = total de seções (todos analisáveis aqui).
    resultado = _analisar(tmp_path, _pleito_local_homogeo_com_plantada())
    assert resultado["secoes_total"] == 10
    assert (
        resultado["secoes_analisadas"] + resultado["secoes_nao_analisadas"]
        == resultado["secoes_total"]
    )
    assert resultado["testes"] == 50  # 10 seções × (3 candidatos pres. + 2 gov.)


def test_limiares_do_config_controlam_a_sinalizacao(tmp_path):
    pleito = _pleito_local_homogeo_com_plantada()
    gerador.empacotar(tmp_path, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=tmp_path)

    config = carregar_config()
    config["limiares"]["divergencia"]["z_tamanho_min"] = 1000.0
    assert (
        divergencia.conferir(uf="DF", turno=1, ano=2022, data_dir=tmp_path, config=config)[
            "achados"
        ]
        == []
    )

    config["limiares"]["divergencia"]["dif_minima_pp"] = 100.0
    assert (
        divergencia.conferir(uf="DF", turno=1, ano=2022, data_dir=tmp_path, config=config)[
            "achados"
        ]
        == []
    )


def test_cli_roda_a_etapa_divergencia_sobre_a_fixture(tmp_path):
    gerador.empacotar(tmp_path, gerador.pleito_padrao())
    processo = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
            "--uf",
            "DF",
            "--ano",
            "2022",
            "--turno",
            "1",
            "--data-dir",
            str(tmp_path),
            "--etapas",
            "ingest,divergencia",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0, processo.stdout + processo.stderr
    assert "[divergencia]" in processo.stdout
    # pleito_padrao: 4 seções em zonas com menos de 30 seções → não analisadas
    assert "4 não analisadas" in processo.stdout
    assert "achados: 0" in processo.stdout


def test_emissao_registra_as_contagens_do_modulo_2(tmp_path):
    gerador.empacotar(tmp_path, gerador.pleito_padrao())
    processo = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
            "--uf",
            "DF",
            "--ano",
            "2022",
            "--turno",
            "1",
            "--data-dir",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / "public" / "data"),
            "--etapas",
            "ingest,totais,integridade,divergencia,emitir",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0, processo.stdout + processo.stderr
    caminho_index = tmp_path / "public" / "data" / "index.json"
    assert caminho_index.exists(), processo.stdout + processo.stderr
    indice = json.loads(caminho_index.read_text("utf-8"))
    rubrica = indice["contagens"]["divergencia"]
    assert rubrica["secoes_total"] == 4
    assert rubrica["secoes_analisadas"] == 0
    assert rubrica["secoes_nao_analisadas"] == 4
    assert rubrica["achados"] == 0
    assert rubrica["secoes_sinalizadas"] == 0
    assert rubrica["percentual_sinalizado"] == 0.0
