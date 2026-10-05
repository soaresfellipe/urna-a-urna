"""Conferência de totais por candidato: soma do Boletim de Urna × totalização.

Validação de integridade global do pleito: para cada cargo analisado
(Presidente e Governador), a soma dos votos do BU deve ser exatamente igual à
soma da totalização por seção, votável a votável — cada candidato nominal,
brancos (NR_VOTAVEL 95) e nulos (96). O filtro de turno já é aplicado na
ingestão das tabelas bu/secao_total e é reafirmado aqui.

O relatório é estruturado (dict pronto para o index.json) e também impresso no
terminal em formato legível. Qualquer diferença ≠ 0 entre os lados levanta
TotaisError — os dados reais divergem das premissas verificadas na inspeção
(library/tse-schema.md: diferença 0 em todos os votáveis do DF 2022).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import duckdb

from pipeline.erros import TotaisError
from pipeline.ingest import CARGO_GOVERNADOR, CARGO_PRESIDENTE

# Cargos de análise (códigos fixos do TSE, CD_CARGO / CD_CARGO_PERGUNTA).
CARGOS_ANALISE = (
    (CARGO_PRESIDENTE, "Presidente"),
    (CARGO_GOVERNADOR, "Governador"),
)

_SQL_LINHAS_BU = """
SELECT cargo_id, MAX(cargo) AS cargo, votavel, MAX(votavel_nome) AS nome,
       MAX(tipo_votavel) AS tipo, SUM(votos) AS votos
FROM bu
WHERE cargo_id IN (?, ?) AND turno = ?
GROUP BY cargo_id, votavel
"""

# A totalização não tem CD_TIPO_VOTAVEL — a classificação do votável vem do BU.
_SQL_LINHAS_TOT = """
SELECT cargo_id, MAX(cargo) AS cargo, votavel, MAX(votavel_nome) AS nome, SUM(votos) AS votos
FROM secao_total
WHERE cargo_id IN (?, ?) AND turno = ?
GROUP BY cargo_id, votavel
"""


def _acumular(linhas: Iterable[dict[str, object]], chave_votos: str) -> dict[tuple[int, int], dict]:
    """Soma as linhas por (cargo_id, votável), preservando nome e rótulos."""
    somas: dict[tuple[int, int], dict] = {}
    for linha in linhas:
        chave = (int(linha["cargo_id"]), int(linha["votavel"]))
        if chave in somas:
            somas[chave][chave_votos] += int(linha["votos"])
        else:
            somas[chave] = {
                "cargo_id": chave[0],
                "cargo": linha["cargo"],
                "votavel": chave[1],
                "nome": linha["nome"],
                "tipo": linha.get("tipo"),
                chave_votos: int(linha["votos"]),
            }
    return somas


def agregar_votaveis(
    linhas_bu: Iterable[dict[str, object]],
    linhas_tot: Iterable[dict[str, object]],
) -> list[dict[str, object]]:
    """Confere BU × totalização votável a votável, com diferença por votável.

    Votável presente em só um dos lados vale 0 do outro lado (diferença expõe
    a assimetria em vez de escondê-la).
    """
    bu = _acumular(linhas_bu, "votos_bu")
    tot = _acumular(linhas_tot, "votos_totalizacao")
    votaveis: list[dict[str, object]] = []
    for cargo_id, votavel in sorted(set(bu) | set(tot)):
        lado_bu = bu.get((cargo_id, votavel))
        lado_tot = tot.get((cargo_id, votavel))
        origem = lado_bu or lado_tot
        votos_bu = lado_bu["votos_bu"] if lado_bu else 0
        votos_tot = lado_tot["votos_totalizacao"] if lado_tot else 0
        votaveis.append(
            {
                "cargo_id": cargo_id,
                "cargo": origem["cargo"],
                "votavel": votavel,
                "nome": origem["nome"],
                "tipo": lado_bu["tipo"] if lado_bu else None,
                "votos_bu": votos_bu,
                "votos_totalizacao": votos_tot,
                "diferenca": votos_bu - votos_tot,
            }
        )
    return votaveis


def _linhas(con: duckdb.DuckDBPyConnection, sql: str, turno: int) -> list[dict]:
    resultado = con.execute(sql, [CARGO_PRESIDENTE, CARGO_GOVERNADOR, turno])
    colunas = [d[0] for d in resultado.description]
    return [dict(zip(colunas, linha, strict=True)) for linha in resultado.fetchall()]


def construir_relatorio(con: duckdb.DuckDBPyConnection, uf: str, turno: int) -> dict[str, object]:
    """Constrói o relatório de conferência sobre o banco tipado (etapa ingest)."""
    votaveis = agregar_votaveis(
        _linhas(con, _SQL_LINHAS_BU, turno),
        _linhas(con, _SQL_LINHAS_TOT, turno),
    )
    cargos: list[dict[str, object]] = []
    for cargo_id, cargo in CARGOS_ANALISE:
        do_cargo = [v for v in votaveis if v["cargo_id"] == cargo_id]
        cargos.append(
            {
                "cargo_id": cargo_id,
                "cargo": cargo,
                "votaveis": do_cargo,
                "diferenca": sum(int(v["diferenca"]) for v in do_cargo),
            }
        )
    consistente = all(int(v["diferenca"]) == 0 for v in votaveis)
    return {
        "uf": uf,
        "turno": turno,
        "cargos": cargos,
        "diferenca": sum(int(c["diferenca"]) for c in cargos),
        "consistente": consistente,
    }


def conferir(uf: str, turno: int, data_dir: Path) -> dict[str, object]:
    """Abre o banco da ingestão, confere os totais e devolve o relatório."""
    caminho_banco = Path(data_dir) / "processed" / "urna.duckdb"
    if not caminho_banco.exists():
        raise TotaisError(
            f"Banco de dados ausente em {caminho_banco} — rode a etapa 'ingest' antes de "
            "'totais' (por ex.: --etapas ingest,totais)."
        )
    con = duckdb.connect(str(caminho_banco), read_only=True)
    try:
        return construir_relatorio(con, uf=uf, turno=turno)
    finally:
        con.close()


def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def relatar(relatorio: dict[str, object]) -> None:
    """Imprime a conferência no terminal, um bloco por cargo, linha por votável."""
    print("[totais] Conferência de totais — soma do Boletim de Urna × totalização por seção")
    for cargo in relatorio["cargos"]:  # type: ignore[union-attr]
        print(f"[totais] {cargo['cargo']}:")
        print(
            "[totais]   Votável | Nome | Votos no BU | Votos na totalização | "
            "Diferença (BU − totalização)"
        )
        for v in cargo["votaveis"]:
            print(
                f"[totais]   {v['votavel']} | {v['nome']} | {_numero(int(v['votos_bu']))} | "
                f"{_numero(int(v['votos_totalizacao']))} | {_numero(int(v['diferenca']))}"
            )
    if relatorio["consistente"]:
        print(
            "[totais] Diferença total: 0 — totais de BU e totalização batem para "
            "todos os votáveis dos cargos analisados."
        )
    else:
        print(
            f"[totais] Diferença total: {_numero(int(relatorio['diferenca']))} — "
            "HÁ divergências entre os lados (ver acima)."
        )


def validar(relatorio: dict[str, object]) -> None:
    """Falha se qualquer votável divergir entre BU e totalização."""
    if relatorio["consistente"]:
        return
    divergentes: list[tuple[str, dict]] = [
        (str(cargo["cargo"]), v)  # type: ignore[union-attr]
        for cargo in relatorio["cargos"]  # type: ignore[union-attr]
        for v in cargo["votaveis"]  # type: ignore[union-attr]
        if int(v["diferenca"]) != 0
    ]
    detalhes = "; ".join(
        f"{nome_do_cargo} votável {v['votavel']} ({v['nome']}): BU {_numero(int(v['votos_bu']))} "
        f"× totalização {_numero(int(v['votos_totalizacao']))}"
        for nome_do_cargo, v in divergentes[:10]
    )
    excedente = "" if len(divergentes) <= 10 else f" (+{len(divergentes) - 10} outros)"
    raise TotaisError(
        "Divergência entre BU e totalização na conferência de totais — os dados não batem "
        "com a premissa de diferença 0 para o pleito. Divergências: "
        f"{detalhes}{excedente}. Verifique o filtro de turno (--turno) e os arquivos de "
        "origem no portal do TSE."
    )
