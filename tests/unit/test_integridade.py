"""Testes do módulo 1 (integridade): regras R1, R2 e R3 e exceções legítimas.

O módulo trabalha sobre as tabelas tipadas do banco da ingestão (bu,
secao_total, pleito, locais). Os testes montam um banco sintético em memória
com o MESMO esquema tipado de pipeline/ingest.py e exercitam cada regra e cada
exceção legítima (multiplicador de votos por eleitor, voto em trânsito,
exterior e seção agregada) — casos legítimos NÃO podem virar achado.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from pipeline import integridade
from pipeline.config import carregar_config

# ---------------------------------------------------------------------------
# Construção do banco sintético com o esquema tipado de pipeline/ingest.py
# ---------------------------------------------------------------------------

COLUNAS_BU = (
    "ano_eleicao",
    "turno",
    "uf",
    "municipio_id",
    "municipio",
    "zona",
    "secao",
    "local_id",
    "eleicao_id",
    "cargo_id",
    "cargo",
    "tipo_votavel_id",
    "tipo_votavel",
    "votavel",
    "votavel_nome",
    "votos",
    "aptos",
    "comparecimento",
    "abstencoes",
    "urna",
    "agregadas",
)
COLUNAS_TOT = (
    "turno",
    "uf",
    "municipio_id",
    "municipio",
    "zona",
    "secao",
    "local_id",
    "cargo_id",
    "cargo",
    "votavel",
    "votavel_nome",
    "candidato_sq",
    "votos",
    "local_votacao",
    "endereco_local",
)
# Tipos exatos das tabelas criadas em pipeline/ingest.py (numéricos BIGINT).
TIPOS_BU = dict(
    zip(
        COLUNAS_BU,
        (
            "BIGINT",
            "BIGINT",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "VARCHAR",
            "BIGINT",
            "VARCHAR",
            "BIGINT",
            "VARCHAR",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "VARCHAR",
        ),
        strict=True,
    )
)
TIPOS_TOT = dict(
    zip(
        COLUNAS_TOT,
        (
            "BIGINT",
            "VARCHAR",
            "VARCHAR",
            "VARCHAR",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "BIGINT",
            "VARCHAR",
            "BIGINT",
            "VARCHAR",
            "BIGINT",
            "BIGINT",
            "VARCHAR",
            "VARCHAR",
        ),
        strict=True,
    )
)

CARGOS_POR_ID = {1: "Presidente", 3: "Governador", 5: "Senador"}
NOMES = {
    22: "JAIR BOLSONARO",
    13: "LULA",
    15: "IBANEIS ROCHA",
    43: "LEANDRO GRASS",
    95: "Branco",
    96: "Nulo",
    111: "CAND A",
    222: "CAND B",
}
TIPO_POR_VOTAVEL = {95: (2, "Branco"), 96: (3, "Nulo")}


def _linha_bu(
    zona: int,
    secao: int,
    cargo_id: int,
    votavel: int,
    votos: int,
    aptos: int,
    comparecimento: int,
    *,
    uf: str = "DF",
    urna: int | None = None,
    agregadas: str | None = None,
    nome: str | None = None,
) -> dict[str, Any]:
    tipo, tipo_nome = TIPO_POR_VOTAVEL.get(votavel, (1, "Nominal"))
    return {
        "ano_eleicao": 2022,
        "turno": 1,
        "uf": uf,
        "municipio_id": "97012",
        "municipio": "BRASÍLIA",
        "zona": zona,
        "secao": secao,
        "local_id": 1015,
        "eleicao_id": 544,
        "cargo_id": cargo_id,
        "cargo": CARGOS_POR_ID[cargo_id],
        "tipo_votavel_id": tipo,
        "tipo_votavel": tipo_nome,
        "votavel": votavel,
        "votavel_nome": nome or NOMES.get(votavel, f"VOTAVEL {votavel}"),
        "votos": votos,
        "aptos": aptos,
        "comparecimento": comparecimento,
        "abstencoes": aptos - comparecimento,
        "urna": urna if urna is not None else 2220000 + secao,
        "agregadas": agregadas,
    }


def _linha_tot(
    zona: int,
    secao: int,
    cargo_id: int,
    votavel: int,
    votos: int,
    *,
    uf: str = "DF",
    nome: str | None = None,
) -> dict[str, Any]:
    return {
        "turno": 1,
        "uf": uf,
        "municipio_id": "97012",
        "municipio": "BRASÍLIA",
        "zona": zona,
        "secao": secao,
        "local_id": 1015,
        "cargo_id": cargo_id,
        "cargo": CARGOS_POR_ID[cargo_id],
        "votavel": votavel,
        "votavel_nome": nome or NOMES.get(votavel, f"VOTAVEL {votavel}"),
        "candidato_sq": votavel if votavel < 90 else -1,
        "votos": votos,
        "local_votacao": "ESCOLA CLASSE 01",
        "endereco_local": "SQS 103 BLOCO A",
    }


def _inserir(con: duckdb.DuckDBPyConnection, tabela: str, colunas: tuple, linhas: list) -> None:
    if not linhas:
        return
    nomes = ", ".join(colunas)
    marcadores = ", ".join("?" for _ in colunas)
    con.executemany(
        f"INSERT INTO {tabela} ({nomes}) VALUES ({marcadores})",
        [tuple(linha[c] for c in colunas) for linha in linhas],
    )


def _criar_banco(linhas_bu: list[dict], linhas_tot: list[dict]) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE bu (" + ", ".join(f"{c} {TIPOS_BU[c]}" for c in COLUNAS_BU) + ")")
    con.execute(
        "CREATE TABLE secao_total (" + ", ".join(f"{c} {TIPOS_TOT[c]}" for c in COLUNAS_TOT) + ")"
    )
    _inserir(con, "bu", COLUNAS_BU, linhas_bu)
    _inserir(con, "secao_total", COLUNAS_TOT, linhas_tot)
    # pleito deduplicado por (seção, urna, cargo), como em pipeline/ingest.py
    con.execute(
        "CREATE TABLE pleito AS SELECT DISTINCT uf, municipio_id, municipio, "
        "zona, secao, urna, cargo_id, cargo, aptos, comparecimento, abstencoes "
        "FROM bu"
    )
    con.execute(
        "CREATE TABLE locais AS SELECT DISTINCT uf, municipio_id, municipio, "
        "zona, secao, local_id FROM bu"
    )
    return con


# Cenário base: duas seções, dois cargos com pleitos DIFERENTES por cargo
# (pegadinha real: aptos/comparecimento variam por cargo).
BASE_PRES_Z12 = [  # soma 230 = comparecimento 230; aptos 267
    _linha_bu(1, 12, 1, 22, 120, 267, 230, urna=2221001),
    _linha_bu(1, 12, 1, 13, 80, 267, 230, urna=2221001),
    _linha_bu(1, 12, 1, 95, 18, 267, 230, urna=2221001),
    _linha_bu(1, 12, 1, 96, 12, 267, 230, urna=2221001),
]
BASE_GOV_Z12 = [  # soma 209 = comparecimento 209; aptos 259
    _linha_bu(1, 12, 3, 15, 130, 259, 209, urna=2221001),
    _linha_bu(1, 12, 3, 43, 60, 259, 209, urna=2221001),
    _linha_bu(1, 12, 3, 95, 10, 259, 209, urna=2221001),
    _linha_bu(1, 12, 3, 96, 9, 259, 209, urna=2221001),
]
# comparecimento do Presidente (270) excederia os aptos do Governador (260):
# pleito do próprio cargo evita falso positivo de R2.
BASE_PRES_Z1S2 = [  # soma 270 = comparecimento 270; aptos 310
    _linha_bu(1, 2, 1, 22, 150, 310, 270, urna=2221002),
    _linha_bu(1, 2, 1, 13, 90, 310, 270, urna=2221002),
    _linha_bu(1, 2, 1, 96, 30, 310, 270, urna=2221002),
]
BASE_GOV_Z1S2 = [  # soma 200 = comparecimento 200; aptos 260
    _linha_bu(1, 2, 3, 15, 150, 260, 200, urna=2221002),
    _linha_bu(1, 2, 3, 43, 50, 260, 200, urna=2221002),
]

BASE_LINHAS_BU = [*BASE_PRES_Z12, *BASE_GOV_Z12, *BASE_PRES_Z1S2, *BASE_GOV_Z1S2]

BASE_TOT = [
    _linha_tot(1, 12, 1, 22, 120),
    _linha_tot(1, 12, 1, 13, 80),
    _linha_tot(1, 12, 1, 95, 18),
    _linha_tot(1, 12, 1, 96, 12),
    _linha_tot(1, 12, 3, 15, 130),
    _linha_tot(1, 12, 3, 43, 60),
    _linha_tot(1, 12, 3, 95, 10),
    _linha_tot(1, 12, 3, 96, 9),
    _linha_tot(1, 2, 1, 22, 150),
    _linha_tot(1, 2, 1, 13, 90),
    _linha_tot(1, 2, 1, 96, 30),
    _linha_tot(1, 2, 3, 15, 150),
    _linha_tot(1, 2, 3, 43, 50),
]


def _config(multiplicador_senador: int | None = None) -> dict[str, Any]:
    # Config real do projeto (única fonte de verdade); a fixture só sobrescreve
    # o multiplicador do senador quando o cenário pede.
    dados: dict[str, Any] = carregar_config()
    if multiplicador_senador is not None:
        dados["multiplicador_votos_por_eleitor"]["2022"]["senador"] = multiplicador_senador
    return dados


def _analisar(con: duckdb.DuckDBPyConnection, config: dict | None = None) -> dict:
    resultado = integridade.analisar(con, uf="DF", turno=1, ano=2022, config=config or _config())
    con.close()
    return resultado


def _tot_com_divergencia(
    *mudancas: tuple[int, int, int],
) -> list[dict[str, Any]]:
    """Cópia de BASE_TOT com votos alterados em (votável, seção) específicos."""
    linhas = [dict(linha) for linha in BASE_TOT]
    for votavel, secao, votos in mudancas:
        for linha in linhas:
            if linha["votavel"] == votavel and linha["secao"] == secao:
                linha["votos"] = votos
    return linhas


def _tot_gov_z12() -> list[dict[str, Any]]:
    return [
        _linha_tot(1, 12, 3, 15, 130),
        _linha_tot(1, 12, 3, 43, 60),
        _linha_tot(1, 12, 3, 95, 10),
        _linha_tot(1, 12, 3, 96, 9),
    ]


# ---------------------------------------------------------------------------
# Regras R1 e R2 (por seção × cargo, com o pleito do próprio cargo)
# ---------------------------------------------------------------------------


def test_cenario_base_integro_nao_gera_achado():
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, BASE_TOT))
    assert resultado["achados"] == []
    assert resultado["secoes_sinalizadas"] == 0
    assert resultado["excecoes_aplicadas"] == []
    # pleitos por cargo distintos preservados: 2 seções, 2 cargos
    assert resultado["secoes_analisadas"] == 2


def test_r1_violacao_de_soma_e_detectada_com_valores():
    # seção 2 do Presidente: votos somam 250 contra comparecimento 248
    violada = [
        _linha_bu(1, 2, 1, 22, 150, 260, 248, urna=2221002),
        _linha_bu(1, 2, 1, 13, 60, 260, 248, urna=2221002),
        _linha_bu(1, 2, 1, 95, 20, 260, 248, urna=2221002),
        _linha_bu(1, 2, 1, 96, 20, 260, 248, urna=2221002),
    ]
    tot = [
        *_tot_com_divergencia((22, 2, 150), (13, 2, 60), (96, 2, 20)),
        _linha_tot(1, 2, 1, 95, 20),
    ]
    linhas_bu = [*BASE_PRES_Z12, *BASE_GOV_Z12, *violada, *BASE_GOV_Z1S2]
    resultado = _analisar(_criar_banco(linhas_bu, tot))
    achados_r1 = [a for a in resultado["achados"] if a["regra"] == "R1"]
    assert len(achados_r1) == 1
    achado = achados_r1[0]
    assert achado["valor_observado"] == 250
    assert achado["valor_esperado"] == 248
    assert achado["cargo"] == "Presidente"
    assert achado["secao"] == 2
    assert achado["zona"] == 1
    # a seção íntegra do mesmo cargo não gera achado
    assert resultado["secoes_sinalizadas"] == 1


def test_r1_cenario_integro_nao_gera_achado_de_r1():
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, BASE_TOT))
    assert [a for a in resultado["achados"] if a["regra"] == "R1"] == []


def test_r2_comparecimento_acima_dos_aptos_e_detectado():
    # seção 12 do Governador: comparecimento 310 acima de 300 aptos, votos 310
    violada = [
        _linha_bu(1, 12, 3, 15, 200, 300, 310, urna=2221001),
        _linha_bu(1, 12, 3, 43, 80, 300, 310, urna=2221001),
        _linha_bu(1, 12, 3, 96, 30, 300, 310, urna=2221001),
    ]
    tot = [
        *_tot_pres_z12_para_teste(),
        *_tot_com_divergencia((15, 12, 200), (43, 12, 80), (96, 12, 30)),
        *_tot_pres_z1s2_para_teste(),
        *_tot_gov_z1s2_para_teste(),
    ]
    linhas_bu = [*BASE_PRES_Z12, *violada, *BASE_PRES_Z1S2, *BASE_GOV_Z1S2]
    resultado = _analisar(_criar_banco(linhas_bu, tot))
    achados_r2 = [a for a in resultado["achados"] if a["regra"] == "R2"]
    assert len(achados_r2) == 1
    achado = achados_r2[0]
    assert achado["valor_observado"] == 310
    assert achado["valor_esperado"] == 300  # aptos do PRÓPRIO cargo
    assert achado["cargo"] == "Governador"
    assert achado["secao"] == 12


def _tot_pres_z12_para_teste() -> list[dict[str, Any]]:
    return [
        _linha_tot(1, 12, 1, 22, 120),
        _linha_tot(1, 12, 1, 13, 80),
        _linha_tot(1, 12, 1, 95, 18),
        _linha_tot(1, 12, 1, 96, 12),
    ]


def _tot_pres_z1s2_para_teste() -> list[dict[str, Any]]:
    return [
        _linha_tot(1, 2, 1, 22, 150),
        _linha_tot(1, 2, 1, 13, 90),
        _linha_tot(1, 2, 1, 96, 30),
    ]


def _tot_gov_z1s2_para_teste() -> list[dict[str, Any]]:
    return [
        _linha_tot(1, 2, 3, 15, 150),
        _linha_tot(1, 2, 3, 43, 50),
    ]


def test_pleito_de_outro_cargo_nao_contamina_r2():
    # Presidente tem comparecimento 270 > aptos do Governador (260): com pleito
    # por cargo correto, NADA é sinalizado.
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, BASE_TOT))
    assert [a for a in resultado["achados"] if a["regra"] == "R2"] == []


# ---------------------------------------------------------------------------
# Regra R3 (BU × totalização por seção e votável)
# ---------------------------------------------------------------------------


def test_r3_divergencia_por_votavel_e_detectada():
    tot = _tot_com_divergencia((22, 12, 118))
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, tot))
    achados_r3 = [a for a in resultado["achados"] if a["regra"] == "R3"]
    assert len(achados_r3) == 1
    achado = achados_r3[0]
    assert achado["votavel"] == 22
    assert achado["valor_observado"] == 120  # lado do BU
    assert achado["valor_esperado"] == 118  # lado da totalização
    assert achado["cargo"] == "Presidente"
    assert achado["secao"] == 12
    assert achado["zona"] == 1
    # candidatos não divergentes da mesma seção não geram achado
    assert resultado["secoes_sinalizadas"] == 1


def test_r3_votavel_ausente_de_um_lado_gera_achado_com_zero():
    tot = [linha for linha in BASE_TOT if not (linha["votavel"] == 13 and linha["secao"] == 12)]
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, tot))
    achados_r3 = [a for a in resultado["achados"] if a["regra"] == "R3"]
    assert len(achados_r3) == 1
    assert achados_r3[0]["votavel"] == 13
    assert achados_r3[0]["valor_observado"] == 80
    assert achados_r3[0]["valor_esperado"] == 0


# ---------------------------------------------------------------------------
# Exceções legítimas, aplicadas por regra e SEMPRE registradas
# ---------------------------------------------------------------------------


def test_excecao_multiplicador_senador_legitimo_nao_sinaliza_e_registra():
    config = _config(multiplicador_senador=2)
    # senador: soma = 2 × comparecimento (2 votos por eleitor)
    senador = [
        _linha_bu(1, 30, 5, 111, 120, 300, 100, urna=2221003),
        _linha_bu(1, 30, 5, 222, 60, 300, 100, urna=2221003),
        _linha_bu(1, 30, 5, 96, 20, 300, 100, urna=2221003),
    ]
    tot = [
        *_tot_com_divergencia((111, 30, 120), (222, 30, 60), (96, 30, 20)),
        _linha_tot(1, 30, 5, 111, 120),
        _linha_tot(1, 30, 5, 222, 60),
        _linha_tot(1, 30, 5, 96, 20),
    ]
    linhas_bu = [*BASE_PRES_Z12, *BASE_GOV_Z12, *senador]
    linhas_tot = [
        *_tot_pres_z12_para_teste(),
        *_tot_gov_z12(),
        *_tot_pres_z1s2_para_teste(),
        *_tot_gov_z1s2_para_teste(),
        *tot,
    ]
    resultado = _analisar(_criar_banco(linhas_bu, linhas_tot), config)
    assert [a for a in resultado["achados"] if a["regra"] == "R1"] == []
    registros = [
        e
        for e in resultado["excecoes_aplicadas"]
        if e["excecao"] == "multiplicador_votos_por_eleitor"
    ]
    assert len(registros) == 1
    assert registros[0]["regra"] == "R1"
    assert registros[0]["cargo"] == "Senador"
    assert registros[0]["multiplicador"] == 2
    assert registros[0]["secao"] == 30
    assert registros[0]["zona"] == 1


def test_excecao_multiplicador_aparece_no_achado_quando_a_regra_e_violada():
    config = _config(multiplicador_senador=2)
    # soma = comparecimento (1×) — com multiplicador 2, o esperado é 2×
    senador = [
        _linha_bu(1, 30, 5, 111, 60, 300, 100, urna=2221003),
        _linha_bu(1, 30, 5, 96, 40, 300, 100, urna=2221003),
    ]
    linhas_tot = [
        *_tot_pres_z12_para_teste(),
        *_tot_gov_z12(),
        *_tot_pres_z1s2_para_teste(),
        *_tot_gov_z1s2_para_teste(),
        _linha_tot(1, 30, 5, 111, 60),
        _linha_tot(1, 30, 5, 96, 40),
    ]
    resultado = _analisar(
        _criar_banco([*BASE_PRES_Z12, *BASE_GOV_Z12, *senador], linhas_tot), config
    )
    achados_r1 = [a for a in resultado["achados"] if a["regra"] == "R1"]
    assert len(achados_r1) == 1
    achado = achados_r1[0]
    assert achado["valor_observado"] == 100
    assert achado["valor_esperado"] == 200  # multiplicador × comparecimento
    assert achado["excecao_aplicada"]["excecao"] == "multiplicador_votos_por_eleitor"
    assert achado["excecao_aplicada"]["multiplicador"] == 2


def test_sem_config_o_multiplicador_de_senador_e_1():
    # soma = 2 × comparecimento SEM multiplicador no config → achado R1
    senador = [
        _linha_bu(1, 30, 5, 111, 60, 300, 100, urna=2221003),
        _linha_bu(1, 30, 5, 222, 60, 300, 100, urna=2221003),
        _linha_bu(1, 30, 5, 96, 40, 300, 100, urna=2221003),
    ]
    linhas_tot = [
        *_tot_pres_z12_para_teste(),
        *_tot_gov_z12(),
        *_tot_pres_z1s2_para_teste(),
        *_tot_gov_z1s2_para_teste(),
        _linha_tot(1, 30, 5, 111, 60),
        _linha_tot(1, 30, 5, 222, 60),
        _linha_tot(1, 30, 5, 96, 40),
    ]
    resultado = _analisar(_criar_banco([*BASE_PRES_Z12, *BASE_GOV_Z12, *senador], linhas_tot))
    achados_r1 = [a for a in resultado["achados"] if a["regra"] == "R1"]
    assert len(achados_r1) == 1
    assert achados_r1[0]["valor_esperado"] == 100


def test_excecao_voto_em_transito_e_exterior_excluidas_de_r3_e_registradas():
    # seções VT e ZZ com divergência real entre BU e totalização: excluídas de
    # R3 (comparecimento do próprio cargo consistente, para R1/R2 não tocarem)
    vt_bu = [_linha_bu(2, 40, 1, 22, 50, 100, 50, urna=2221004, uf="VT")]
    zz_bu = [_linha_bu(2, 41, 1, 22, 70, 100, 70, urna=2221005, uf="ZZ")]
    vt_tot = [_linha_tot(2, 40, 1, 22, 60, uf="VT")]
    zz_tot = [_linha_tot(2, 41, 1, 22, 70, uf="ZZ")]
    linhas_bu = [*BASE_LINHAS_BU, *vt_bu, *zz_bu]
    linhas_tot = [*BASE_TOT, *vt_tot, *zz_tot]
    resultado = _analisar(_criar_banco(linhas_bu, linhas_tot))
    achados_r3 = [a for a in resultado["achados"] if a["regra"] == "R3"]
    assert achados_r3 == []
    por_excecao = {e["excecao"]: e for e in resultado["excecoes_aplicadas"]}
    assert "voto_em_transito" in por_excecao
    assert por_excecao["voto_em_transito"]["regra"] == "R3"
    assert por_excecao["voto_em_transito"]["secao"] == 40
    assert "exterior" in por_excecao
    assert por_excecao["exterior"]["secao"] == 41
    # R1/R2 continuam valendo para as seções VT/ZZ (comparecimento consistente)
    assert resultado["secoes_sinalizadas"] == 0


def test_excecao_secao_agregada_comparada_como_unidade_e_registrada():
    # urna 900 agregada cobre as seções 100 e 101; a totalização reporta a
    # seção 100 (a agregada) com os votos da urna inteira — unidade única.
    agregada = [
        _linha_bu(3, 100, 1, 22, 180, 400, 350, urna=900, agregadas="101"),
        _linha_bu(3, 100, 1, 13, 120, 400, 350, urna=900, agregadas="101"),
        _linha_bu(3, 100, 1, 96, 50, 400, 350, urna=900, agregadas="101"),
    ]
    tot = [
        *_tot_pres_z12_para_teste(),
        *_tot_gov_z12(),
        *_tot_pres_z1s2_para_teste(),
        *_tot_gov_z1s2_para_teste(),
        _linha_tot(3, 100, 1, 22, 180),
        _linha_tot(3, 100, 1, 13, 120),
        _linha_tot(3, 100, 1, 96, 50),
    ]
    linhas_bu = [*BASE_LINHAS_BU, *agregada]
    resultado = _analisar(_criar_banco(linhas_bu, tot))
    assert resultado["achados"] == []
    registros = [e for e in resultado["excecoes_aplicadas"] if e["excecao"] == "secao_agregada"]
    assert len(registros) == 1
    assert registros[0]["regra"] == "R3"
    assert registros[0]["secao"] == 100
    assert registros[0]["zona"] == 3
    assert "101" in registros[0]["detalhe"]
    # a seção componente (101) não aparece como seção própria
    assert all(a["secao"] != 101 for a in resultado["achados"])


def test_contagens_do_resultado_sao_consistentes():
    tot = _tot_com_divergencia((22, 12, 118))
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, tot))
    assert resultado["contagens"]["R1"] == 0
    assert resultado["contagens"]["R2"] == 0
    assert resultado["contagens"]["R3"] == 1
    assert resultado["secoes_sinalizadas"] == 1
    assert len(resultado["achados"]) == 1


def test_relatar_imprime_contagens_por_regra(capsys):
    tot = _tot_com_divergencia((22, 12, 118))
    resultado = _analisar(_criar_banco(BASE_LINHAS_BU, tot))
    integridade.relatar(resultado)
    saida = capsys.readouterr().out
    assert "R1" in saida and "R2" in saida and "R3" in saida
    assert "1" in saida  # seções sinalizadas


def test_analisar_sem_banco_falha_de_forma_orientada(tmp_path):
    with pytest.raises(integridade.IntegridadeError) as excinfo:
        integridade.conferir(uf="DF", turno=1, ano=2022, data_dir=tmp_path, config=_config())
    assert "ingest" in str(excinfo.value)
