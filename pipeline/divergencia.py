"""Módulo 2 — Divergência dos percentuais por candidato em relação aos vizinhos.

Para cada candidato dos cargos de análise (Presidente e Governador), em cada
seção, o percentual sobre os votos válidos do PRÓPRIO cargo é comparado à
distribuição dos percentuais das demais seções do grupo de referência:

- grupo padrão: as demais seções do MESMO local de votação (mesma zona e
  mesmo nome do local), leave-one-out — a seção analisada não entra na
  própria mediana;
- fallback: local com menos de `min_secoes_local` seções usa a ZONA inteira
  (mínimo `min_secoes_zona`; abaixo disso a seção fica "não analisada", com
  o motivo registrado — nunca vira achado);
- seção com menos de `min_votos_validos_secao` votos válidos no cargo não é
  analisada (motivo registrado).

Sinalização exige TODOS os critérios do config (limiares.divergencia):

- z robusto |p − mediana| / (1,4826 × MAD) ≥ `z_robusto_min` (5,0), em módulo
  (a divergência vale nos dois sentidos);
- z de tamanho |p − mediana| / sqrt(mediana·(1−mediana)/n) ≥ `z_tamanho_min`
  (6,0), com n = votos válidos da seção (erro binomial);
- |p − mediana| ≥ `dif_minima_pp` (8,0 pp);
- q-valor Benjamini–Hochberg ≤ `fdr_alpha` (0,01) — p-valor normal bilateral
  do z robusto, calculado sobre TODOS os testes do módulo (seções ×
  candidatos × cargos).

O achado registra `tipo_referencia` ("local" ou "zona"), `tamanho_grupo`,
percentual observado, mediana, MAD, z, q — a comparação é sempre com a
distribuição local, nunca com um valor fixo. Seções pequenas e zonas
insuficientes são contadas como "não analisadas" e nunca sinalizadas.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

import duckdb

from pipeline.erros import DivergenciaError

# Cargos de análise do módulo 2 (módulos 2 e 3 olham Presidente/Governador;
# os demais cargos só entram nas checagens de integridade).
CARGO_PRESIDENTE = 1
CARGO_GOVERNADOR = 3
CARGOS_DE_ANALISE = (CARGO_PRESIDENTE, CARGO_GOVERNADOR)
NOMES_CARGO = {CARGO_PRESIDENTE: "Presidente", CARGO_GOVERNADOR: "Governador"}

# Votáveis não nominais (fora dos votos válidos): branco e nulo.
VOTAVEL_BRANCO = 95
VOTAVEL_NULO = 96

# Fator de consistência do MAD para o desvio normal equivalente.
FATOR_MAD = 1.4826

MOTIVO_VOTOS = "votos_validos_abaixo_do_minimo"
MOTIVO_ZONA = "zona_sem_secoes_suficientes"

_SQL_VOTOS_CANDIDATO = """
SELECT zona, secao, cargo_id, votavel, MAX(votavel_nome) AS votavel_nome,
       SUM(COALESCE(votos, 0)) AS votos
FROM secao_total
WHERE turno = ? AND cargo_id IN (?, ?) AND votavel NOT IN (?, ?)
GROUP BY 1, 2, 3, 4
"""

_SQL_VALIDOS = """
SELECT zona, secao, cargo_id, SUM(COALESCE(votos, 0)) AS validos
FROM secao_total
WHERE turno = ? AND cargo_id IN (?, ?) AND votavel NOT IN (?, ?)
GROUP BY 1, 2, 3
"""

# Mapa seção → local de votação (nome do local, junto com a zona, define o
# grupo de referência do módulo).
_SQL_LOCAIS = "SELECT zona, secao, local_id, local_votacao FROM locais WHERE turno = ?"

# Total de seções do pleito no banco (mesma rubrica do módulo 1).
_SQL_SECOES = """
SELECT DISTINCT zona, secao FROM (
    SELECT zona, secao FROM bu
    UNION
    SELECT zona, secao FROM secao_total
)
"""


# ---------------------------------------------------------------------------
# Estatística pura (unit-testável)
# ---------------------------------------------------------------------------


def mediana(valores: Sequence[float]) -> float:
    """Mediana clássica: elemento central (ou média dos dois centrais)."""
    ordenados = sorted(valores)
    meio = len(ordenados) // 2
    if len(ordenados) % 2:
        return float(ordenados[meio])
    return (ordenados[meio - 1] + ordenados[meio]) / 2.0


def mad(valores: Sequence[float]) -> float:
    """Desvio absoluto mediano (MAD) dos valores em torno da própria mediana."""
    if not valores:
        return 0.0
    centro = mediana(valores)
    return mediana([abs(valor - centro) for valor in valores])


def mediana_leave_um_fora(valores: Sequence[float], indice: int) -> tuple[float, float]:
    """Mediana e MAD do grupo SEM o valor da própria seção (leave-one-out).

    O valor de índice `indice` (o percentual da seção analisada) é retirado
    da lista antes do cálculo — a referência nunca é contaminada pela seção.
    """
    restante = list(valores[:indice]) + list(valores[indice + 1 :])
    return mediana(restante), mad(restante)


def z_robusto(percentual: float, mediana_grupo: float, mad_grupo: float) -> float:
    """Z robusto (p − mediana) / (1,4826 × MAD); com MAD 0, ±infinito ou 0."""
    if mad_grupo == 0.0:
        if percentual == mediana_grupo:
            return 0.0
        return math.inf if percentual > mediana_grupo else -math.inf
    return (percentual - mediana_grupo) / (FATOR_MAD * mad_grupo)


def z_tamanho(percentual: float, mediana_grupo: float, votos_validos: int) -> float:
    """Z de tamanho: |p − mediana| sobre o erro binomial sqrt(m(1−m)/n)."""
    diferenca = abs(percentual - mediana_grupo)
    se_quadrado = mediana_grupo * (1.0 - mediana_grupo) / votos_validos
    if se_quadrado <= 0.0:
        return 0.0 if diferenca == 0.0 else math.inf
    return diferenca / math.sqrt(se_quadrado)


def p_valor_normal_dupla(z: float) -> float:
    """P-valor bilateral da normal padrão para o |z| observado."""
    if math.isinf(z):
        return 0.0
    return math.erfc(abs(z) / math.sqrt(2.0))


def q_valores_bh(p_valores: Sequence[float]) -> list[float]:
    """Q-valores de Benjamini–Hochberg, na MESMA ordem dos p-valores dados.

    Passo a passo clássico: com os p-valores em ordem crescente de posto j,
    q = min sobre postos superiores de p_j × N / j, limitado a 1 e mantido
    monótono (o menor q se propaga para os postos anteriores).
    """
    total = len(p_valores)
    if total == 0:
        return []
    postos = sorted(range(total), key=lambda i: p_valores[i])
    q: list[float] = [1.0] * total
    menor = 1.0
    for pos in range(total - 1, -1, -1):
        indice = postos[pos]
        bruto = p_valores[indice] * total / (pos + 1)
        menor = min(menor, bruto, 1.0)
        q[indice] = menor
    return q


# ---------------------------------------------------------------------------
# Análise
# ---------------------------------------------------------------------------


def _limiares(config: dict[str, object]) -> dict[str, object]:
    caminho = config.get("limiares", {})
    if not isinstance(caminho, dict):
        raise DivergenciaError("config.yaml: 'limiares' ausente ou malformado.")
    modulo = caminho.get("divergencia", {})
    if not isinstance(modulo, dict):
        raise DivergenciaError(
            "config.yaml: 'limiares.divergencia' ausente — rode com o config.yaml da missão."
        )
    return modulo


def analisar(
    con: duckdb.DuckDBPyConnection,
    *,
    uf: str,
    turno: int,
    config: dict[str, object],
) -> dict[str, object]:
    """Avalia os percentuais por seção contra o grupo de referência local."""
    limiares = _limiares(config)
    z_robusto_min = float(limiares["z_robusto_min"])  # type: ignore[call-overload]
    z_tamanho_min = float(limiares["z_tamanho_min"])  # type: ignore[call-overload]
    dif_minima_pp = float(limiares["dif_minima_pp"])  # type: ignore[call-overload]
    fdr_alpha = float(limiares["fdr_alpha"])  # type: ignore[call-overload]
    min_secoes_local = int(limiares["min_secoes_local"])  # type: ignore[call-overload]
    min_secoes_zona = int(limiares["min_secoes_zona"])  # type: ignore[call-overload]
    min_votos = int(limiares["min_votos_validos_secao"])  # type: ignore[call-overload]

    # Percentual por candidato e votos válidos, por (seção, cargo) — o pleito
    # do próprio cargo; brancos e nulos ficam fora dos válidos.
    votos: dict[tuple[int, int, int], dict[int, int]] = defaultdict(dict)
    nomes: dict[tuple[int, int], str] = {}
    parametros = [turno, CARGO_PRESIDENTE, CARGO_GOVERNADOR, VOTAVEL_BRANCO, VOTAVEL_NULO]
    for zona, secao, cargo_id, votavel, nome, votos_linha in con.execute(
        _SQL_VOTOS_CANDIDATO, parametros
    ).fetchall():
        chave = (int(zona), int(secao), int(cargo_id))
        votos[chave][int(votavel)] = int(votos_linha)
        nomes[(int(cargo_id), int(votavel))] = str(nome)
    validos: dict[tuple[int, int, int], int] = {}
    for zona, secao, cargo_id, soma in con.execute(_SQL_VALIDOS, parametros).fetchall():
        validos[(int(zona), int(secao), int(cargo_id))] = int(soma)
    universo: dict[int, list[int]] = {}
    for cargo in CARGOS_DE_ANALISE:
        universo[cargo] = sorted(
            {votavel for (cargo_linha, votavel) in nomes if cargo_linha == cargo}
        )

    # Seções do pleito e grupos de referência (local por zona+nome; zona).
    secoes = {(int(zona), int(secao)) for zona, secao in con.execute(_SQL_SECOES).fetchall()}
    local_da_secao: dict[tuple[int, int], tuple[int, str]] = {}
    secoes_do_local: dict[tuple[int, str], set[tuple[int, int]]] = defaultdict(set)
    for zona, secao, local_id, local_nome in con.execute(_SQL_LOCAIS, [turno]).fetchall():
        chave = (int(zona), int(secao))
        local_da_secao[chave] = (int(local_id), str(local_nome))
        secoes_do_local[(int(zona), str(local_nome))].add(chave)
    secoes_da_zona: dict[int, set[tuple[int, int]]] = defaultdict(set)
    for chave in secoes:
        secoes_da_zona[chave[0]].add(chave)

    testes: list[dict[str, object]] = []
    nao_analisadas: list[dict[str, object]] = []
    secoes_analisadas: set[tuple[int, int]] = set()

    for zona, secao in sorted(secoes):
        for cargo_id in CARGOS_DE_ANALISE:
            chave_cargo = (zona, secao, cargo_id)
            n_proprio = validos.get(chave_cargo, 0)
            if n_proprio < min_votos:
                nao_analisadas.append(
                    {
                        "zona": zona,
                        "secao": secao,
                        "cargo_id": cargo_id,
                        "cargo": NOMES_CARGO[cargo_id],
                        "motivo": MOTIVO_VOTOS,
                    }
                )
                continue
            local = local_da_secao.get((zona, secao))
            tamanho_local = (
                len(secoes_do_local.get((zona, local[1]), ())) if local is not None else 0
            )
            if tamanho_local >= min_secoes_local:
                grupo = sorted(secoes_do_local[(zona, local[1])])  # type: ignore[index]
                tipo_referencia = "local"
            else:
                grupo = sorted(secoes_da_zona.get(zona, ()))
                if len(grupo) < min_secoes_zona:
                    nao_analisadas.append(
                        {
                            "zona": zona,
                            "secao": secao,
                            "cargo_id": cargo_id,
                            "cargo": NOMES_CARGO[cargo_id],
                            "motivo": MOTIVO_ZONA,
                        }
                    )
                    continue
                tipo_referencia = "zona"
            tamanho_grupo = len(grupo) - 1  # leave-one-out
            secoes_analisadas.add((zona, secao))
            local_id, local_nome = local if local is not None else (None, None)
            for votavel in universo[cargo_id]:
                p_proprio = votos[chave_cargo].get(votavel, 0) / n_proprio
                valores: list[float] = []
                indice_proprio: int | None = None
                for gzona, gsecao in grupo:
                    validos_grupo = validos.get((gzona, gsecao, cargo_id), 0)
                    if validos_grupo <= 0:
                        continue  # sem votos nominais no cargo: percentual indefinido
                    valores.append(
                        votos.get((gzona, gsecao, cargo_id), {}).get(votavel, 0) / validos_grupo
                    )
                    if (gzona, gsecao) == (zona, secao):
                        indice_proprio = len(valores) - 1
                if indice_proprio is None or len(valores) < 2:
                    continue  # sem grupo comparável para este candidato
                mediana_grupo, dispersao = mediana_leave_um_fora(valores, indice_proprio)
                zr = z_robusto(p_proprio, mediana_grupo, dispersao)
                zt = z_tamanho(p_proprio, mediana_grupo, n_proprio)
                diferenca_pp = abs(p_proprio - mediana_grupo) * 100.0
                testes.append(
                    {
                        "aprovado": (
                            abs(zr) >= z_robusto_min
                            and zt >= z_tamanho_min
                            and diferenca_pp >= dif_minima_pp
                        ),
                        "zona": zona,
                        "secao": secao,
                        "cargo_id": cargo_id,
                        "votavel": votavel,
                        "percentual": p_proprio * 100.0,
                        "mediana": mediana_grupo * 100.0,
                        "mad": dispersao * 100.0,
                        "z_robusto": abs(zr),
                        "z_tamanho": zt,
                        "diferenca_pp": diferenca_pp,
                        "tipo_referencia": tipo_referencia,
                        "tamanho_grupo": tamanho_grupo,
                        "votos_validos": n_proprio,
                        "local_id": local_id,
                        "local": local_nome,
                        "p_valor": p_valor_normal_dupla(abs(zr)),
                    }
                )

    q_valores = q_valores_bh([float(t["p_valor"]) for t in testes])  # type: ignore[arg-type]
    achados: list[dict[str, object]] = []
    for teste, q_valor in zip(testes, q_valores, strict=True):
        if not teste["aprovado"] or q_valor > fdr_alpha:  # type: ignore[operator]
            continue
        achados.append(
            {
                "modulo": "divergencia",
                "cargo_id": teste["cargo_id"],
                "cargo": NOMES_CARGO[teste["cargo_id"]],  # type: ignore[index]
                "zona": teste["zona"],
                "secao": teste["secao"],
                "local_id": teste["local_id"],
                "local": teste["local"],
                "votavel": teste["votavel"],
                "candidato": nomes.get((teste["cargo_id"], teste["votavel"]), ""),  # type: ignore[arg-type]
                "votos_candidato": votos[  # type: ignore[index]
                    (teste["zona"], teste["secao"], teste["cargo_id"])
                ].get(teste["votavel"], 0),  # type: ignore[union-attr]
                "percentual": teste["percentual"],
                "mediana": teste["mediana"],
                "mad": teste["mad"],
                "z_robusto": teste["z_robusto"],
                "z_tamanho": teste["z_tamanho"],
                "diferenca_pp": teste["diferenca_pp"],
                "q_valor": q_valor,
                "tipo_referencia": teste["tipo_referencia"],
                "tamanho_grupo": teste["tamanho_grupo"],
                "votos_validos": teste["votos_validos"],
            }
        )

    por_motivo: dict[str, int] = {MOTIVO_VOTOS: 0, MOTIVO_ZONA: 0}
    for registro in nao_analisadas:
        motivo = str(registro["motivo"])
        por_motivo[motivo] = por_motivo.get(motivo, 0) + 1
    secoes_sinalizadas = len({(a["zona"], a["secao"]) for a in achados})  # type: ignore[arg-type]
    total_secoes = len(secoes)
    return {
        "uf": uf,
        "turno": turno,
        "secoes_total": total_secoes,
        "secoes_analisadas": len(secoes_analisadas),
        "secoes_nao_analisadas": total_secoes - len(secoes_analisadas),
        "nao_analisadas": nao_analisadas,
        "nao_analisadas_por_motivo": por_motivo,
        "achados": achados,
        "secoes_sinalizadas": secoes_sinalizadas,
        "testes": len(testes),
        "candidatos_por_cargo": {str(cargo): len(universo[cargo]) for cargo in CARGOS_DE_ANALISE},
    }


def conferir(
    *,
    uf: str,
    turno: int,
    ano: int,  # noqa: ARG001 — simetria com os demais módulos (registro no resultado)
    data_dir: Path,
    config: dict[str, object],
) -> dict[str, object]:
    """Abre o banco da ingestão e avalia o módulo 2."""
    caminho_banco = Path(data_dir) / "processed" / "urna.duckdb"
    if not caminho_banco.exists():
        raise DivergenciaError(
            f"Banco de dados ausente em {caminho_banco} — rode a etapa 'ingest' antes de "
            "'divergencia' (por ex.: --etapas ingest,divergencia)."
        )
    con = duckdb.connect(str(caminho_banco), read_only=True)
    try:
        resultado = analisar(con, uf=uf, turno=turno, config=config)
    finally:
        con.close()
    resultado["ano"] = ano
    return resultado


def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _percentual_texto(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",") + "%"


def relatar(resultado: dict[str, object]) -> None:
    """Imprime as contagens do módulo 2 no terminal."""
    total = int(resultado["secoes_total"])  # type: ignore[arg-type]
    analisadas = int(resultado["secoes_analisadas"])  # type: ignore[arg-type]
    nao = int(resultado["secoes_nao_analisadas"])  # type: ignore[arg-type]
    achados = resultado["achados"]  # type: ignore[union-attr]
    sinalizadas = int(resultado["secoes_sinalizadas"])  # type: ignore[arg-type]
    print("[divergencia] Percentual por candidato × mediana/MAD leave-one-out do grupo local")
    print(
        f"[divergencia] Seções: {_numero(total)} total | {_numero(analisadas)} analisadas | "
        f"{_numero(nao)} não analisadas"
    )
    por_motivo = resultado["nao_analisadas_por_motivo"]  # type: ignore[union-attr]
    print(
        f"[divergencia] Não analisadas por motivo: {MOTIVO_VOTOS}: "
        f"{por_motivo.get(MOTIVO_VOTOS, 0)} | {MOTIVO_ZONA}: {por_motivo.get(MOTIVO_ZONA, 0)}"
    )
    taxa = sinalizadas / total * 100.0 if total else 0.0
    print(
        f"[divergencia] Testes: {_numero(int(resultado['testes']))} | "  # type: ignore[arg-type]
        f"achados: {_numero(len(achados))} em {_numero(sinalizadas)} seções "
        f"({_percentual_texto(taxa)} do total)"
    )
    for achado in achados[:10]:
        referencia = "local" if achado["tipo_referencia"] == "local" else "mesmo grupo (zona)"
        print(
            f"[divergencia] {achado['cargo']} zona {achado['zona']} seção {achado['secao']}: "
            f"candidato {achado['votavel']} ({achado['candidato']}) — "
            f"{_percentual_texto(float(achado['percentual']))} contra mediana de "
            f"{_percentual_texto(float(achado['mediana']))} nas demais "
            f"{achado['tamanho_grupo']} seções do {referencia} "
            f"(z={float(achado['z_robusto']):.2f}; q={float(achado['q_valor']):.4f})"
        )
    excedente = len(achados) - 10
    if excedente > 0:
        print(f"[divergencia] (+{excedente} achados não listados)")
