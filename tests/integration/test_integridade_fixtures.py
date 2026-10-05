"""Integração do módulo 1 com fixtures sintéticas e injeção de anomalias.

O mini-pleito é empacotado como zips + manifesto (formato do download real),
ingido pelo caminho REAL de pipeline/ingest.py (Latin-1, ';', aspas, colunas
exatas) e analisado pelo módulo 1 de verdade (R1, R2, R3). Cada anomalia
injetada deve ser detectada exatamente — seção, regra, valor observado e
esperado — e a cópia íntegra NÃO pode gerar achado (sem falsos positivos).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pipeline import RAIZ, ingest, integridade, totais
from pipeline.config import carregar_config
from tests.fixtures import gerador


def _analisar(pasta: Path, pleito: gerador.PleitoSintetico) -> dict:
    gerador.empacotar(pasta, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=pasta)
    return integridade.conferir(
        uf="DF", turno=1, ano=2022, data_dir=pasta, config=carregar_config()
    )


def test_pleito_integro_nao_gera_achado(tmp_path):
    resultado = _analisar(tmp_path, gerador.pleito_padrao())
    assert resultado["achados"] == []
    assert resultado["secoes_sinalizadas"] == 0
    assert resultado["excecoes_aplicadas"] == []
    assert resultado["contagens"] == {"R1": 0, "R2": 0, "R3": 0}
    assert resultado["secoes_analisadas"] == 4  # seções 12 e 2 (z1), 30 (z1), 40 (z2)


def test_pleito_integro_passa_na_conferencia_de_totais(tmp_path):
    gerador.empacotar(tmp_path, gerador.pleito_padrao())
    ingest.ingerir(uf="DF", turno=1, data_dir=tmp_path)
    relatorio = totais.conferir(uf="DF", turno=1, data_dir=tmp_path)
    assert relatorio["consistente"]
    assert int(relatorio["diferenca"]) == 0
    totais.validar(relatorio)  # não pode levantar erro


def test_cli_roda_de_ponta_a_ponta_sobre_a_fixture_integra(tmp_path):
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
            "ingest,totais,integridade",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0, processo.stdout + processo.stderr
    assert "R1 0 | R2 0 | R3 0" in processo.stdout
    assert "Diferença total: 0" in processo.stdout


def test_r1_violada_e_detectada_com_os_valores_exatos(tmp_path):
    # VAL-INT-001: soma dos votos 250 contra comparecimento 248 (diferença 2)
    pleito = gerador.pleito_padrao()
    gerador.injetar_r1(
        pleito, zona=1, secao=2, cargo_id=gerador.CARGO_PRESIDENTE, comparecimento=248
    )
    resultado = _analisar(tmp_path, pleito)
    achados_r1 = [a for a in resultado["achados"] if a["regra"] == "R1"]
    assert len(resultado["achados"]) == 1
    assert achados_r1 == [
        {
            "regra": "R1",
            "cargo_id": gerador.CARGO_PRESIDENTE,
            "cargo": "Presidente",
            "zona": 1,
            "secao": 2,
            "valor_observado": 250,
            "valor_esperado": 248,
            "excecao_aplicada": None,
        }
    ]
    assert resultado["secoes_sinalizadas"] == 1


def test_r1_violada_na_o_falso_positivo_nas_outras_secoes(tmp_path):
    pleito = gerador.pleito_padrao()
    gerador.injetar_r1(
        pleito, zona=1, secao=2, cargo_id=gerador.CARGO_PRESIDENTE, comparecimento=248
    )
    resultado = _analisar(tmp_path, pleito)
    secoes_achadas = {(a["zona"], a["secao"]) for a in resultado["achados"]}
    assert secoes_achadas == {(1, 2)}  # só a seção violada; as demais ficam limpas


def test_r2_violada_e_detectada_com_os_valores_exatos(tmp_path):
    # VAL-INT-002: 300 aptos e comparecimento 310, pleito do próprio cargo
    pleito = gerador.pleito_padrao()
    gerador.injetar_r2(
        pleito, zona=1, secao=30, cargo_id=gerador.CARGO_GOVERNADOR, comparecimento=310
    )
    resultado = _analisar(tmp_path, pleito)
    assert len(resultado["achados"]) == 1
    achado = resultado["achados"][0]
    assert achado["regra"] == "R2"
    assert achado["cargo"] == "Governador"
    assert achado["zona"] == 1 and achado["secao"] == 30
    assert achado["valor_observado"] == 310
    assert achado["valor_esperado"] == 300
    assert resultado["contagens"] == {"R1": 0, "R2": 1, "R3": 0}


def test_r2_nao_confunde_o_pleito_entre_cargos(tmp_path):
    # R2 usa os aptos do GOVERNADOR (300); os do PRESIDENTE (280) não podem
    # gerar achado para a mesma seção — pleito sempre do próprio cargo.
    pleito = gerador.pleito_padrao()
    gerador.injetar_r2(
        pleito, zona=1, secao=30, cargo_id=gerador.CARGO_GOVERNADOR, comparecimento=310
    )
    resultado = _analisar(tmp_path, pleito)
    cargos_achados = {a["cargo"] for a in resultado["achados"]}
    assert cargos_achados == {"Governador"}


def test_r3_divergente_e_detectada_por_votavel_com_os_valores(tmp_path):
    # VAL-INT-003: candidato 22 com 120 votos no BU e 118 na totalização
    pleito = gerador.pleito_padrao()
    gerador.injetar_r3(
        pleito,
        zona=2,
        secao=40,
        cargo_id=gerador.CARGO_PRESIDENTE,
        votavel=22,
        votos_totalizacao=118,
    )
    resultado = _analisar(tmp_path, pleito)
    assert len(resultado["achados"]) == 1
    achado = resultado["achados"][0]
    assert achado["regra"] == "R3"
    assert achado["cargo_id"] == gerador.CARGO_PRESIDENTE
    assert achado["zona"] == 2 and achado["secao"] == 40
    assert achado["votavel"] == 22
    assert achado["valor_observado"] == 120  # lado do BU
    assert achado["valor_esperado"] == 118  # lado da totalização
    assert resultado["contagens"] == {"R1": 0, "R2": 0, "R3": 1}


def test_r3_divergente_em_um_votavel_na_o_marca_os_outras(tmp_path):
    # Os outros votáveis da mesma seção e cargo não podem gerar achado.
    pleito = gerador.pleito_padrao()
    gerador.injetar_r3(
        pleito,
        zona=2,
        secao=40,
        cargo_id=gerador.CARGO_PRESIDENTE,
        votavel=22,
        votos_totalizacao=118,
    )
    resultado = _analisar(tmp_path, pleito)
    votaveis_achados = {a["votavel"] for a in resultado["achados"]}
    assert votaveis_achados == {22}
    assert resultado["secoes_sinalizadas"] == 1
