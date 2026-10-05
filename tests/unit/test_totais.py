"""Testes da conferência de totais por candidato (BU × totalização).

A agregação é exercitada como função pura (linhas sintéticas) e sobre um
banco DuckDB em memória com as mesmas colunas usadas pelo SQL do módulo —
nenhum arquivo real (grande) entra na suíte.
"""

import duckdb
import pytest

from pipeline.erros import TotaisError
from pipeline.totais import agregar_votaveis, conferir, construir_relatorio, relatar, validar

# --- fixtures sintéticas (linhas no formato que o SQL do módulo devolve) ----


def _linha_bu(cargo_id: int, cargo: str, votavel: int, nome: str, tipo: str, votos: int) -> dict:
    return {
        "cargo_id": cargo_id,
        "cargo": cargo,
        "votavel": votavel,
        "nome": nome,
        "tipo": tipo,
        "votos": votos,
    }


def _linha_tot(cargo_id: int, cargo: str, votavel: int, nome: str, votos: int) -> dict:
    return {"cargo_id": cargo_id, "cargo": cargo, "votavel": votavel, "nome": nome, "votos": votos}


PRESIDENTE, GOVERNADOR, SENADOR = 1, 3, 5


def _linhas_bu_sinteticas() -> list[dict]:
    linhas = [
        _linha_bu(PRESIDENTE, "Presidente", 22, "JAIR BOLSONARO", "Nominal", 150),
        _linha_bu(PRESIDENTE, "Presidente", 22, "JAIR BOLSONARO", "Nominal", 150),
        _linha_bu(PRESIDENTE, "Presidente", 13, "LULA", "Nominal", 200),
        _linha_bu(PRESIDENTE, "Presidente", 95, "Branco", "Branco", 50),
        _linha_bu(PRESIDENTE, "Presidente", 96, "Nulo", "Nulo", 30),
        _linha_bu(GOVERNADOR, "Governador", 15, "IBANEIS ROCHA", "Nominal", 220),
        _linha_bu(GOVERNADOR, "Governador", 43, "LEANDRO GRASS", "Nominal", 120),
        _linha_bu(GOVERNADOR, "Governador", 95, "Branco", "Branco", 20),
        _linha_bu(GOVERNADOR, "Governador", 96, "Nulo", "Nulo", 10),
        _linha_bu(SENADOR, "Senador", 555, "SENADOR SINTÉTICO", "Nominal", 999),
    ]
    return linhas


def _linhas_tot_sinteticas() -> list[dict]:
    linhas = [
        _linha_tot(PRESIDENTE, "PRESIDENTE", 22, "JAIR BOLSONARO", 300),
        _linha_tot(PRESIDENTE, "PRESIDENTE", 13, "LULA", 200),
        _linha_tot(PRESIDENTE, "PRESIDENTE", 95, "Branco", 50),
        _linha_tot(PRESIDENTE, "PRESIDENTE", 96, "Nulo", 30),
        _linha_tot(GOVERNADOR, "GOVERNADOR", 15, "IBANEIS ROCHA", 220),
        _linha_tot(GOVERNADOR, "GOVERNADOR", 43, "LEANDRO GRASS", 120),
        _linha_tot(GOVERNADOR, "GOVERNADOR", 95, "Branco", 20),
        _linha_tot(GOVERNADOR, "GOVERNADOR", 96, "Nulo", 10),
        _linha_tot(SENADOR, "SENADOR", 555, "SENADOR SINTÉTICO", 999),
    ]
    return linhas


# --- agregação pura --------------------------------------------------------


def test_agregacao_soma_as_linhas_de_cada_votavel_e_zera_a_diferenca():
    votaveis = agregar_votaveis(_linhas_bu_sinteticas(), _linhas_tot_sinteticas())
    por_chave = {(v["cargo_id"], v["votavel"]): v for v in votaveis}
    bolsonaro = por_chave[(PRESIDENTE, 22)]
    assert bolsonaro["votos_bu"] == 300
    assert bolsonaro["votos_totalizacao"] == 300
    assert bolsonaro["diferenca"] == 0
    # brancos e nulos entram na conferência de cada cargo
    assert por_chave[(PRESIDENTE, 95)]["diferenca"] == 0
    assert por_chave[(PRESIDENTE, 96)]["diferenca"] == 0
    assert por_chave[(GOVERNADOR, 15)]["votos_bu"] == 220
    assert por_chave[(GOVERNADOR, 96)]["diferenca"] == 0


def test_agregacao_detecta_divergencia_entre_os_lados():
    linhas_tot = _linhas_tot_sinteticas()
    linhas_tot[0]["votos"] = 298  # Bolsonaro: 300 no BU × 298 na totalização
    votaveis = agregar_votaveis(_linhas_bu_sinteticas(), linhas_tot)
    por_chave = {(v["cargo_id"], v["votavel"]): v for v in votaveis}
    assert por_chave[(PRESIDENTE, 22)]["diferenca"] == 2
    assert por_chave[(PRESIDENTE, 13)]["diferenca"] == 0


def test_agregacao_votavel_em_apenas_um_lado_vale_zero_do_outro():
    linhas_bu = _linhas_bu_sinteticas()
    linhas_bu.append(_linha_bu(PRESIDENTE, "Presidente", 99, "CANDIDATO SÓ NO BU", "Nominal", 7))
    votaveis = agregar_votaveis(linhas_bu, _linhas_tot_sinteticas())
    por_chave = {(v["cargo_id"], v["votavel"]): v for v in votaveis}
    so_bu = por_chave[(PRESIDENTE, 99)]
    assert so_bu["votos_bu"] == 7
    assert so_bu["votos_totalizacao"] == 0
    assert so_bu["diferenca"] == 7


# --- relatório sobre banco DuckDB sintético --------------------------------


def _criar_banco_sintetico(con: duckdb.DuckDBPyConnection, turno: int = 1) -> None:
    con.execute(
        "CREATE TABLE bu (turno INTEGER, cargo_id INTEGER, cargo VARCHAR, votavel BIGINT, "
        "votavel_nome VARCHAR, tipo_votavel VARCHAR, votos BIGINT)"
    )
    con.execute(
        "CREATE TABLE secao_total (turno INTEGER, cargo_id INTEGER, cargo VARCHAR, "
        "votavel BIGINT, votavel_nome VARCHAR, votos BIGINT)"
    )
    for linha in _linhas_bu_sinteticas():
        con.execute(
            "INSERT INTO bu VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                turno,
                linha["cargo_id"],
                linha["cargo"],
                linha["votavel"],
                linha["nome"],
                linha["tipo"],
                linha["votos"],
            ],
        )
    # linhas de outro turno não podem entrar na conferência
    con.execute("INSERT INTO bu VALUES (2, 1, 'Presidente', 22, 'JAIR BOLSONARO', 'Nominal', 500)")
    for linha in _linhas_tot_sinteticas():
        con.execute(
            "INSERT INTO secao_total VALUES (?, ?, ?, ?, ?, ?)",
            [
                turno,
                linha["cargo_id"],
                linha["cargo"],
                linha["votavel"],
                linha["nome"],
                linha["votos"],
            ],
        )
    con.execute("INSERT INTO secao_total VALUES (2, 1, 'PRESIDENTE', 22, 'JAIR BOLSONARO', 500)")


def test_relatorio_no_banco_sintetico_cobre_os_dois_cargos_com_diferenca_zero():
    con = duckdb.connect(":memory:")
    _criar_banco_sintetico(con)
    relatorio = construir_relatorio(con, uf="DF", turno=1)
    assert [c["cargo"] for c in relatorio["cargos"]] == ["Presidente", "Governador"]
    presidente = relatorio["cargos"][0]
    por_votavel = {v["votavel"]: v for v in presidente["votaveis"]}
    assert por_votavel[22]["votos_bu"] == 300  # 150 + 150; turno 2 (500) fica fora
    assert por_votavel[13]["votos_totalizacao"] == 200
    assert por_votavel[95]["votos_bu"] == 50
    assert por_votavel[96]["votos_totalizacao"] == 30
    governador = relatorio["cargos"][1]
    assert {v["votavel"] for v in governador["votaveis"]} == {15, 43, 95, 96}
    assert all(v["diferenca"] == 0 for c in relatorio["cargos"] for v in c["votaveis"])
    assert relatorio["consistente"] is True
    assert relatorio["diferenca"] == 0
    con.close()


def test_relatorio_deixa_cargo_fora_da_analise_fora_da_conferencia():
    con = duckdb.connect(":memory:")
    _criar_banco_sintetico(con)
    relatorio = construir_relatorio(con, uf="DF", turno=1)
    assert all(c["cargo_id"] != SENADOR for c in relatorio["cargos"])
    con.close()


def test_relatorio_divergente_e_marcado_como_inconsistente():
    con = duckdb.connect(":memory:")
    _criar_banco_sintetico(con)
    con.execute(
        "UPDATE secao_total SET votos = 298 WHERE turno = 1 AND cargo_id = 1 AND votavel = 22"
    )
    relatorio = construir_relatorio(con, uf="DF", turno=1)
    assert relatorio["consistente"] is False
    assert relatorio["diferenca"] == 2
    con.close()


# --- conferência no disco (banco ausente) ----------------------------------


def test_banco_ausente_falha_de_forma_orientada(tmp_path):
    with pytest.raises(TotaisError) as excinfo:
        conferir(uf="DF", turno=1, data_dir=tmp_path)
    assert "ingest" in str(excinfo.value)


# --- validação e relato ----------------------------------------------------


def test_validar_relatorio_divergente_falha_com_detalhes(capsys):
    relatorio = {
        "uf": "DF",
        "turno": 1,
        "cargos": [
            {
                "cargo": "Presidente",
                "cargo_id": 1,
                "votaveis": [
                    {
                        "votavel": 22,
                        "nome": "JAIR BOLSONARO",
                        "votos_bu": 300,
                        "votos_totalizacao": 298,
                        "diferenca": 2,
                    }
                ],
            }
        ],
        "diferenca": 2,
        "consistente": False,
    }
    with pytest.raises(TotaisError) as excinfo:
        validar(relatorio)
    mensagem = str(excinfo.value)
    assert "Presidente" in mensagem
    assert "22" in mensagem
    assert "BU" in mensagem and "totalização" in mensagem
    # o relato permanece no terminal mesmo diante da falha
    relatar(relatorio)
    saida = capsys.readouterr().out
    assert "JAIR BOLSONARO" in saida
    assert "298" in saida


def test_relatar_relatorio_consistente_mostra_todos_os_votaveis(capsys):
    con = duckdb.connect(":memory:")
    _criar_banco_sintetico(con)
    relatorio = construir_relatorio(con, uf="DF", turno=1)
    con.close()
    relatar(relatorio)
    saida = capsys.readouterr().out
    assert "Presidente" in saida and "Governador" in saida
    assert "JAIR BOLSONARO" in saida and "LULA" in saida
    assert "Branco" in saida and "Nulo" in saida
    assert "Diferença" in saida
    assert "0" in saida
