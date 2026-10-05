"""Ingestão dos CSVs do TSE em tabelas tipadas no DuckDB.

Formato real dos arquivos (verificado em data/reports/schema-2022-df.md):
encoding Latin-1, delimitador ';', todos os campos entre aspas. Tudo é lido
como TEXTO e tipado explicitamente na criação das tabelas — a inferência de
tipos do DuckDB converte códigos (zona, votável, município) para inteiro, e o
design os trata como categoria quando convém, então a tipagem é controlada
aqui, coluna a coluna.

Contrato de esquema: as colunas obrigatórias de cada arquivo são verificadas
ANTES de qualquer escrita (o cabeçalho é lido direto do ZIP). Coluna ausente
→ IngestaoError nomeando o arquivo, a coluna e o que fazer; nada é ingerido
de forma parcial (o banco é construído em arquivo temporário e promovido por
renomeação atômica).

Tabelas em data/processed/urna.duckdb:
- bu           — 1 linha por urna × cargo × votável (Boletim de Urna da UF).
- secao_total  — 1 linha por seção × cargo × votável (UF + Presidente do BR).
- pleito       — aptos/comparecimento/abstenções por (seção, cargo), dedupli-
                 cadas do BU: no BU esses valores repetem em cada linha de
                 votável e variam por cargo (nunca deduplicar por seção).
- locais       — zona, seção, local de votação (número, nome e endereço).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

from pipeline.erros import IngestaoError

# Papéis registrados no manifesto do download (data/raw/manifest.json).
PAPEIS_ESPERADOS = ("boletim_de_urna", "totalizacao_uf", "totalizacao_presidente")

# Contrato de esquema: colunas obrigatórias por arquivo (mínimo para o pipeline).
OBRIGATORIAS_BU = (
    "ANO_ELEICAO",
    "NR_TURNO",
    "SG_UF",
    "CD_MUNICIPIO",
    "NM_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "NR_LOCAL_VOTACAO",
    "CD_ELEICAO",
    "CD_CARGO_PERGUNTA",
    "DS_CARGO_PERGUNTA",
    "QT_APTOS",
    "QT_COMPARECIMENTO",
    "QT_ABSTENCOES",
    "CD_TIPO_VOTAVEL",
    "DS_TIPO_VOTAVEL",
    "NR_VOTAVEL",
    "NM_VOTAVEL",
    "QT_VOTOS",
    "NR_URNA_EFETIVADA",
    "DS_AGREGADAS",
)
OBRIGATORIAS_TOTALIZACAO = (
    "ANO_ELEICAO",
    "NR_TURNO",
    "SG_UF",
    "CD_MUNICIPIO",
    "NM_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "CD_CARGO",
    "DS_CARGO",
    "NR_VOTAVEL",
    "NM_VOTAVEL",
    "QT_VOTOS",
    "NR_LOCAL_VOTACAO",
    "SQ_CANDIDATO",
    "NM_LOCAL_VOTACAO",
    "DS_LOCAL_VOTACAO_ENDERECO",
)

# Código fixo do TSE para o cargo de Presidente (CD_CARGO / CD_CARGO_PERGUNTA).
CARGO_PRESIDENTE = 1

# Código fixo do TSE para o cargo de Governador (CD_CARGO / CD_CARGO_PERGUNTA).
CARGO_GOVERNADOR = 3


# ---------------------------------------------------------------------------
# Localização e leitura das fontes (via manifesto do download)
# ---------------------------------------------------------------------------


def _caminho_zip(papel: str, bruto: Path) -> Path:
    """Resolve o zip da fonte pelo papel registrado no manifesto do download."""
    caminho_manifesto = bruto / "manifest.json"
    if not caminho_manifesto.is_file():
        raise IngestaoError(
            f"Manifesto de download não encontrado em {caminho_manifesto}. Rode a etapa "
            "'download' do pipeline antes da ingestão — ela registra os arquivos "
            "baixados do portal do TSE."
        )
    manifesto = json.loads(caminho_manifesto.read_text(encoding="utf-8"))
    arquivos = manifesto.get("arquivos", {})
    achados = [
        (nome, entrada)
        for nome, entrada in arquivos.items()
        if isinstance(entrada, dict) and entrada.get("papel") == papel
    ]
    if len(achados) != 1:
        raise IngestaoError(
            f"O manifesto {caminho_manifesto} não registra exatamente um arquivo com o "
            f"papel '{papel}' ({len(achados)} encontrados). Rode a etapa 'download' "
            "novamente para reconstruir o manifesto."
        )
    nome, _entrada = achados[0]
    caminho = bruto / nome
    if not caminho.is_file():
        raise IngestaoError(
            f"O arquivo '{nome}' registrado no manifesto não está mais em {bruto}. Rode "
            "a etapa 'download' do pipeline para baixá-lo novamente."
        )
    return caminho


def _membro_csv(caminho_zip: Path) -> str:
    """Nome do único membro .csv dentro do zip (os zips do TSE trazem CSV + PDF)."""
    with zipfile.ZipFile(caminho_zip) as arquivo:
        membros = [nome for nome in arquivo.namelist() if nome.lower().endswith(".csv")]
    if len(membros) != 1:
        raise IngestaoError(
            f"Esperava exatamente um CSV dentro de '{caminho_zip.name}' e encontrei "
            f"{len(membros)}. O zip pode ter mudado de formato — confira o conteúdo do "
            "arquivo baixado do portal do TSE."
        )
    return membros[0]


def _cabecalho_do_zip(caminho_zip: Path) -> list[str]:
    """Lê o cabeçalho do CSV direto do zip (sem extrair): colunas na ordem real."""
    with (
        zipfile.ZipFile(caminho_zip) as arquivo,
        arquivo.open(_membro_csv(caminho_zip)) as conteudo,
    ):
        primeira = conteudo.readline().decode("latin-1")
    return [campo.strip().strip('"').strip() for campo in primeira.split(";")]


def _verificar_contrato(caminho_zip: Path, obrigatorias: tuple[str, ...]) -> None:
    """Coluna obrigatória ausente → erro claro nomeando arquivo, coluna e o que fazer.

    Roda ANTES da ingestão de qualquer arquivo: sem contrato, não há escrita.
    """
    presentes = _cabecalho_do_zip(caminho_zip)
    ausentes = [coluna for coluna in obrigatorias if coluna not in presentes]
    if ausentes:
        raise IngestaoError(
            f"O arquivo '{caminho_zip.name}' do TSE não tem a(s) coluna(s) "
            f"obrigatória(s) do contrato de esquema: {', '.join(ausentes)}. O formato dos "
            "arquivos mudou (ex.: novo ano do pleito publicado). Compare o cabeçalho com "
            "o dicionário de dados do TSE (LEIA-ME dentro do próprio zip) e atualize o "
            "contrato em pipeline/ingest.py antes de prosseguir."
        )


def _extrair_csv(caminho_zip: Path, extraidos: Path) -> Path:
    """Extrai o CSV do zip para extraidos/<zip>/<csv>.csv (com cache)."""
    destino = extraidos / caminho_zip.stem / Path(_membro_csv(caminho_zip)).name
    if destino.is_file() and destino.stat().st_size > 0:
        return destino
    destino.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(caminho_zip) as arquivo,
        arquivo.open(_membro_csv(caminho_zip)) as origem,
        tempfile.NamedTemporaryFile(delete=False, dir=destino.parent, suffix=".part") as temporario,
    ):
        shutil.copyfileobj(origem, temporario)
    os.replace(temporario.name, destino)
    return destino


# ---------------------------------------------------------------------------
# Leitura como texto e tipagem explícita
# ---------------------------------------------------------------------------


def _carregar_bruto(con: duckdb.DuckDBPyConnection, vista: str, caminho_csv: Path) -> None:
    """Cria uma vista com TODAS as colunas como texto, no formato real do TSE."""
    caminho = str(caminho_csv).replace("'", "''")
    con.execute(
        f"CREATE OR REPLACE VIEW {vista} AS "
        f"SELECT * FROM read_csv('{caminho}', encoding='latin-1', delim=';', "
        "quote='\"', escape='\"', header=true, all_varchar=true)"
    )


_SQL_BU = """
CREATE TABLE bu AS
SELECT
    CAST(ANO_ELEICAO AS INTEGER)            AS ano_eleicao,
    CAST(NR_TURNO AS INTEGER)               AS turno,
    SG_UF                                   AS uf,
    CAST(CD_MUNICIPIO AS VARCHAR)           AS municipio_id,
    NM_MUNICIPIO                            AS municipio,
    CAST(NR_ZONA AS BIGINT)                 AS zona,
    CAST(NR_SECAO AS BIGINT)                AS secao,
    CAST(NR_LOCAL_VOTACAO AS BIGINT)        AS local_id,
    CAST(CD_ELEICAO AS INTEGER)             AS eleicao_id,
    CAST(CD_CARGO_PERGUNTA AS INTEGER)      AS cargo_id,
    DS_CARGO_PERGUNTA                       AS cargo,
    CAST(CD_TIPO_VOTAVEL AS INTEGER)        AS tipo_votavel_id,
    DS_TIPO_VOTAVEL                         AS tipo_votavel,
    CAST(NR_VOTAVEL AS BIGINT)              AS votavel,
    NM_VOTAVEL                              AS votavel_nome,
    TRY_CAST(QT_VOTOS AS BIGINT)            AS votos,
    TRY_CAST(QT_APTOS AS BIGINT)            AS aptos,
    TRY_CAST(QT_COMPARECIMENTO AS BIGINT)   AS comparecimento,
    TRY_CAST(QT_ABSTENCOES AS BIGINT)       AS abstencoes,
    CAST(NR_URNA_EFETIVADA AS BIGINT)       AS urna,
    NULLIF(DS_AGREGADAS, '#NULO#')          AS agregadas
FROM bu_bruto
WHERE CAST(NR_TURNO AS INTEGER) = ?
"""

_SQL_SECAO_TOTAL_UF = """
CREATE TABLE secao_total AS
SELECT
    CAST(NR_TURNO AS INTEGER)               AS turno,
    SG_UF                                   AS uf,
    CAST(CD_MUNICIPIO AS VARCHAR)           AS municipio_id,
    NM_MUNICIPIO                            AS municipio,
    CAST(NR_ZONA AS BIGINT)                 AS zona,
    CAST(NR_SECAO AS BIGINT)                AS secao,
    CAST(NR_LOCAL_VOTACAO AS BIGINT)        AS local_id,
    CAST(CD_CARGO AS INTEGER)               AS cargo_id,
    DS_CARGO                                AS cargo,
    CAST(NR_VOTAVEL AS BIGINT)              AS votavel,
    NM_VOTAVEL                              AS votavel_nome,
    TRY_CAST(SQ_CANDIDATO AS BIGINT)        AS candidato_sq,
    TRY_CAST(QT_VOTOS AS BIGINT)            AS votos,
    NM_LOCAL_VOTACAO                        AS local_votacao,
    DS_LOCAL_VOTACAO_ENDERECO               AS endereco_local
FROM tot_uf_bruto
WHERE CAST(NR_TURNO AS INTEGER) = ? AND CAST(CD_CARGO AS INTEGER) <> ?
"""

_SQL_SECAO_TOTAL_BR = """
INSERT INTO secao_total
SELECT
    CAST(NR_TURNO AS INTEGER)               AS turno,
    SG_UF                                   AS uf,
    CAST(CD_MUNICIPIO AS VARCHAR)           AS municipio_id,
    NM_MUNICIPIO                            AS municipio,
    CAST(NR_ZONA AS BIGINT)                 AS zona,
    CAST(NR_SECAO AS BIGINT)                AS secao,
    CAST(NR_LOCAL_VOTACAO AS BIGINT)        AS local_id,
    CAST(CD_CARGO AS INTEGER)               AS cargo_id,
    DS_CARGO                                AS cargo,
    CAST(NR_VOTAVEL AS BIGINT)              AS votavel,
    NM_VOTAVEL                              AS votavel_nome,
    TRY_CAST(SQ_CANDIDATO AS BIGINT)        AS candidato_sq,
    TRY_CAST(QT_VOTOS AS BIGINT)            AS votos,
    NM_LOCAL_VOTACAO                        AS local_votacao,
    DS_LOCAL_VOTACAO_ENDERECO               AS endereco_local
FROM tot_br_bruto
WHERE CAST(NR_TURNO AS INTEGER) = ? AND SG_UF = ? AND CAST(CD_CARGO AS INTEGER) = ?
"""

# aptos/comparecimento do BU repetem por votável e variam por cargo — o pleito
# do próprio cargo fica preservado na deduplicação por (seção, urna, cargo).
_SQL_PLEITO = """
CREATE TABLE pleito AS
SELECT DISTINCT
    uf, municipio_id, municipio, zona, secao, urna, cargo_id, cargo,
    aptos, comparecimento, abstencoes
FROM bu
"""

_SQL_LOCAIS = """
CREATE TABLE locais AS
SELECT DISTINCT
    CAST(NR_TURNO AS INTEGER)               AS turno,
    SG_UF                                   AS uf,
    CAST(CD_MUNICIPIO AS VARCHAR)           AS municipio_id,
    NM_MUNICIPIO                            AS municipio,
    CAST(NR_ZONA AS BIGINT)                 AS zona,
    CAST(NR_SECAO AS BIGINT)                AS secao,
    CAST(NR_LOCAL_VOTACAO AS BIGINT)        AS local_id,
    NM_LOCAL_VOTACAO                        AS local_votacao,
    DS_LOCAL_VOTACAO_ENDERECO               AS endereco_local
FROM tot_uf_bruto
WHERE CAST(NR_TURNO AS INTEGER) = ?
"""


def _contar(con: duckdb.DuckDBPyConnection, tabela: str) -> int:
    return int(con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0])


def ingerir(uf: str, turno: int, data_dir: Path) -> dict[str, int]:
    """Ingere os três CSVs do pleito no DuckDB tipado.

    Requer a etapa 'download' já executada (manifesto em data/raw/manifest.json).
    Devolve contagens de linhas e dimensões para o relatório do CLI. O banco é
    reconstruído do zero a cada execução (a ingestão é idempotente) e só substitui
    o arquivo final se tudo correr bem — sem ingestão parcial.
    """
    bruto = Path(data_dir) / "raw"
    extraidos = Path(data_dir) / "extracted"
    processados = Path(data_dir) / "processed"
    processados.mkdir(parents=True, exist_ok=True)

    zips = {papel: _caminho_zip(papel, bruto) for papel in PAPEIS_ESPERADOS}

    # Contrato de esquema dos três arquivos ANTES de qualquer escrita.
    _verificar_contrato(zips["boletim_de_urna"], OBRIGATORIAS_BU)
    _verificar_contrato(zips["totalizacao_uf"], OBRIGATORIAS_TOTALIZACAO)
    _verificar_contrato(zips["totalizacao_presidente"], OBRIGATORIAS_TOTALIZACAO)

    print("[ingest] Extraindo os CSVs dos zips (com cache)…")
    csv_bu = _extrair_csv(zips["boletim_de_urna"], extraidos)
    csv_tot_uf = _extrair_csv(zips["totalizacao_uf"], extraidos)
    csv_tot_br = _extrair_csv(zips["totalizacao_presidente"], extraidos)

    caminho_final = processados / "urna.duckdb"
    caminho_parcial = processados / "urna.duckdb.par"
    caminho_parcial.unlink(missing_ok=True)
    try:
        con = duckdb.connect(str(caminho_parcial))
        try:
            print(f"[ingest] Lendo o Boletim de Urna da UF {uf}…")
            _carregar_bruto(con, "bu_bruto", csv_bu)
            con.execute(_SQL_BU, [turno])

            print("[ingest] Lendo a totalização por seção da UF…")
            _carregar_bruto(con, "tot_uf_bruto", csv_tot_uf)
            con.execute(_SQL_SECAO_TOTAL_UF, [turno, CARGO_PRESIDENTE])

            print(f"[ingest] Lendo a totalização BR (Presidente, turno {turno}, UF {uf})…")
            _carregar_bruto(con, "tot_br_bruto", csv_tot_br)
            con.execute(_SQL_SECAO_TOTAL_BR, [turno, uf, CARGO_PRESIDENTE])

            con.execute(_SQL_PLEITO)
            con.execute(_SQL_LOCAIS, [turno])

            contagens = {
                "linhas_bu": _contar(con, "bu"),
                "linhas_secao_total": _contar(con, "secao_total"),
                "linhas_pleito": _contar(con, "pleito"),
                "linhas_locais": _contar(con, "locais"),
                "secoes": int(
                    con.execute(
                        "SELECT count(*) FROM (SELECT DISTINCT zona, secao FROM locais)"
                    ).fetchone()[0]
                ),
                "zonas": int(con.execute("SELECT count(DISTINCT zona) FROM locais").fetchone()[0]),
                "locais": int(
                    con.execute("SELECT count(DISTINCT local_id) FROM locais").fetchone()[0]
                ),
            }
            con.execute("DROP VIEW bu_bruto")
            con.execute("DROP VIEW tot_uf_bruto")
            con.execute("DROP VIEW tot_br_bruto")
        finally:
            con.close()
    except Exception:
        caminho_parcial.unlink(missing_ok=True)
        raise

    os.replace(caminho_parcial, caminho_final)
    print(
        f"[ingest] Banco em {caminho_final}: bu {contagens['linhas_bu']:,} linhas, "
        f"secao_total {contagens['linhas_secao_total']:,}, pleito "
        f"{contagens['linhas_pleito']:,}, locais {contagens['linhas_locais']:,}"
    )
    print(
        f"[ingest] Seções: {contagens['secoes']:,} | zonas: {contagens['zonas']} | "
        f"locais de votação: {contagens['locais']}"
    )
    return contagens
