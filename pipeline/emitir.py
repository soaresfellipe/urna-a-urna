"""Emissão dos JSONs particionados em public/data (architecture.md §4.8).

Grava três famílias de arquivos a partir dos resultados das etapas:

- ``sinalizacoes.json`` — lista ranqueada de seções atípicas: os achados dos
  três módulos (integridade, divergência, voto cruzado) combinados pelo
  ranking (pipeline/ranking.py), agrupados por seção, com severidade (menor
  q-valor / maior z) e ordenação q ascendente, z descendente; cada achado
  carrega a frase explicativa com os valores observado e de referência.
- ``zonas/{zona}.json`` — números por seção para a página de detalhe: por
  cargo de análise, aptos/comparecimento, válidos, brancos, nulos, votos e
  percentuais por votável (BU e totalização lado a lado), resultado da
  integridade com as exceções aplicadas e as referências usadas nas
  comparações (vazias até os módulos 2 e 3 existirem).
- ``index.json`` — uf/ano/turno, data de geração, espelho dos limiares do
  config.yaml, conferência de totais por candidato, contagens por módulo
  (seções sinalizadas, achados, total de seções, percentual sinalizado),
  checksums das fontes (de data/raw/manifest.json) e manifesto dos arquivos
  emitidos.

O terminal recebe o resumo das contagens por módulo ao final da execução
(via ``relatar``). A emissão nunca derruba o pipeline por achados de módulo —
só por dados inconsistentes (a conferência de totais é validada pelo CLI).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from pipeline import guardas, ranking
from pipeline.erros import EmitirError
from pipeline.ingest import CARGO_GOVERNADOR, CARGO_PRESIDENTE
from pipeline.totais import validar as validar_totais

CARGOS_ANALISE = (CARGO_PRESIDENTE, CARGO_GOVERNADOR)
VOTAVEL_BRANCO = 95
VOTAVEL_NULO = 96
MIB = 1024 * 1024

# Uma linha por (zona, seção, cargo, votável): números da totalização ao lado
# dos do BU — a página de detalhe mostra os dois lados da comparação R3. A
# base de votáveis é a totalização (registro da seção); votável presente só
# no BU não entra nos números da zona (o achado R3 o expõe com os dois lados).
_SQL_NUMEROS = """
SELECT z.zona, z.secao, z.local_id, z.local_votacao, z.endereco_local,
       z.municipio_id, z.municipio,
       p.cargo_id, p.cargo, p.aptos, p.comparecimento, p.abstencoes,
       s.votavel, s.votavel_nome, s.votos AS votos_totalizacao,
       b.votos AS votos_bu
FROM (SELECT DISTINCT zona, secao, local_id, local_votacao, endereco_local,
             municipio_id, municipio
      FROM locais) AS z
JOIN pleito AS p
    ON p.zona = z.zona AND p.secao = z.secao AND p.cargo_id IN (?, ?)
LEFT JOIN secao_total AS s
    ON s.zona = z.zona AND s.secao = z.secao AND s.cargo_id = p.cargo_id
LEFT JOIN (
    SELECT zona, secao, cargo_id, votavel, SUM(votos) AS votos
    FROM bu
    GROUP BY 1, 2, 3, 4
) AS b
    ON b.zona = z.zona AND b.secao = z.secao
   AND b.cargo_id = p.cargo_id AND b.votavel = s.votavel
ORDER BY z.zona, z.secao, p.cargo_id, s.votavel
"""


# ---------------------------------------------------------------------------
# Contagens por módulo (index.json + terminal)
# ---------------------------------------------------------------------------


def _contagens_por_modulo(
    resultado_integridade: dict[str, object] | None,
    *,
    resultado_divergencia: dict[str, object] | None = None,
    resultado_cruzado: dict[str, object] | None = None,
) -> dict[str, dict[str, object]]:
    """Contagens por módulo: seções sinalizadas, achados, total e percentual.

    Módulos ainda não implementados entram aqui quando existirem (a emissão
    aceita os resultados como argumentos opcionais).
    """
    contagens: dict[str, dict[str, object]] = {}

    def _rubrica(nome: str, resultado: dict[str, object] | None) -> None:
        if resultado is None:
            return
        # Módulos que separam "total de seções" de "seções avaliadas" (o
        # módulo 2 registra não analisadas) usam o total como base do
        # percentual sinalizado; o módulo 1 segue com as seções analisadas.
        total = resultado.get("secoes_total")
        if not isinstance(total, int):
            total = int(resultado["secoes_analisadas"])  # type: ignore[arg-type]
        sinalizadas = int(resultado["secoes_sinalizadas"])  # type: ignore[arg-type]
        achados = resultado["achados"]  # type: ignore[union-attr]
        rubrica: dict[str, object] = {
            "secoes_sinalizadas": sinalizadas,
            "achados": len(achados),
            "secoes_analisadas": int(resultado["secoes_analisadas"]),  # type: ignore[arg-type]
            "secoes_total": total,
            "percentual_sinalizado": round(sinalizadas / total * 100, 2) if total else 0.0,
        }
        if "secoes_nao_analisadas" in resultado:
            rubrica["secoes_nao_analisadas"] = resultado["secoes_nao_analisadas"]
        if "testes" in resultado:
            rubrica["testes"] = resultado["testes"]
        if "contagens" in resultado:
            rubrica["por_regra"] = resultado["contagens"]
        if "contagens_por_par" in resultado:
            rubrica["por_par"] = resultado["contagens_por_par"]
        if "excecoes" in resultado:
            rubrica["excecoes"] = resultado["excecoes"]
        contagens[nome] = rubrica

    _rubrica("integridade", resultado_integridade)
    _rubrica("divergencia", resultado_divergencia)
    _rubrica("cruzado", resultado_cruzado)
    return contagens


# ---------------------------------------------------------------------------
# sinalizacoes.json — lista ranqueada (ranking combinado, §4.7)
# ---------------------------------------------------------------------------


def _frase_achado(achado: dict[str, object]) -> str:
    """Frase explicativa do achado (movida para pipeline/ranking.py)."""
    return ranking.frase_explicativa(achado)


def _construir_sinalizacoes(
    resultado_integridade: dict[str, object],
    *,
    uf: str,
    ano: int,
    turno: int,
    resultado_divergencia: dict[str, object] | None = None,
    resultado_cruzado: dict[str, object] | None = None,
    locais_por_secao: dict[tuple[int, int], dict[str, object]],
) -> dict[str, object]:
    """Lista ranqueada global (ranking combinado): agrupa achados por seção,
    calcula a severidade (menor q / maior z) e ordena q ascendente, z
    descendente — ver pipeline/ranking.py."""
    return ranking.ranquear(
        ranking.achados_dos_resultados(
            resultado_integridade,
            resultado_divergencia,
            resultado_cruzado,
        ),
        uf=uf,
        ano=ano,
        turno=turno,
        locais_por_secao=locais_por_secao,
        total_secoes=ranking.total_de_secoes(
            resultado_integridade,
            resultado_divergencia,
            resultado_cruzado,
        ),
    )


# ---------------------------------------------------------------------------
# zonas/{zona}.json — números por seção para a página de detalhe
# ---------------------------------------------------------------------------


def _percentual(votos: int, validos: int) -> float | None:
    if validos == 0:
        return None
    return round(votos / validos * 100, 2)


def _zona_em_bytes(
    con: duckdb.DuckDBPyConnection,
    resultado_integridade: dict[str, object],
) -> tuple[dict[int, dict[str, object]], dict[tuple[int, int], dict[str, object]]]:
    """Consulta o banco e devolve os dados por zona e o mapa de locais.

    Devolve: (zonas com as seções, locais por (zona, seção)) — o mapa de
    locais alimenta as entradas do sinalizacoes.json.
    """
    achados_por_secao_cargo: dict[tuple[int, int, int], list[dict]] = {}
    for achado in resultado_integridade["achados"]:  # type: ignore[union-attr]
        chave = (
            int(achado["zona"]),  # type: ignore[arg-type]
            int(achado["secao"]),  # type: ignore[arg-type]
            int(achado["cargo_id"]),  # type: ignore[arg-type]
        )
        achados_por_secao_cargo.setdefault(chave, []).append(achado)
    excecoes_por_secao: dict[tuple[int, int], list[dict]] = {}
    for registro in resultado_integridade["excecoes_aplicadas"]:  # type: ignore[union-attr]
        chave = (int(registro["zona"]), int(registro["secao"]))  # type: ignore[arg-type]
        excecoes_por_secao.setdefault(chave, []).append(registro)

    zonas: dict[int, dict[str, object]] = {}
    locais_por_secao: dict[tuple[int, int], dict[str, object]] = {}
    secoes_por_zona: dict[int, dict[int, dict[str, object]]] = {}
    cargos_por_chave: dict[tuple[int, int, int], dict[str, object]] = {}
    for (
        zona,
        secao,
        local_id,
        local_nome,
        endereco,
        municipio_id,
        municipio,
        cargo_id,
        cargo,
        aptos,
        comparecimento,
        abstencoes,
        votavel,
        votavel_nome,
        votos_tot,
        votos_bu,
    ) in con.execute(_SQL_NUMEROS, [CARGO_PRESIDENTE, CARGO_GOVERNADOR]).fetchall():
        zonas.setdefault(
            int(zona),
            {
                "zona": int(zona),
                "municipio_id": municipio_id,
                "municipio": municipio,
                "secoes": [],
            },
        )
        secoes = secoes_por_zona.setdefault(int(zona), {})
        entrada = secoes.get(int(secao))
        if entrada is None:
            entrada = {
                "secao": int(secao),
                "local_id": int(local_id),
                "local": local_nome,
                "endereco": endereco,
                "cargos": [],
                "excecoes_aplicadas": excecoes_por_secao.get((int(zona), int(secao)), []),
            }
            secoes[int(secao)] = entrada
            locais_por_secao[(int(zona), int(secao))] = {
                "municipio_id": municipio_id,
                "municipio": municipio,
                "local_id": int(local_id),
                "local": local_nome,
                "endereco": endereco,
            }
        votos_bu = 0 if votos_bu is None else int(votos_bu)
        votos_tot = 0 if votos_tot is None else int(votos_tot)
        chave_cargo = (int(zona), int(secao), int(cargo_id))
        cargo_entrada = cargos_por_chave.get(chave_cargo)
        if cargo_entrada is None:
            cargo_entrada = {
                "cargo_id": int(cargo_id),
                "cargo": cargo,
                "aptos": int(aptos),
                "comparecimento": int(comparecimento),
                "abstencoes": int(abstencoes),
                "validos": 0,
                "brancos": 0,
                "nulos": 0,
                "votaveis": [],
                "integridade": {
                    "achados": achados_por_secao_cargo.get(chave_cargo, []),
                    "referencias": [],  # módulos 2 e 3 registradas aqui
                },
            }
            cargos_por_chave[chave_cargo] = cargo_entrada
            entrada["cargos"].append(cargo_entrada)
        if votavel is None:
            continue  # cargo sem nenhum votável na totalização
        entrada_votavel = {
            "votavel": int(votavel),
            "nome": votavel_nome,
            "votos": votos_bu,
            "votos_totalizacao": votos_tot,
            "percentual_validos": None,
        }
        cargo_entrada["votaveis"].append(entrada_votavel)
        if int(votavel) == VOTAVEL_BRANCO:
            cargo_entrada["brancos"] = votos_bu
        elif int(votavel) == VOTAVEL_NULO:
            cargo_entrada["nulos"] = votos_bu
        else:
            cargo_entrada["validos"] += votos_bu

    for zona, secoes in secoes_por_zona.items():
        for entrada in secoes.values():
            for cargo_entrada in entrada["cargos"]:  # percentuais dos válidos
                validos = int(cargo_entrada["validos"])  # type: ignore[arg-type]
                for votavel in cargo_entrada["votaveis"]:  # type: ignore[union-attr]
                    votavel["percentual_validos"] = _percentual(
                        int(votavel["votos"]),
                        validos,  # type: ignore[arg-type]
                    )
                ordenados = sorted(cargo_entrada["votaveis"], key=lambda v: v["votavel"])  # type: ignore[union-attr]
                cargo_entrada["votaveis"] = ordenados
        for numero in sorted(secoes):
            zonas[zona]["secoes"].append(secoes[numero])  # type: ignore[union-attr]
    return zonas, locais_por_secao


# ---------------------------------------------------------------------------
# index.json — espelho do config, conferência, contagens e manifestos
# ---------------------------------------------------------------------------


def _checksums_fontes(data_dir: Path) -> list[dict[str, object]]:
    """Espelho dos checksums registrados pelo download (data/raw/manifest.json)."""
    caminho_manifesto = Path(data_dir) / "raw" / "manifest.json"
    if not caminho_manifesto.is_file():
        raise EmitirError(
            f"Manifesto de download ausente em {caminho_manifesto} — rode a etapa "
            "'download' antes de 'emitir' (por ex.: --etapas download,ingest,emitir)."
        )
    manifesto = json.loads(caminho_manifesto.read_text(encoding="utf-8"))
    arquivos = manifesto.get("arquivos", {})
    if not isinstance(arquivos, dict):
        arquivos = {}
    checksums = []
    for nome in sorted(arquivos):
        entrada = arquivos[nome]
        checksums.append(
            {
                "nome": nome,
                "papel": entrada.get("papel"),
                "conjunto": entrada.get("conjunto"),
                "tamanho_bytes": entrada.get("tamanho_bytes"),
                "sha256": entrada.get("sha256"),
                "sha512_portal": entrada.get("sha512_portal"),
                "checksum_portal_verificado": entrada.get("checksum_portal_verificado"),
            }
        )
    return checksums


def _sha256_bytes(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def _bytes_json(dados: dict[str, object]) -> bytes:
    """Serialização determinística dos JSONs (a mesma usada ao gravar)."""
    return json.dumps(dados, indent=2, ensure_ascii=False, sort_keys=False).encode("utf-8")


def _gravar_json(caminho: Path, dados: dict[str, object]) -> bytes:
    """Grava JSON determinístico com troca atômica (.part → renomeia)."""
    conteudo = _bytes_json(dados)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False, dir=caminho.parent, suffix=".part"
    ) as temporario:
        temporario.write(conteudo)
    os.replace(temporario.name, caminho)
    return conteudo


def _limite_particao_bytes(config: dict[str, object]) -> int:
    """Limite do config (particao.tamanho_max_mb, em MiB) convertido em bytes."""
    particao = config.get("particao") or {}
    if not isinstance(particao, dict):
        particao = {}
    return int(particao.get("tamanho_max_mb", 20)) * MIB


def _particionar_sinalizacoes(
    sinalizacoes: dict[str, object],
) -> dict[int, dict[str, object]]:
    """Quebra a lista ranqueada por zona, preservando a ordem de cada grupo.

    Cada parte carrega os totais globais para contexto do painel; a união
    das partes, ordenada pela severidade, reproduce o arquivo único.
    """
    partes: dict[int, dict[str, object]] = {}
    for entrada in sinalizacoes["sinalizacoes"]:  # type: ignore[union-attr]
        zona = int(entrada["zona"])  # type: ignore[arg-type]
        if zona not in partes:
            partes[zona] = {
                "zona": zona,
                "uf": sinalizacoes["uf"],
                "ano": sinalizacoes["ano"],
                "turno": sinalizacoes["turno"],
                "total_secoes": sinalizacoes["total_secoes"],
                "total_sinalizadas": sinalizacoes["total_sinalizadas"],
                "total_achados": sinalizacoes["total_achados"],
                "sinalizacoes": [],
            }
        partes[zona]["sinalizacoes"].append(entrada)  # type: ignore[union-attr]
    return partes


def _manifesto_arquivos(emitidos: dict[str, bytes]) -> list[dict[str, object]]:
    """Manifesto dos arquivos emitidos (caminho relativo, tamanho e SHA-256).

    O index.json entra na lista como arquivo auto-referente (tamanho e hash
    nulos — computá-los exigiria o manifesto dentro do próprio arquivo).
    """
    caminhos = sorted(emitidos) + ["index.json"]
    manifesto = []
    for caminho in caminhos:
        if caminho == "index.json":
            manifesto.append({"caminho": caminho, "tamanho_bytes": None, "sha256": None})
        else:
            conteudo = emitidos[caminho]
            manifesto.append(
                {
                    "caminho": caminho,
                    "tamanho_bytes": len(conteudo),
                    "sha256": _sha256_bytes(conteudo),
                }
            )
    return manifesto


def _construir_index(
    *,
    config: dict[str, object],
    uf: str,
    ano: int,
    turno: int,
    relatorio_totais: dict[str, object],
    resultado_integridade: dict[str, object],
    contagens: dict[str, dict[str, object]],
    checksums_fontes: list[dict[str, object]],
    manifesto: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "uf": uf,
        "ano": ano,
        "turno": turno,
        "data_geracao": datetime.now(UTC).isoformat(),
        "limiares": json.loads(json.dumps(config.get("limiares", {}))),  # espelho do config
        "referencia": config.get("referencia", {}),
        "multiplicador_votos_por_eleitor": config.get("multiplicador_votos_por_eleitor", {}),
        "voto_cruzado": config.get("voto_cruzado", {}),
        "particao": config.get("particao", {}),
        "total_secoes": int(resultado_integridade["secoes_analisadas"]),  # type: ignore[arg-type]
        "conferencia_totais": relatorio_totais,
        "contagens": contagens,
        "checksums_fontes": checksums_fontes,
        "manifesto_arquivos": manifesto,
    }


# ---------------------------------------------------------------------------
# Orquestração da emissão
# ---------------------------------------------------------------------------


def emitir(
    *,
    uf: str,
    ano: int,
    turno: int,
    data_dir: Path,
    out_dir: Path,
    config: dict[str, object],
    relatorio_totais: dict[str, object],
    resultado_integridade: dict[str, object],
    resultado_divergencia: dict[str, object] | None = None,
    resultado_cruzado: dict[str, object] | None = None,
) -> dict[str, object]:
    """Grava os JSONs em out_dir e devolve o resumo das contagens por módulo.

    Requer a etapa 'ingest' já executada (banco em data/processed/urna.duckdb)
    e o manifesto do download em data/raw/manifest.json. A conferência de
    totais chega validada pelo CLI; aqui ela é espelhada no index.json.
    """
    validar_totais(relatorio_totais)
    caminho_banco = Path(data_dir) / "processed" / "urna.duckdb"
    if not caminho_banco.exists():
        raise EmitirError(
            f"Banco de dados ausente em {caminho_banco} — rode a etapa 'ingest' antes de "
            "'emitir' (por ex.: --etapas ingest,totais,integridade,emitir)."
        )
    con = duckdb.connect(str(caminho_banco), read_only=True)
    try:
        zonas, locais_por_secao = _zona_em_bytes(con, resultado_integridade)
    finally:
        con.close()

    contagens = _contagens_por_modulo(
        resultado_integridade,
        resultado_divergencia=resultado_divergencia,
        resultado_cruzado=resultado_cruzado,
    )
    sinalizacoes = _construir_sinalizacoes(
        resultado_integridade,
        uf=uf,
        ano=ano,
        turno=turno,
        resultado_divergencia=resultado_divergencia,
        resultado_cruzado=resultado_cruzado,
        locais_por_secao=locais_por_secao,
    )
    checksums_fontes = _checksums_fontes(data_dir)

    saida = Path(out_dir)
    pasta_zonas = saida / "zonas"
    pasta_sinalizacoes = saida / "sinalizacoes"
    if pasta_zonas.exists():
        # Zonas de execuções anteriores não podem sobrar de enxurrada (o
        # manifesto registra exatamente o que esta execução emitiu).
        shutil.rmtree(pasta_zonas)
    if pasta_sinalizacoes.exists():
        shutil.rmtree(pasta_sinalizacoes)
    emitidos: dict[str, bytes] = {}
    if len(_bytes_json(sinalizacoes)) > _limite_particao_bytes(config):
        # sinalizacoes.json excederia o limite do config: quebra por zona e
        # o manifesto do index.json lista as partes (architecture.md §4.8).
        # Um sinalizacoes.json único de execução anterior no mesmo diretório
        # é obsoleto neste regime — remove somente esse arquivo.
        obsoleto = saida / "sinalizacoes.json"
        if obsoleto.exists():
            obsoleto.unlink()
        partes = _particionar_sinalizacoes(sinalizacoes)
        for zona, parte in sorted(partes.items()):
            emitidos[f"sinalizacoes/{zona}.json"] = _gravar_json(
                saida / "sinalizacoes" / f"{zona}.json", parte
            )
    else:
        emitidos["sinalizacoes.json"] = _gravar_json(saida / "sinalizacoes.json", sinalizacoes)
    for zona, dados in zonas.items():
        emitidos[f"zonas/{zona}.json"] = _gravar_json(saida / "zonas" / f"{zona}.json", dados)
    manifesto = _manifesto_arquivos(emitidos)
    indice = _construir_index(
        config=config,
        uf=uf,
        ano=ano,
        turno=turno,
        relatorio_totais=relatorio_totais,
        resultado_integridade=resultado_integridade,
        contagens=contagens,
        checksums_fontes=checksums_fontes,
        manifesto=manifesto,
    )
    _gravar_json(saida / "index.json", indice)

    # Guardas de publicação: tamanho por arquivo e total de arquivos (os
    # limites vêm do config.yaml); violação levanta GuardaError.
    resumo_guardas = guardas.executar(saida, config)

    arquivos = ["index.json", *sorted(emitidos)]
    return {
        "out_dir": str(saida),
        "total_secoes": contagens["integridade"]["secoes_analisadas"],
        "arquivos": arquivos,
        "zonas": len(zonas),
        "contagens": contagens,
        "guardas": resumo_guardas,
    }


# ---------------------------------------------------------------------------
# Relato no terminal
# ---------------------------------------------------------------------------


def _numero(valor: int) -> str:
    return f"{valor:,}".replace(",", ".")


def _percentual_texto(valor: float) -> str:
    return f"{valor:.2f}".replace(".", ",") + "%"


def _descricao_sinalizacoes(arquivos: list[str]) -> str:
    """Descrição da forma de emissão do ranking: arquivo único ou por zona."""
    if any(arquivo == "sinalizacoes.json" for arquivo in arquivos):
        return "sinalizacoes.json"
    partes = [arquivo for arquivo in arquivos if arquivo.startswith("sinalizacoes/")]
    return f"sinalizacoes/ particionado em {len(partes)} arquivo(s) por zona"


def relatar(resumo: dict[str, object]) -> None:
    """Imprime o resumo da emissão: arquivos gravados, guardas e contagens."""
    zonas = int(resumo["zonas"])  # type: ignore[arg-type]
    arquivos = resumo["arquivos"]  # type: ignore[union-attr]
    print(
        f"[emissao] JSONs gravados em {resumo['out_dir']}: index.json, "
        f"{_descricao_sinalizacoes(arquivos)} "
        f"e {zonas} arquivo(s) de zona ({len(arquivos)} no total)"
    )
    limites = resumo.get("guardas", {})  # type: ignore[union-attr]
    if isinstance(limites, dict) and limites:
        print(
            f"[emissao] Guardas de publicação: {limites['arquivos']} arquivo(s) "
            f"em {limites['pasta']} — limite de {limites['tamanho_max_mb']:g} MiB por "
            f"arquivo e menos que {limites['total_max_arquivos']:,} arquivos".replace(",", ".")
        )
    print(
        "[emissao] index.json: limiares do config.yaml, conferência de totais, "
        "contagens por módulo, checksums das fontes e manifesto dos arquivos"
    )
    print("[emissao] Contagens por módulo:")
    for nome, contagem in resumo["contagens"].items():  # type: ignore[union-attr]
        por_regra = contagem.get("por_regra", {})  # type: ignore[union-attr]
        regras = ", ".join(f"{regra} {n}" for regra, n in sorted(por_regra.items()))
        por_par = contagem.get("por_par", {})  # type: ignore[union-attr]
        pares = " | ".join(
            f"{rotulo}: {dados['achados']} achado(s)"  # type: ignore[index]
            for rotulo, dados in por_par.items()
        )
        detalhes = "; ".join(particular for particular in (regras, pares) if particular)
        detalhe = f" — {detalhes}" if detalhes else ""
        print(
            f"[emissao]   {nome}: {contagem['secoes_sinalizadas']} seções sinalizadas "
            f"({contagem['achados']} achados{detalhe}) em "
            f"{_numero(int(contagem['secoes_analisadas']))} analisadas "
            f"({_percentual_texto(float(contagem['percentual_sinalizado']))})"
        )
