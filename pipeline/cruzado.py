"""Módulo 3 — Voto cruzado relativo entre pares presidente × governador.

Os pares vêm EXCLUSIVAMENTE do config.yaml (`voto_cruzado.pares`, cada um
com o rótulo aprovado); nomes e números de candidato nunca aparecem no
código — trocar, remover ou acrescentar um par no config muda o resultado
sem qualquer mudança de código. Para cada seção e par do config:

    d = p_presidente(A) − p_governador(B)

com p como percentual dos votos VÁLIDOS do PRÓPRIO cargo (o pleito de cada
cargo; brancos e nulos ficam fora dos válidos).

Referência: a distribuição de d no nível definido no config
(`referencia.nivel`), leave-one-out com mediana e MAD — a seção analisada
nunca entra na própria referência. No DF o município é único e degenerado
(BRASÍLIA, 97012): o config pede município com fallback zona e a comparação
cai direto na zona; o nível EFETIVAMENTE usado é registrado no resultado e
em cada achado.

Sinalização exige TODOS os critérios do config (limiares.cruzado):

- z robusto |d − mediana| / (1,4826 × MAD) ≥ `z_robusto_min` (5,0);
- z de tamanho |d − mediana| / erro combinado ≥ `z_tamanho_min` (6,0), com
  erro combinado sqrt(p1(1−p1)/n1 + p2(1−p2)/n2) (n = votos válidos de cada
  cargo na seção);
- |d − mediana| ≥ `dif_minima_pp` (8,0 pp);
- q-valor Benjamini–Hochberg ≤ `fdr_alpha` (0,01) sobre TODOS os testes do
  módulo (seções × pares), com p-valor normal bilateral do z robusto.

Toda sinalização identifica o par pelo rótulo do config. A comparação é
sempre com a distribuição local de d, nunca com um valor fixo.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

import duckdb

from pipeline.divergencia import (
    _SQL_SECOES,
    _SQL_VALIDOS,
    _SQL_VOTOS_CANDIDATO,
    CARGO_GOVERNADOR,
    CARGO_PRESIDENTE,
    VOTAVEL_BRANCO,
    VOTAVEL_NULO,
    mad,
    mediana,
    p_valor_normal_dupla,
    q_valores_bh,
    z_robusto,
)
from pipeline.erros import CruzadoError

# Local de votação com município — o município define o nível de referência
# do módulo (config: referencia.nivel com fallback).
_SQL_LOCAIS_MUNICIPIO = """
SELECT zona, secao, local_id, local_votacao, municipio_id, municipio
FROM locais
WHERE turno = ?
"""


# ---------------------------------------------------------------------------
# Config: pares e nível de referência (única fonte — nada fixo no código)
# ---------------------------------------------------------------------------


def _limiares(config: dict[str, object]) -> dict[str, object]:
    caminho = config.get("limiares", {})
    if not isinstance(caminho, dict):
        raise CruzadoError("config.yaml: 'limiares' ausente ou malformado.")
    modulo = caminho.get("cruzado", {})
    if not isinstance(modulo, dict):
        raise CruzadoError(
            "config.yaml: 'limiares.cruzado' ausente — rode com o config.yaml da missão."
        )
    return modulo


def pares_do_config(config: dict[str, object]) -> list[dict[str, object]]:
    """Pares presidente×governador do config.yaml, com rótulo e nomes.

    Levanta CruzadoError orientado quando o config não traz pares válidos —
    o módulo nunca roda um par que não esteja aprovado no config.yaml.
    """
    voto_cruzado = config.get("voto_cruzado", {})
    if not isinstance(voto_cruzado, dict):
        raise CruzadoError("config.yaml: 'voto_cruzado' ausente ou malformado.")
    pares = voto_cruzado.get("pares")
    if not isinstance(pares, list) or not pares:
        raise CruzadoError(
            "config.yaml: 'voto_cruzado.pares' ausente ou vazio — defina os pares aprovados "
            "no config.yaml (o módulo 3 nunca fixa candidatos no código)."
        )
    lidos: list[dict[str, object]] = []
    for indice, par in enumerate(pares):
        contexto = f"voto_cruzado.pares[{indice}]"
        if not isinstance(par, dict):
            raise CruzadoError(f"config.yaml: '{contexto}' malformado.")
        rotulo = par.get("rotulo")
        if not isinstance(rotulo, str) or not rotulo.strip():
            raise CruzadoError(f"config.yaml: '{contexto}.rotulo' deve ser texto não vazio.")
        lados: dict[str, object] = {"rotulo": rotulo.strip()}
        for lado in ("presidente", "governador"):
            dados = par.get(lado)
            numero = dados.get("numero") if isinstance(dados, dict) else None
            nome = dados.get("nome") if isinstance(dados, dict) else None
            if isinstance(numero, bool) or not isinstance(numero, int):
                raise CruzadoError(
                    f"config.yaml: '{contexto}.{lado}.numero' deve ser o número do candidato "
                    f"(inteiro; recebido: {numero!r})."
                )
            if not isinstance(nome, str) or not nome.strip():
                raise CruzadoError(
                    f"config.yaml: '{contexto}.{lado}.nome' deve ser texto não vazio."
                )
            lados[lado] = {"numero": int(numero), "nome": nome.strip()}
        lidos.append(lados)
    return lidos


def nivel_de_referencia(config: dict[str, object]) -> tuple[str, str]:
    """(nível, fallback) de referência do config.yaml, validados."""
    referencia = config.get("referencia", {})
    if not isinstance(referencia, dict):
        raise CruzadoError("config.yaml: 'referencia' ausente ou malformado.")
    nivel = referencia.get("nivel")
    fallback = referencia.get("fallback")
    if nivel not in ("municipio", "local", "zona"):
        raise CruzadoError(
            "config.yaml: 'referencia.nivel' deve ser 'municipio', 'local' ou 'zona' "
            f"(recebido: {nivel!r})."
        )
    if fallback not in ("municipio", "local", "zona"):
        raise CruzadoError(
            "config.yaml: 'referencia.fallback' deve ser 'municipio', 'local' ou 'zona' "
            f"(recebido: {fallback!r})."
        )
    return str(nivel), str(fallback)


def nivel_efetivo(config: dict[str, object], municipios: set[str]) -> str:
    """Nível de referência EFETIVO, com fallback quando a dimensão degenera.

    No DF o município é único (BRASÍLIA, 97012): o config pede município com
    fallback zona e a comparação cai direto na zona — sempre registrado.
    """
    nivel, fallback = nivel_de_referencia(config)
    if nivel == "municipio" and len(municipios) <= 1:
        return fallback
    return nivel


# ---------------------------------------------------------------------------
# Estatística do módulo (erro combinado dos dois cargos)
# ---------------------------------------------------------------------------


def z_tamanho_erro_combinado(
    d: float,
    mediana_grupo: float,
    n_presidente: int,
    n_governador: int,
    p_presidente: float,
    p_governador: float,
) -> float:
    """Z de tamanho: |d − mediana| sobre o erro combinado dos percentuais.

    Erro combinado sqrt(p1(1−p1)/n1 + p2(1−p2)/n2), com p e n observados na
    própria seção (n = votos válidos de cada cargo); com erro 0, ±infinito
    quando há diferença e 0 quando não há.
    """
    variancia = (
        p_presidente * (1.0 - p_presidente) / n_presidente
        + p_governador * (1.0 - p_governador) / n_governador
    )
    erro = math.sqrt(max(variancia, 0.0))
    diferenca = abs(d - mediana_grupo)
    if erro == 0.0:
        return math.inf if diferenca > 0.0 else 0.0
    return diferenca / erro


# ---------------------------------------------------------------------------
# Análise
# ---------------------------------------------------------------------------


def analisar(
    con: duckdb.DuckDBPyConnection,
    *,
    uf: str,
    turno: int,
    config: dict[str, object],
) -> dict[str, object]:
    """Avalia a diferença d de cada par por seção contra a distribuição local."""
    limiares = _limiares(config)
    z_robusto_min = float(limiares["z_robusto_min"])  # type: ignore[call-overload]
    z_tamanho_min = float(limiares["z_tamanho_min"])  # type: ignore[call-overload]
    dif_minima_pp = float(limiares["dif_minima_pp"])  # type: ignore[call-overload]
    fdr_alpha = float(limiares["fdr_alpha"])  # type: ignore[call-overload]
    pares = pares_do_config(config)

    # Votos nominais e válidos por (seção, cargo) — o pleito do próprio cargo.
    votos: dict[tuple[int, int, int], dict[int, int]] = defaultdict(dict)
    parametros = [turno, CARGO_PRESIDENTE, CARGO_GOVERNADOR, VOTAVEL_BRANCO, VOTAVEL_NULO]
    for zona, secao, cargo_id, votavel, _nome, votos_linha in con.execute(
        _SQL_VOTOS_CANDIDATO, parametros
    ).fetchall():
        votos[(int(zona), int(secao), int(cargo_id))][int(votavel)] = int(votos_linha)
    validos: dict[tuple[int, int, int], int] = {}
    for zona, secao, cargo_id, soma in con.execute(_SQL_VALIDOS, parametros).fetchall():
        validos[(int(zona), int(secao), int(cargo_id))] = int(soma)

    # Seções do pleito e município/local de cada uma (nível de referência).
    secoes = {(int(zona), int(secao)) for zona, secao in con.execute(_SQL_SECOES).fetchall()}
    municipio_da_secao: dict[tuple[int, int], str] = {}
    local_da_secao: dict[tuple[int, int], tuple[int | None, str | None]] = {}
    for zona, secao, local_id, local_nome, municipio_id, municipio in con.execute(
        _SQL_LOCAIS_MUNICIPIO, [turno]
    ).fetchall():
        chave = (int(zona), int(secao))
        municipio_da_secao[chave] = f"{municipio_id} {municipio}"
        local_da_secao[chave] = (
            (int(local_id), str(local_nome)) if local_id is not None else (None, None)
        )
    nivel_usado = nivel_efetivo(config, set(municipio_da_secao.values()))

    # Grupo de referência de cada seção conforme o nível efetivo do config.
    def _grupo(chave: tuple[int, int]) -> object:
        if nivel_usado == "municipio":
            return municipio_da_secao.get(chave)
        if nivel_usado == "local":
            local = local_da_secao.get(chave)
            return None if local is None else (chave[0], local[1])
        return chave[0]

    chaves_grupo = {chave: _grupo(chave) for chave in secoes}

    testes: list[dict[str, object]] = []
    secoes_analisadas: set[tuple[int, int]] = set()
    testes_por_par: dict[str, int] = {str(par["rotulo"]): 0 for par in pares}  # type: ignore[arg-type]

    for par in pares:
        rotulo = str(par["rotulo"])  # type: ignore[arg-type]
        numero_presidente = int(par["presidente"]["numero"])  # type: ignore[index,union-attr]
        numero_governador = int(par["governador"]["numero"])  # type: ignore[index,union-attr]
        nome_presidente = str(par["presidente"]["nome"])  # type: ignore[index,union-attr]
        nome_governador = str(par["governador"]["nome"])  # type: ignore[index,union-attr]

        # d de cada seção para este par (somente seções comparáveis nos dois
        # cargos: com votos válidos positivos nos dois pleitos).
        observados: dict[tuple[int, int], dict[str, float]] = {}
        for zona, secao in sorted(secoes):
            chave_presidente = (zona, secao, CARGO_PRESIDENTE)
            chave_governador = (zona, secao, CARGO_GOVERNADOR)
            n_presidente = validos.get(chave_presidente, 0)
            n_governador = validos.get(chave_governador, 0)
            if n_presidente <= 0 or n_governador <= 0:
                continue  # percentual indefinido em um dos cargos
            p_presidente = votos[chave_presidente].get(numero_presidente, 0) / n_presidente
            p_governador = votos[chave_governador].get(numero_governador, 0) / n_governador
            observados[(zona, secao)] = {
                "d": p_presidente - p_governador,
                "p1": p_presidente,
                "p2": p_governador,
                "n1": float(n_presidente),
                "n2": float(n_governador),
            }

        grupos: dict[object, list[tuple[int, int]]] = defaultdict(list)
        for chave in observados:
            grupo = chaves_grupo.get(chave)
            if grupo is not None:
                grupos[grupo].append(chave)

        for (zona, secao), observado in sorted(observados.items()):
            vizinhos = [
                vizinho
                for vizinho in grupos.get(chaves_grupo.get((zona, secao)), ())
                if vizinho != (zona, secao)
            ]
            if len(vizinhos) < 2:
                continue  # sem grupo comparável (leave-one-out exige ≥ 2 valores)
            valores = [float(observados[vizinho]["d"]) for vizinho in vizinhos]
            secoes_analisadas.add((zona, secao))
            testes_por_par[rotulo] += 1

            d = float(observado["d"])
            mediana_grupo = mediana(valores)
            dispersao = mad(valores)
            zr = abs(z_robusto(d, mediana_grupo, dispersao))
            zt = z_tamanho_erro_combinado(
                d,
                mediana_grupo,
                int(observado["n1"]),
                int(observado["n2"]),
                float(observado["p1"]),
                float(observado["p2"]),
            )
            diferenca_pp = abs(d - mediana_grupo) * 100.0
            local_id, local_nome = local_da_secao.get((zona, secao), (None, None))
            testes.append(
                {
                    "aprovado": (
                        zr >= z_robusto_min
                        and zt >= z_tamanho_min
                        and diferenca_pp >= dif_minima_pp
                    ),
                    "rotulo": rotulo,
                    "nome_presidente": nome_presidente,
                    "nome_governador": nome_governador,
                    "zona": zona,
                    "secao": secao,
                    "local_id": local_id,
                    "local": local_nome,
                    "diferenca": d * 100.0,
                    "percentual_presidente": float(observado["p1"]) * 100.0,
                    "percentual_governador": float(observado["p2"]) * 100.0,
                    "votos_validos_presidente": int(observado["n1"]),
                    "votos_validos_governador": int(observado["n2"]),
                    "mediana": mediana_grupo * 100.0,
                    "mad": dispersao * 100.0,
                    "z_robusto": zr,
                    "z_tamanho": zt,
                    "diferenca_pp": diferenca_pp,
                    "nivel_referencia": nivel_usado,
                    "tamanho_grupo": len(vizinhos),
                    "p_valor": p_valor_normal_dupla(zr),
                }
            )

    q_valores = q_valores_bh([float(t["p_valor"]) for t in testes])  # type: ignore[arg-type]
    achados: list[dict[str, object]] = []
    for teste, q_valor in zip(testes, q_valores, strict=True):
        if not teste["aprovado"] or q_valor > fdr_alpha:  # type: ignore[operator]
            continue
        achados.append(
            {
                "modulo": "cruzado",
                "rotulo": teste["rotulo"],
                "presidente": teste["nome_presidente"],
                "governador": teste["nome_governador"],
                "zona": teste["zona"],
                "secao": teste["secao"],
                "local_id": teste["local_id"],
                "local": teste["local"],
                "diferenca": teste["diferenca"],
                "percentual_presidente": teste["percentual_presidente"],
                "percentual_governador": teste["percentual_governador"],
                "votos_validos_presidente": teste["votos_validos_presidente"],
                "votos_validos_governador": teste["votos_validos_governador"],
                "mediana": teste["mediana"],
                "mad": teste["mad"],
                "z_robusto": teste["z_robusto"],
                "z_tamanho": teste["z_tamanho"],
                "diferenca_pp": teste["diferenca_pp"],
                "q_valor": q_valor,
                "nivel_referencia": teste["nivel_referencia"],
                "tamanho_grupo": teste["tamanho_grupo"],
            }
        )

    contagens_por_par: dict[str, dict[str, int]] = {}
    for par in pares:
        rotulo = str(par["rotulo"])  # type: ignore[arg-type]
        do_par = [a for a in achados if a["rotulo"] == rotulo]
        contagens_por_par[rotulo] = {
            "achados": len(do_par),
            "secoes_sinalizadas": len({(a["zona"], a["secao"]) for a in do_par}),  # type: ignore[arg-type]
        }
    secoes_sinalizadas = len({(a["zona"], a["secao"]) for a in achados})  # type: ignore[arg-type]
    total_secoes = len(secoes)
    return {
        "uf": uf,
        "turno": turno,
        "nivel_referencia_usado": nivel_usado,
        "pares": [str(par["rotulo"]) for par in pares],  # type: ignore[union-attr,index]
        "secoes_total": total_secoes,
        "secoes_analisadas": len(secoes_analisadas),
        "achados": achados,
        "secoes_sinalizadas": secoes_sinalizadas,
        "testes": len(testes),
        "contagens_por_par": contagens_por_par,
    }


def conferir(
    *,
    uf: str,
    turno: int,
    ano: int,  # noqa: ARG001 — simetria com os demais módulos (registro no resultado)
    data_dir: Path,
    config: dict[str, object],
) -> dict[str, object]:
    """Abre o banco da ingestão e avalia o módulo 3."""
    caminho_banco = Path(data_dir) / "processed" / "urna.duckdb"
    if not caminho_banco.exists():
        raise CruzadoError(
            f"Banco de dados ausente em {caminho_banco} — rode a etapa 'ingest' antes de "
            "'cruzado' (por ex.: --etapas ingest,cruzado)."
        )
    con = duckdb.connect(str(caminho_banco), read_only=True)
    try:
        resultado = analisar(con, uf=uf, turno=turno, config=config)
    finally:
        con.close()
    resultado["ano"] = ano
    return resultado


# ---------------------------------------------------------------------------
# Relato no terminal
# ---------------------------------------------------------------------------


def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _percentual_texto(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",") + "%"


def relatar(resultado: dict[str, object]) -> None:
    """Imprime as contagens do módulo 3 no terminal, sempre por par."""
    total = int(resultado["secoes_total"])  # type: ignore[arg-type]
    analisadas = int(resultado["secoes_analisadas"])  # type: ignore[arg-type]
    achados = resultado["achados"]  # type: ignore[union-attr]
    sinalizadas = int(resultado["secoes_sinalizadas"])  # type: ignore[arg-type]
    print("[cruzado] Diferença d = percentual de válidos do presidente − do governador, por par")
    rotulos = resultado["pares"]  # type: ignore[union-attr]
    print(
        f"[cruzado] Pares do config.yaml: {len(rotulos)} "
        f"({'; '.join(str(rotulo) for rotulo in rotulos)})"  # type: ignore[arg-type]
    )
    nivel = str(resultado["nivel_referencia_usado"])
    if nivel == "zona":
        print(
            "[cruzado] Referência: distribuição de d na zona "
            "(município único/degenerado — fallback do config)"
        )
    else:
        print(f"[cruzado] Referência: distribuição de d no {nivel}")
    taxa = sinalizadas / total * 100.0 if total else 0.0
    print(
        f"[cruzado] Seções: {_numero(total)} total | {_numero(analisadas)} analisadas | "
        f"testes: {_numero(int(resultado['testes']))}"  # type: ignore[arg-type]
    )
    print(
        f"[cruzado] achados: {_numero(len(achados))} em {_numero(sinalizadas)} seções "
        f"({_percentual_texto(taxa)} do total)"
    )
    for rotulo, contagem in resultado["contagens_por_par"].items():  # type: ignore[union-attr]
        print(
            f"[cruzado] Par {rotulo}: {contagem['achados']} achado(s) em "
            f"{contagem['secoes_sinalizadas']} seção(ões)"
        )
    for achado in achados[:10]:
        print(
            f"[cruzado] Par {achado['rotulo']} — zona {achado['zona']} seção "
            f"{achado['secao']}: d de {_percentual_texto(float(achado['diferenca']))} "
            f"contra mediana de {_percentual_texto(float(achado['mediana']))} "
            f"nas demais {achado['tamanho_grupo']} seções "
            f"(z={float(achado['z_robusto']):.2f}; q={float(achado['q_valor']):.4f})"
        )
    excedente = len(achados) - 10
    if excedente > 0:
        print(f"[cruzado] (+{excedente} achados não listados)")
