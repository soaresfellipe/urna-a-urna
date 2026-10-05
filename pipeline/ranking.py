"""Ranking combinado das sinalizações (architecture.md §4.7).

Combina os achados dos três módulos (integridade, divergência e voto
cruzado) por seção — uma entrada por seção, com os achados agrupados e a
seção nunca duplicada — e ordena a lista globalmente:

- severidade da seção = MENOR q-valor / MAIOR z entre os achados dela;
- ordenação global: q-valor ascendente, depois z descendente; zona e seção
  entram como desempate determinístico.

Achados do módulo 1 (integridade) são determinísticos — violação certa de
regra, sem teste estatístico — e entram na severidade com q = 0,0 (o mais
certo que uma sinalização pode ser); não têm z, então não contribuem para o
"maior z" e ficam com ``z = None`` no JSON (que não representa infinito).

Cada achado carrega uma frase explicativa em pt-BR com os valores observado
e de referência, no formato do design, ex.:

- divergência: ``Candidato X: 45,2% dos válidos nesta seção contra mediana
  de 44,1% nas demais 48 seções do mesmo local (z=7,1; q=0,008)``;
- voto cruzado: ``Par aprovado pelo config: diferença de 12,4 pp nesta
  seção contra mediana de 2,1 pp na zona (z=5,3; q=0,010)``.

O vocabulário é controlado: apenas "atípico"/"divergente" (guarda
automatizada). Z infinito (dispersão nula com divergência) sai como
``z = None`` no JSON e como ``z=∞`` na frase.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from pipeline.erros import RankingError

_SQL_LOCAIS = """
SELECT DISTINCT zona, secao, local_id, local_votacao, endereco_local,
       municipio_id, municipio
FROM locais
WHERE turno = ?
"""

# Descritores de referência por tipo/nível (frase em pt-BR).
_REFERENCIA_DIV = {"local": "do mesmo local", "zona": "da mesma zona"}
_REFERENCIA_CRUZ = {
    "municipio": "no município",
    "local": "no mesmo local",
    "zona": "na zona",
}


# ---------------------------------------------------------------------------
# Formatação pt-BR (decimais com vírgula)
# ---------------------------------------------------------------------------


def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _decimal(valor: float, casas: int = 1) -> str:
    return f"{valor:.{casas}f}".replace(".", ",")


def _z_texto(z: float) -> str:
    return "∞" if math.isinf(z) else _decimal(z, 1)


def _z_seguro(z: float) -> float | None:
    """Z serializável: infinito vira None (JSON não representa infinito)."""
    return None if math.isinf(z) else z


# ---------------------------------------------------------------------------
# Frases explicativas (uma por módulo; despachadas por ``achado_publico``)
# ---------------------------------------------------------------------------


def frase_integridade(achado: dict[str, object]) -> str:
    """Frase do achado de integridade (R1/R2/R3) com os dois valores."""
    regra = str(achado["regra"])
    cargo = str(achado["cargo"])
    observado = _numero(int(achado["valor_observado"]))  # type: ignore[arg-type]
    esperado = _numero(int(achado["valor_esperado"]))  # type: ignore[arg-type]
    if regra == "R1":
        return (
            f"{cargo}: soma dos votos ({observado}) difere do comparecimento do "
            f"próprio cargo ({esperado}) — regra R1."
        )
    if regra == "R2":
        return f"{cargo}: comparecimento ({observado}) excede os aptos ({esperado}) — regra R2."
    if regra == "R3":
        votavel = achado["votavel"]
        nome = achado["votavel_nome"]
        return (
            f"{cargo}: votável {votavel} ({nome}) registra {observado} no Boletim de "
            f"Urna e {esperado} na totalização — regra R3."
        )
    return f"{cargo}: observado {observado} contra esperado {esperado} — regra {regra}."


def frase_divergencia(achado: dict[str, object]) -> str:
    """Frase do achado de divergência: percentual da seção × mediana do grupo."""
    candidato = str(achado.get("candidato") or f"candidato {achado['votavel']}")
    tipo = str(achado["tipo_referencia"])
    if tipo not in _REFERENCIA_DIV:
        raise RankingError(
            f"Achado de divergência com tipo de referência desconhecido ({tipo!r}) "
            "— esperado 'local' ou 'zona'."
        )
    return (
        f"{candidato}: {_decimal(float(achado['percentual']))}% dos válidos nesta seção "  # type: ignore[arg-type]
        f"contra mediana de {_decimal(float(achado['mediana']))}% "  # type: ignore[arg-type]
        f"nas demais {_numero(int(achado['tamanho_grupo']))} seções "  # type: ignore[arg-type]
        f"{_REFERENCIA_DIV[tipo]} "
        f"(z={_z_texto(float(achado['z_robusto']))}; q={_decimal(float(achado['q_valor']), 3)})"  # type: ignore[arg-type]
    )


def frase_cruzado(achado: dict[str, object]) -> str:
    """Frase do achado de voto cruzado: d da seção × mediana da referência."""
    nivel = str(achado["nivel_referencia"])
    if nivel not in _REFERENCIA_CRUZ:
        raise RankingError(
            f"Achado de voto cruzado com nível de referência desconhecido ({nivel!r}) "
            "— esperado 'municipio', 'local' ou 'zona'."
        )
    return (
        f"Par {achado['rotulo']}: diferença de {_decimal(float(achado['diferenca']))} pp "  # type: ignore[arg-type]
        f"nesta seção contra mediana de {_decimal(float(achado['mediana']))} pp "  # type: ignore[arg-type]
        f"{_REFERENCIA_CRUZ[nivel]} "
        f"(z={_z_texto(float(achado['z_robusto']))}; q={_decimal(float(achado['q_valor']), 3)})"  # type: ignore[arg-type]
    )


def frase_explicativa(achado: dict[str, object]) -> str:
    """Frase explicativa do achado, no formato do design, em pt-BR."""
    modulo = str(achado.get("modulo") or "integridade")
    if modulo == "integridade":
        return frase_integridade(achado)
    if modulo == "divergencia":
        return frase_divergencia(achado)
    if modulo == "cruzado":
        return frase_cruzado(achado)
    raise RankingError(
        f"Achado com módulo desconhecido ({modulo!r}) — esperado 'integridade', "
        "'divergencia' ou 'cruzado'."
    )


# ---------------------------------------------------------------------------
# Achado no formato público do sinalizacoes.json
# ---------------------------------------------------------------------------


def achado_publico(achado: dict[str, object]) -> dict[str, object]:
    """Achado uniforme para o painel: módulo, observado, referência, z, q.

    Campos ``valor_observado``/``valor_referencia`` padronizados por módulo
    (percentual do candidato, diferença pp do par, ou votos das regras);
    ``tipo_referencia``/``tamanho_grupo`` registram a referência usada; os
    campos específicos de cada módulo são preservados para o detalhe.
    """
    modulo = str(achado.get("modulo") or "integridade")
    if modulo == "integridade":
        return {
            "modulo": "integridade",
            "regra": achado["regra"],
            "cargo_id": achado["cargo_id"],
            "cargo": achado["cargo"],
            "votavel": achado.get("votavel"),
            "votavel_nome": achado.get("votavel_nome"),
            "valor_observado": achado["valor_observado"],
            "valor_esperado": achado["valor_esperado"],
            "valor_referencia": achado["valor_esperado"],
            "unidade": "votos",
            "excecao_aplicada": achado.get("excecao_aplicada"),
            "z": None,
            "q": None,
            "frase": frase_explicativa(achado),
        }
    if modulo == "divergencia":
        z_robusto = float(achado["z_robusto"])  # type: ignore[arg-type]
        z_tamanho = float(achado["z_tamanho"])  # type: ignore[arg-type]
        return {
            "modulo": "divergencia",
            "cargo_id": achado["cargo_id"],
            "cargo": achado["cargo"],
            "votavel": achado["votavel"],
            "candidato": achado["candidato"],
            "votos_candidato": achado["votos_candidato"],
            "valor_observado": achado["percentual"],
            "valor_referencia": achado["mediana"],
            "unidade": "percentual",
            "mediana": achado["mediana"],
            "mad": achado["mad"],
            "diferenca_pp": achado["diferenca_pp"],
            "z": _z_seguro(z_robusto),
            "z_robusto": _z_seguro(z_robusto),
            "z_tamanho": _z_seguro(z_tamanho),
            "q": achado["q_valor"],
            "tipo_referencia": achado["tipo_referencia"],
            "tamanho_grupo": achado["tamanho_grupo"],
            "votos_validos": achado["votos_validos"],
            "local_id": achado.get("local_id"),
            "local": achado.get("local"),
            "frase": frase_explicativa(achado),
        }
    if modulo == "cruzado":
        z_robusto = float(achado["z_robusto"])  # type: ignore[arg-type]
        z_tamanho = float(achado["z_tamanho"])  # type: ignore[arg-type]
        return {
            "modulo": "cruzado",
            "rotulo": achado["rotulo"],
            "presidente": achado["presidente"],
            "governador": achado["governador"],
            "valor_observado": achado["diferenca"],
            "valor_referencia": achado["mediana"],
            "unidade": "pp",
            "percentual_presidente": achado["percentual_presidente"],
            "percentual_governador": achado["percentual_governador"],
            "votos_validos_presidente": achado["votos_validos_presidente"],
            "votos_validos_governador": achado["votos_validos_governador"],
            "mediana": achado["mediana"],
            "mad": achado["mad"],
            "diferenca_pp": achado["diferenca_pp"],
            "z": _z_seguro(z_robusto),
            "z_robusto": _z_seguro(z_robusto),
            "z_tamanho": _z_seguro(z_tamanho),
            "q": achado["q_valor"],
            "tipo_referencia": achado["nivel_referencia"],
            "nivel_referencia": achado["nivel_referencia"],
            "tamanho_grupo": achado["tamanho_grupo"],
            "local_id": achado.get("local_id"),
            "local": achado.get("local"),
            "frase": frase_explicativa(achado),
        }
    raise RankingError(
        f"Achado com módulo desconhecido ({modulo!r}) — esperado 'integridade', "
        "'divergencia' ou 'cruzado'."
    )


# ---------------------------------------------------------------------------
# Severidade e ordenação
# ---------------------------------------------------------------------------


def _q_efetivo(achado: dict[str, object]) -> float:
    """Q-valor da severidade: integridade (determinística) conta como 0,0."""
    modulo = str(achado.get("modulo") or "integridade")
    return 0.0 if modulo == "integridade" else float(achado["q"])  # type: ignore[arg-type]


def _z_efetivo(achado: dict[str, object]) -> float | None:
    """Z da severidade: None quando o achado não tem z estatístico."""
    z = achado.get("z")
    return None if z is None else float(z)  # type: ignore[arg-type]


def _severidade(achados: list[dict[str, object]]) -> dict[str, object]:
    """Severidade da seção: menor q-valor / maior z entre os achados."""
    q = min(_q_efetivo(a) for a in achados)
    zs = [z for a in achados if (z := _z_efetivo(a)) is not None]
    return {"q": q, "z": max(zs) if zs else None}


def _chave_entrada(entrada: dict[str, object]) -> tuple[float, float, int, int]:
    """Chave global: q ascendente, z descendente, zona/seção como desempate."""
    z = entrada["severidade"]["z"]  # type: ignore[index,union-attr]
    return (
        float(entrada["severidade"]["q"]),  # type: ignore[index,union-attr,arg-type]
        -z if z is not None else -math.inf,
        int(entrada["zona"]),  # type: ignore[arg-type]
        int(entrada["secao"]),  # type: ignore[arg-type]
    )


def _chave_achado(achado: dict[str, object]) -> tuple[float, float, str]:
    """Chave dos achados dentro da seção: menor q primeiro, depois maior z."""
    z = _z_efetivo(achado)
    return (_q_efetivo(achado), -(z if z is not None else -math.inf), str(achado["modulo"]))


# ---------------------------------------------------------------------------
# Combinação e ordenação global
# ---------------------------------------------------------------------------


def achados_dos_resultados(*resultados: dict[str, object] | None) -> list[dict[str, object]]:
    """Achados de todos os resultados de módulo fornecidos (None é ignorado)."""
    return [achado for resultado in resultados if resultado for achado in resultado["achados"]]  # type: ignore[union-attr,index]


def total_de_secoes(*resultados: dict[str, object] | None) -> int:
    """Total de seções do pleito: o maior total registrado entre os módulos."""
    totais = []
    for resultado in resultados:
        if not resultado:
            continue
        total = resultado.get("secoes_total", resultado.get("secoes_analisadas", 0))
        totais.append(int(total))  # type: ignore[arg-type]
    return max(totais, default=0)


def locais_do_banco(
    con: duckdb.DuckDBPyConnection, turno: int
) -> dict[tuple[int, int], dict[str, object]]:
    """Mapa (zona, seção) → município/local/endereço, do banco da ingestão."""
    locais: dict[tuple[int, int], dict[str, object]] = {}
    for zona, secao, local_id, local_nome, endereco, municipio_id, municipio in con.execute(
        _SQL_LOCAIS, [turno]
    ).fetchall():
        locais[(int(zona), int(secao))] = {
            "municipio_id": municipio_id,
            "municipio": municipio,
            "local_id": local_id,
            "local": local_nome,
            "endereco": endereco,
        }
    return locais


def ranquear(
    achados: list[dict[str, object]],
    *,
    uf: str,
    ano: int,
    turno: int,
    locais_por_secao: dict[tuple[int, int], dict[str, object]],
    total_secoes: int,
) -> dict[str, object]:
    """Combina os achados por seção e devolve a lista ranqueada globalmente."""
    por_secao: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    for achado in achados:
        chave = (int(achado["zona"]), int(achado["secao"]))  # type: ignore[arg-type]
        por_secao[chave].append(achado)

    sinalizacoes: list[dict[str, object]] = []
    for (zona, secao), do_secao in por_secao.items():
        # Achado público convertido UMA vez (consumir dict público duplicado
        # quebraria os campos originais de cada módulo).
        publicos = [achado_publico(achado) for achado in do_secao]
        local = locais_por_secao.get((zona, secao))
        if local is None:
            # Seção fora do mapa de locais: os campos do achado (módulos 2 e
            # 3 carregam local/local_id) valem como fallback.
            local = {
                "municipio_id": None,
                "municipio": None,
                "local_id": publicos[0].get("local_id"),
                "local": publicos[0].get("local"),
                "endereco": None,
            }
        publicos.sort(key=_chave_achado)
        sinalizacoes.append(
            {
                "municipio_id": local.get("municipio_id"),
                "municipio": local.get("municipio"),
                "zona": zona,
                "secao": secao,
                "local_id": local.get("local_id"),
                "local": local.get("local"),
                "endereco": local.get("endereco"),
                "severidade": _severidade(publicos),
                "achados": publicos,
            }
        )
    sinalizacoes.sort(key=_chave_entrada)
    return {
        "uf": uf,
        "ano": ano,
        "turno": turno,
        "total_secoes": total_secoes,
        "total_sinalizadas": len(sinalizacoes),
        "total_achados": len(achados),
        "sinalizacoes": sinalizacoes,
    }


# ---------------------------------------------------------------------------
# Orquestração (etapa 'ranking' do CLI) e relato no terminal
# ---------------------------------------------------------------------------


def calcular(
    resultados: list[dict[str, object] | None],
    *,
    uf: str,
    ano: int,
    turno: int,
    data_dir: str | Path,
) -> dict[str, object]:
    """Abre o banco da ingestão e monta a lista ranqueada dos resultados.

    Requer a etapa 'ingest' (o mapa de locais vem de lá); os achados vêm dos
    resultados dos módulos passados pelo chamador (o CLI aproveita os das
    etapas já executadas nesta mesma invocação).
    """
    caminho_banco = Path(data_dir) / "processed" / "urna.duckdb"
    if not caminho_banco.exists():
        raise RankingError(
            f"Banco de dados ausente em {caminho_banco} — rode a etapa 'ingest' antes de "
            "'ranking' (por ex.: --etapas ingest,ranking)."
        )
    con = duckdb.connect(str(caminho_banco), read_only=True)
    try:
        locais = locais_do_banco(con, turno)
    finally:
        con.close()
    return ranquear(
        achados_dos_resultados(*resultados),
        uf=uf,
        ano=ano,
        turno=turno,
        locais_por_secao=locais,
        total_secoes=total_de_secoes(*resultados),
    )


def relatar(ranqueado: dict[str, object]) -> None:
    """Imprime o resumo do ranking: tamanho da lista e severidade da cabeça."""
    sinalizadas = int(ranqueado["total_sinalizadas"])  # type: ignore[arg-type]
    achados = int(ranqueado["total_achados"])  # type: ignore[arg-type]
    lista = ranqueado["sinalizacoes"]  # type: ignore[union-attr]
    if not lista:
        print(f"[ranking] nenhuma seção atípica — lista ranqueada vazia ({achados} achados).")
        return
    por_modulo = Counter(str(a["modulo"]) for e in lista for a in e["achados"])  # type: ignore[union-attr,index]
    resumo = ", ".join(f"{modulo} {n}" for modulo, n in sorted(por_modulo.items()))
    severidade = lista[0]["severidade"]  # type: ignore[index,union-attr]
    z = severidade["z"]  # type: ignore[index,union-attr]
    z_texto = "—" if z is None else _z_texto(float(z))
    print(
        f"[ranking] lista ranqueada: {achados} {'achado' if achados == 1 else 'achados'} "
        f"em {sinalizadas} {'seção' if sinalizadas == 1 else 'seções'} "
        f"({resumo}); total de {int(ranqueado['total_secoes'])} seções"  # type: ignore[arg-type]
    )
    print(
        f"[ranking] cabeça da lista: zona {lista[0]['zona']}, seção {lista[0]['secao']} "  # type: ignore[index,union-attr]
        f"(severidade: q={_decimal(float(severidade['q']), 3)}, z={z_texto})"  # type: ignore[index,union-attr]
    )
