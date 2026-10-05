"""Módulo 1 — Integridade: regras determinísticas R1, R2 e R3.

Todas as regras são computadas SEMPRE por (seção, cargo), com o pleito do
PRÓPRIO cargo — aptos/comparecimento variam por cargo no BU real (378 urnas do
DF 2022 têm valores distintos entre federal e estadual), então deduplicar por
seção infla as comparações. O pleito é deduplicado por (seção, urna, cargo) e
somado por (seção, cargo), o que também ficaria correto num ano futuro com
mais de uma urna por seção.

Regras:
- R1: soma dos votos do cargo (nominais + legenda + brancos + nulos) =
  multiplicador × comparecimento, com o multiplicador de votos por eleitor
  definido no config por ano/cargo (ex.: senador 2018 = 2; 2022 = 1).
- R2: comparecimento ≤ aptos, ambos do pleito daquele cargo.
- R3: BU = totalização por seção, votável a votável (cada candidato, brancos
  e nulos).

Exceções legítimas — aplicadas por regra declarada e SEMPRE registradas no
resultado (campo `excecao_aplicada` nos achados e lista `excecoes_aplicadas`),
nunca silenciosas:
- multiplicador de votos por eleitor (senador com múltiplas vagas): R1
  compara contra `multiplicador × comparecimento`;
- voto em trânsito (SG_UF='VT') e exterior (SG_UF='ZZ'): seções excluídas de
  R3 e registradas (R1/R2 continuam valendo);
- seção agregada (DS_AGREGADAS): a urna agregada é comparada como unidade
  única contra a totalização da seção agregada (os componentes não aparecem
  como seção própria).

Saída: achados com `regra`, `valor_observado`, `valor_esperado`, `cargo`,
`secao` e `zona` (e, em R3, o votável divergente com os votos de cada lado).
Achados são saída da triagem — nunca derrubam o pipeline.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from pipeline.config import multiplicador_por_eleitor
from pipeline.erros import IntegridadeError

# Marcadores reais no arquivo (library/tse-schema.md): seções de voto em
# trânsito e de exterior — excluídas de R3 e registradas.
UFS_EXCLUIDAS_R3 = ("VT", "ZZ")

_SQL_PLEITO_CARGO = """
WITH p AS (
    -- pleito deduplicado por (seção, urna, cargo); somado por (seção, cargo)
    SELECT zona, secao, urna, cargo_id,
           MAX(cargo) AS cargo, MAX(aptos) AS aptos,
           MAX(comparecimento) AS comparecimento
    FROM bu
    WHERE turno = ?
    GROUP BY 1, 2, 3, 4
)
SELECT zona, secao, cargo_id, MAX(cargo) AS cargo,
       SUM(aptos) AS aptos, SUM(comparecimento) AS comparecimento
FROM p
GROUP BY 1, 2, 3
"""

# R1/R2: votos do cargo contra o pleito do PRÓPRIO cargo.
_SQL_R1_R2 = f"""
WITH p AS ({_SQL_PLEITO_CARGO}),
v AS (
    SELECT zona, secao, cargo_id, SUM(votos) AS votos
    FROM bu
    WHERE turno = ?
    GROUP BY 1, 2, 3
)
SELECT p.zona, p.secao, p.cargo_id, p.cargo,
       p.aptos, p.comparecimento, COALESCE(v.votos, 0) AS votos
FROM p
LEFT JOIN v ON v.zona = p.zona AND v.secao = p.secao AND v.cargo_id = p.cargo_id
"""

# R3: BU × totalização, votável a votável; votável em só um dos lados vale 0
# do outro (a divergência expõe a assimetria em vez de escondê-la). Seções de
# voto em trânsito (VT) e exterior (ZZ) são excluídas e registradas.
_SQL_R3 = """
WITH vb AS (
    SELECT zona, secao, cargo_id, MAX(cargo) AS cargo, votavel,
           MAX(votavel_nome) AS votavel_nome, SUM(votos) AS votos
    FROM bu
    WHERE turno = ?
    GROUP BY 1, 2, 3, 5
),
vt AS (
    SELECT zona, secao, cargo_id, MAX(cargo) AS cargo, votavel,
           MAX(votavel_nome) AS votavel_nome, SUM(votos) AS votos
    FROM secao_total
    WHERE turno = ?
    GROUP BY 1, 2, 3, 5
),
excluidas AS (
    SELECT zona, secao FROM bu WHERE uf IN ('VT', 'ZZ')
    UNION
    SELECT zona, secao FROM secao_total WHERE uf IN ('VT', 'ZZ')
)
SELECT COALESCE(vb.zona, vt.zona) AS zona,
       COALESCE(vb.secao, vt.secao) AS secao,
       COALESCE(vb.cargo_id, vt.cargo_id) AS cargo_id,
       COALESCE(vb.cargo, vt.cargo) AS cargo,
       COALESCE(vb.votavel, vt.votavel) AS votavel,
       COALESCE(vb.votavel_nome, vt.votavel_nome) AS votavel_nome,
       COALESCE(vb.votos, 0) AS votos_bu,
       COALESCE(vt.votos, 0) AS votos_totalizacao
FROM vb
FULL OUTER JOIN vt
    ON vb.zona = vt.zona AND vb.secao = vt.secao
   AND vb.cargo_id = vt.cargo_id AND vb.votavel = vt.votavel
WHERE COALESCE(vb.votos, 0) IS DISTINCT FROM COALESCE(vt.votos, 0)
  AND NOT EXISTS (
      SELECT 1 FROM excluidas e
      WHERE e.zona = COALESCE(vb.zona, vt.zona)
        AND e.secao = COALESCE(vb.secao, vt.secao)
  )
"""

# Urnas agregadas (DS_AGREGADAS): comparadas como unidade única em R3.
_SQL_AGREGADAS = """
SELECT zona, secao, MAX(urna) AS urna, MAX(agregadas) AS agregadas
FROM bu
WHERE agregadas IS NOT NULL
GROUP BY 1, 2
"""

# Seções marcadas como voto em trânsito ou exterior (em qualquer dos lados).
_SQL_MARCADORES_UF = """
SELECT DISTINCT zona, secao, uf FROM (
    SELECT zona, secao, uf FROM bu WHERE uf IN ('VT', 'ZZ')
    UNION
    SELECT zona, secao, uf FROM secao_total WHERE uf IN ('VT', 'ZZ')
)
"""

_SQL_SECOES = """
SELECT count(*) FROM (
    SELECT DISTINCT zona, secao FROM (
        SELECT zona, secao FROM bu
        UNION
        SELECT zona, secao FROM secao_total
    )
)
"""


def _multiplicador_do_cargo(config: dict[str, object], ano: int, cargo: str) -> int:
    """Multiplicador de votos por eleitor do cargo (chave do config em minúsculas)."""
    return multiplicador_por_eleitor(config, ano, cargo.strip().lower())


def _excecao_multiplicador(cargo: str, multiplicador: int, ano: int) -> dict[str, object]:
    return {
        "excecao": "multiplicador_votos_por_eleitor",
        "regra": "R1",
        "multiplicador": multiplicador,
        "detalhe": (
            f"{cargo} com {multiplicador} votos por eleitor ({ano}, config): R1 compara "
            f"contra {multiplicador} × comparecimento."
        ),
    }


def analisar(
    con: duckdb.DuckDBPyConnection,
    *,
    uf: str,
    turno: int,
    ano: int,
    config: dict[str, object],
) -> dict[str, object]:
    """Aplica R1, R2 e R3 sobre o banco tipado; devolve achados e exceções."""
    achados: list[dict[str, object]] = []
    excecoes_aplicadas: list[dict[str, object]] = []

    # ---- R1 e R2, sempre com o pleito do próprio cargo -----------------------
    pleito = con.execute(_SQL_R1_R2, [turno, turno]).fetchall()
    multiplicadores: dict[int, int] = {}
    for zona, secao, cargo_id, cargo, aptos, comparecimento, votos in pleito:
        mult = multiplicadores.setdefault(
            cargo_id, _multiplicador_do_cargo(config, ano, str(cargo))
        )
        soma = int(votos)
        esperado_r1 = mult * int(comparecimento)
        if soma != esperado_r1:
            excecao = None
            if mult > 1:
                excecao = _excecao_multiplicador(str(cargo), mult, ano)
            achados.append(
                {
                    "regra": "R1",
                    "cargo_id": int(cargo_id),
                    "cargo": cargo,
                    "zona": int(zona),
                    "secao": int(secao),
                    "valor_observado": soma,
                    "valor_esperado": esperado_r1,
                    "excecao_aplicada": excecao,
                }
            )
        if mult > 1:
            excecoes_aplicadas.append(
                {
                    "excecao": "multiplicador_votos_por_eleitor",
                    "regra": "R1",
                    "zona": int(zona),
                    "secao": int(secao),
                    "cargo": cargo,
                    "multiplicador": mult,
                    "detalhe": (
                        f"R1 compara contra {mult} × comparecimento para {cargo} "
                        f"({ano}; multiplicador do config)."
                    ),
                }
            )
        if int(comparecimento) > int(aptos):
            achados.append(
                {
                    "regra": "R2",
                    "cargo_id": int(cargo_id),
                    "cargo": cargo,
                    "zona": int(zona),
                    "secao": int(secao),
                    "valor_observado": int(comparecimento),
                    "valor_esperado": int(aptos),
                    "excecao_aplicada": None,
                }
            )

    # ---- R3, BU × totalização por votável -----------------------------------
    marcadas = {(int(z), int(s)): str(m) for z, s, m in con.execute(_SQL_MARCADORES_UF).fetchall()}
    agregadas = {
        (int(z), int(s)): (int(u), str(a)) for z, s, u, a in con.execute(_SQL_AGREGADAS).fetchall()
    }
    for zona, secao, cargo_id, cargo, votavel, votavel_nome, votos_bu, votos_tot in con.execute(
        _SQL_R3, [turno, turno]
    ).fetchall():
        chave = (int(zona), int(secao))
        marcador = marcadas.get(chave)
        if marcador is not None:
            # Seção de voto em trânsito/exterior: excluída de R3 (registrada abaixo).
            continue
        excecao = None
        if chave in agregadas:
            urna, nomes_agregadas = agregadas[chave]
            excecao = {
                "excecao": "secao_agregada",
                "regra": "R3",
                "urna": urna,
                "agregadas": nomes_agregadas,
                "detalhe": (
                    f"urna {urna} agregada cobre as seções {nomes_agregadas}; "
                    "comparada como unidade única contra a totalização da seção agregada."
                ),
            }
        achados.append(
            {
                "regra": "R3",
                "cargo_id": int(cargo_id),
                "cargo": cargo,
                "zona": int(zona),
                "secao": int(secao),
                "votavel": int(votavel),
                "votavel_nome": votavel_nome,
                "valor_observado": int(votos_bu),
                "valor_esperado": int(votos_tot),
                "excecao_aplicada": excecao,
            }
        )

    # ---- Registro das exceções aplicadas (nunca silenciosas) ----------------
    for (zona, secao), marcador in sorted(marcadas.items()):
        nome_excecao = "voto_em_transito" if marcador == "VT" else "exterior"
        descricao = "seção de voto em trânsito" if marcador == "VT" else "seção de exterior"
        excecoes_aplicadas.append(
            {
                "excecao": nome_excecao,
                "regra": "R3",
                "zona": zona,
                "secao": secao,
                "cargo": None,
                "detalhe": f"SG_UF='{marcador}' — {descricao} excluída da regra R3.",
            }
        )
    for (zona, secao), (urna, nomes_agregadas) in sorted(agregadas.items()):
        excecoes_aplicadas.append(
            {
                "excecao": "secao_agregada",
                "regra": "R3",
                "zona": zona,
                "secao": secao,
                "cargo": None,
                "detalhe": (
                    f"urna {urna} agregada cobre as seções {nomes_agregadas}; "
                    "comparada como unidade única contra a totalização da seção agregada."
                ),
            }
        )

    contagens = {"R1": 0, "R2": 0, "R3": 0}
    for achado in achados:
        contagens[achado["regra"]] += 1  # type: ignore[index]
    secoes_sinalizadas = len({(int(a["zona"]), int(a["secao"])) for a in achados})  # type: ignore[arg-type]
    por_excecao: dict[str, int] = {}
    for registro in excecoes_aplicadas:
        por_excecao[str(registro["excecao"])] = por_excecao.get(str(registro["excecao"]), 0) + 1
    return {
        "uf": uf,
        "turno": turno,
        "ano": ano,
        "secoes_analisadas": int(con.execute(_SQL_SECOES).fetchone()[0]),
        "achados": achados,
        "secoes_sinalizadas": secoes_sinalizadas,
        "contagens": contagens,
        "excecoes_aplicadas": excecoes_aplicadas,
        "excecoes": por_excecao,
    }


def conferir(
    *,
    uf: str,
    turno: int,
    ano: int,
    data_dir: Path,
    config: dict[str, object],
) -> dict[str, object]:
    """Abre o banco da ingestão e aplica as regras de integridade."""
    caminho_banco = Path(data_dir) / "processed" / "urna.duckdb"
    if not caminho_banco.exists():
        raise IntegridadeError(
            f"Banco de dados ausente em {caminho_banco} — rode a etapa 'ingest' antes de "
            "'integridade' (por ex.: --etapas ingest,integridade)."
        )
    con = duckdb.connect(str(caminho_banco), read_only=True)
    try:
        return analisar(con, uf=uf, turno=turno, ano=ano, config=config)
    finally:
        con.close()


def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def relatar(resultado: dict[str, object]) -> None:
    """Imprime as contagens do módulo 1 no terminal."""
    contagens = resultado["contagens"]  # type: ignore[union-attr]
    total = sum(int(v) for v in contagens.values())
    print("[integridade] Regras R1/R2/R3 por (seção, cargo), com o pleito do próprio cargo")
    print(f"[integridade] Seções analisadas: {_numero(int(resultado['secoes_analisadas']))}")
    print(
        f"[integridade] Achados: R1 {contagens['R1']} | R2 {contagens['R2']} | "
        f"R3 {contagens['R3']} — {_numero(total)} no total; "
        f"{resultado['secoes_sinalizadas']} seções sinalizadas"
    )
    excecoes = resultado["excecoes"]  # type: ignore[union-attr]
    rotulos = (
        ("secao_agregada", "seções agregadas"),
        ("voto_em_transito", "voto em trânsito (VT)"),
        ("exterior", "exterior (ZZ)"),
        ("multiplicador_votos_por_eleitor", "multiplicador de votos por eleitor"),
    )
    partes = ", ".join(f"{rotulo}: {excecoes.get(chave, 0)}" for chave, rotulo in rotulos)
    print(f"[integridade] Exceções aplicadas (registradas): {partes}")
    for achado in resultado["achados"][:10]:  # type: ignore[union-attr]
        print(
            f"[integridade] {achado['regra']} {achado['cargo']} zona {achado['zona']} "
            f"seção {achado['secao']}: observado {_numero(int(achado['valor_observado']))} "
            f"× esperado {_numero(int(achado['valor_esperado']))}"
        )
    excedente = len(resultado["achados"]) - 10  # type: ignore[arg-type]
    if excedente > 0:
        print(f"[integridade] (+{excedente} achados não listados)")
