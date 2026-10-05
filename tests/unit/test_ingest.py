"""Testes do contrato de esquema e da ingestão tipada em DuckDB.

Fixtures sintéticas fiéis ao esquema real: Latin-1, delimitador ';', todos os
campos entre aspas, colunas exatas dos arquivos do TSE (45 no BU, 26 na
totalização). Nenhum arquivo grande entra na suíte.
"""

import json
import zipfile
from pathlib import Path

import duckdb
import pytest

from pipeline.erros import IngestaoError
from pipeline.ingest import ingerir

COLUNAS_BU = [
    "DT_GERACAO",
    "HH_GERACAO",
    "ANO_ELEICAO",
    "CD_TIPO_ELEICAO",
    "NM_TIPO_ELEICAO",
    "CD_PLEITO",
    "DT_PLEITO",
    "NR_TURNO",
    "CD_ELEICAO",
    "DS_ELEICAO",
    "SG_UF",
    "CD_MUNICIPIO",
    "NM_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "NR_LOCAL_VOTACAO",
    "CD_CARGO_PERGUNTA",
    "DS_CARGO_PERGUNTA",
    "NR_PARTIDO",
    "SG_PARTIDO",
    "NM_PARTIDO",
    "DT_BU_RECEBIDO",
    "QT_APTOS",
    "QT_COMPARECIMENTO",
    "QT_ABSTENCOES",
    "CD_TIPO_URNA",
    "DS_TIPO_URNA",
    "CD_TIPO_VOTAVEL",
    "DS_TIPO_VOTAVEL",
    "NR_VOTAVEL",
    "NM_VOTAVEL",
    "QT_VOTOS",
    "NR_URNA_EFETIVADA",
    "CD_CARGA_1_URNA_EFETIVADA",
    "CD_CARGA_2_URNA_EFETIVADA",
    "CD_FLASHCARD_URNA_EFETIVADA",
    "DT_CARGA_URNA_EFETIVADA",
    "DS_CARGO_PERGUNTA_SECAO",
    "DS_AGREGADAS",
    "DT_ABERTURA",
    "DT_ENCERRAMENTO",
    "QT_ELEITORES_BIOMETRIA_NH",
    "DT_EMISSAO_BU",
    "NR_JUNTA_APURADORA",
    "NR_TURMA_APURADORA",
]

COLUNAS_TOT = [
    "DT_GERACAO",
    "HH_GERACAO",
    "ANO_ELEICAO",
    "CD_TIPO_ELEICAO",
    "NM_TIPO_ELEICAO",
    "NR_TURNO",
    "CD_ELEICAO",
    "DS_ELEICAO",
    "DT_ELEICAO",
    "TP_ABRANGENCIA",
    "SG_UF",
    "SG_UE",
    "NM_UE",
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
]

SECOES = [("1", "1", "1015"), ("1", "2", "1015"), ("2", "5", "2030")]
VOTAVEIS_PRESIDENTE = [
    ("1", "Nominal", "22", "JAIR BOLSONARO", "120"),
    ("1", "Nominal", "13", "LULA", "80"),
    ("2", "Branco", "95", "Branco", "18"),
    ("3", "Nulo", "96", "Nulo", "12"),
]
VOTAVEIS_GOVERNADOR = [
    ("1", "Nominal", "15", "IBANEIS ROCHA", "130"),
    ("1", "Nominal", "43", "LEANDRO GRASS", "60"),
    ("2", "Branco", "95", "Branco", "10"),
    ("3", "Nulo", "96", "Nulo", "9"),
]


def _linha(colunas: list[str], valores: dict[str, str]) -> list[str]:
    base = {coluna: "#NULO#" for coluna in colunas}
    base.update(valores)
    return [base[coluna] for coluna in colunas]


def _linhas_bu() -> list[list[str]]:
    linhas = []
    for zona, secao, local in SECOES:
        for tipo, ds_tipo, votavel, nome, votos in VOTAVEIS_PRESIDENTE:
            linhas.append(
                _linha(
                    COLUNAS_BU,
                    {
                        "ANO_ELEICAO": "2022",
                        "NR_TURNO": "1",
                        "CD_ELEICAO": "544",
                        "SG_UF": "DF",
                        "CD_MUNICIPIO": "97012",
                        "NM_MUNICIPIO": "BRASÍLIA",
                        "NR_ZONA": zona,
                        "NR_SECAO": secao,
                        "NR_LOCAL_VOTACAO": local,
                        "CD_CARGO_PERGUNTA": "1",
                        "DS_CARGO_PERGUNTA": "Presidente",
                        "QT_APTOS": "267",
                        "QT_COMPARECIMENTO": "230",
                        "QT_ABSTENCOES": "37",
                        "CD_TIPO_VOTAVEL": tipo,
                        "DS_TIPO_VOTAVEL": ds_tipo,
                        "NR_VOTAVEL": votavel,
                        "NM_VOTAVEL": nome,
                        "QT_VOTOS": votos,
                        "NR_URNA_EFETIVADA": f"222301{zona}",
                    },
                )
            )
        for tipo, ds_tipo, votavel, nome, votos in VOTAVEIS_GOVERNADOR:
            linhas.append(
                _linha(
                    COLUNAS_BU,
                    {
                        "ANO_ELEICAO": "2022",
                        "NR_TURNO": "1",
                        "CD_ELEICAO": "546",
                        "SG_UF": "DF",
                        "CD_MUNICIPIO": "97012",
                        "NM_MUNICIPIO": "BRASÍLIA",
                        "NR_ZONA": zona,
                        "NR_SECAO": secao,
                        "NR_LOCAL_VOTACAO": local,
                        "CD_CARGO_PERGUNTA": "3",
                        "DS_CARGO_PERGUNTA": "Governador",
                        "QT_APTOS": "259",
                        "QT_COMPARECIMENTO": "209",
                        "QT_ABSTENCOES": "50",
                        "CD_TIPO_VOTAVEL": tipo,
                        "DS_TIPO_VOTAVEL": ds_tipo,
                        "NR_VOTAVEL": votavel,
                        "NM_VOTAVEL": nome,
                        "QT_VOTOS": votos,
                        "NR_URNA_EFETIVADA": f"222301{zona}",
                    },
                )
            )
    return linhas


def _linhas_tot_uf() -> list[list[str]]:
    linhas = []
    for zona, secao, local in SECOES:
        for tipo, _ds_tipo, votavel, nome, votos in VOTAVEIS_GOVERNADOR:
            linhas.append(
                _linha(
                    COLUNAS_TOT,
                    {
                        "ANO_ELEICAO": "2022",
                        "NR_TURNO": "1",
                        "SG_UF": "DF",
                        "SG_UE": "DF",
                        "NM_UE": "BRASÍLIA",
                        "CD_MUNICIPIO": "97012",
                        "NM_MUNICIPIO": "BRASÍLIA",
                        "NR_ZONA": zona,
                        "NR_SECAO": secao,
                        "CD_CARGO": "3",
                        "DS_CARGO": "GOVERNADOR",
                        "NR_VOTAVEL": votavel,
                        "NM_VOTAVEL": nome,
                        "QT_VOTOS": votos,
                        "NR_LOCAL_VOTACAO": local,
                        "SQ_CANDIDATO": "2500" if tipo == "1" else "-1",
                        "NM_LOCAL_VOTACAO": "ESCOLA CLASSE 01",
                        "DS_LOCAL_VOTACAO_ENDERECO": "SQS 103 BLOCO A",
                    },
                )
            )
    return linhas


def _linhas_tot_br() -> list[list[str]]:
    linhas = []
    for zona, secao, local in SECOES:
        for tipo, _ds_tipo, votavel, nome, votos in VOTAVEIS_PRESIDENTE:
            linhas.append(
                _linha(
                    COLUNAS_TOT,
                    {
                        "ANO_ELEICAO": "2022",
                        "NR_TURNO": "1",
                        "SG_UF": "DF",
                        "SG_UE": "DF",
                        "NM_UE": "BRASÍLIA",
                        "CD_MUNICIPIO": "97012",
                        "NM_MUNICIPIO": "BRASÍLIA",
                        "NR_ZONA": zona,
                        "NR_SECAO": secao,
                        "CD_CARGO": "1",
                        "DS_CARGO": "PRESIDENTE",
                        "NR_VOTAVEL": votavel,
                        "NM_VOTAVEL": nome,
                        "QT_VOTOS": votos,
                        "NR_LOCAL_VOTACAO": local,
                        "SQ_CANDIDATO": "2500" if tipo == "1" else "-1",
                        "NM_LOCAL_VOTACAO": "ESCOLA CLASSE 01",
                        "DS_LOCAL_VOTACAO_ENDERECO": "SQS 103 BLOCO A",
                    },
                )
            )
    # Armadilhas reais que o filtro deve eliminar:
    linhas.append(
        _linha(
            COLUNAS_TOT,
            {  # 2º turno no arquivo BR
                "NR_TURNO": "2",
                "SG_UF": "DF",
                "NR_ZONA": "1",
                "NR_SECAO": "1",
                "CD_CARGO": "1",
                "DS_CARGO": "PRESIDENTE",
                "NR_VOTAVEL": "22",
                "NM_VOTAVEL": "JAIR BOLSONARO",
                "QT_VOTOS": "999",
                "NR_LOCAL_VOTACAO": "1015",
                "SQ_CANDIDATO": "2500",
                "NM_LOCAL_VOTACAO": "ESCOLA CLASSE 01",
            },
        )
    )
    linhas.append(
        _linha(
            COLUNAS_TOT,
            {  # outra UF no arquivo BR
                "NR_TURNO": "1",
                "SG_UF": "AC",
                "NR_ZONA": "1",
                "NR_SECAO": "1",
                "CD_CARGO": "1",
                "DS_CARGO": "PRESIDENTE",
                "NR_VOTAVEL": "22",
                "NM_VOTAVEL": "JAIR BOLSONARO",
                "QT_VOTOS": "888",
                "NR_LOCAL_VOTACAO": "1015",
                "SQ_CANDIDATO": "2500",
                "NM_LOCAL_VOTACAO": "ESCOLA CLASSE 01",
            },
        )
    )
    linhas.append(
        _linha(
            COLUNAS_TOT,
            {  # cargo que não é Presidente no arquivo BR
                "NR_TURNO": "1",
                "SG_UF": "DF",
                "NR_ZONA": "1",
                "NR_SECAO": "1",
                "CD_CARGO": "3",
                "DS_CARGO": "GOVERNADOR",
                "NR_VOTAVEL": "15",
                "NM_VOTAVEL": "IBANEIS ROCHA",
                "QT_VOTOS": "777",
                "NR_LOCAL_VOTACAO": "1015",
                "SQ_CANDIDATO": "2600",
                "NM_LOCAL_VOTACAO": "ESCOLA CLASSE 01",
            },
        )
    )
    return linhas


def _zip_csv(caminho_zip: Path, colunas: list[str], linhas: list[list[str]]) -> None:
    """Cria um zip com um CSV fiel ao formato do TSE (Latin-1, ';', aspas)."""
    conteudo = ";".join(f'"{coluna}"' for coluna in colunas) + "\r\n"
    for linha in linhas:
        conteudo += ";".join(f'"{valor}"' for valor in linha) + "\r\n"
    caminho_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(caminho_zip, "w") as arquivo:
        arquivo.writestr("conteudo.csv", conteudo.encode("latin-1"))


def _preparar_fontes(
    tmp_path: Path,
    colunas_bu: list[str] | None = None,
    linhas_bu: list[list[str]] | None = None,
    colunas_tot_uf: list[str] | None = None,
    colunas_tot_br: list[str] | None = None,
) -> Path:
    """Cria data/raw com os três zips e o manifesto (papel → arquivo)."""
    bruto = tmp_path / "raw"
    nomes = {
        "boletim_de_urna": "bweb_1t_DF_051020221321.zip",
        "totalizacao_uf": "votacao_secao_2022_DF.zip",
        "totalizacao_presidente": "votacao_secao_2022_BR.zip",
    }
    _zip_csv(
        bruto / nomes["boletim_de_urna"],
        colunas_bu or COLUNAS_BU,
        linhas_bu if linhas_bu is not None else _linhas_bu(),
    )
    _zip_csv(bruto / nomes["totalizacao_uf"], colunas_tot_uf or COLUNAS_TOT, _linhas_tot_uf())
    _zip_csv(
        bruto / nomes["totalizacao_presidente"], colunas_tot_br or COLUNAS_TOT, _linhas_tot_br()
    )
    manifesto = {
        "arquivos": {nome: {"papel": papel, "sha256": "x" * 64} for papel, nome in nomes.items()}
    }
    (bruto / "manifest.json").write_text(json.dumps(manifesto), encoding="utf-8")
    return tmp_path


def _tabelas(caminho_db: Path) -> list[str]:
    with duckdb.connect(str(caminho_db), read_only=True) as con:
        return [
            linha[0]
            for linha in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' ORDER BY table_name"
            ).fetchall()
        ]


def test_ingestao_cria_as_quatro_tabelas(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    ingerir(uf="DF", turno=1, data_dir=dados)
    caminho_db = dados / "processed" / "urna.duckdb"
    assert caminho_db.is_file()
    assert _tabelas(caminho_db) == ["bu", "locais", "pleito", "secao_total"]


def test_tipagem_explicita_das_tabelas(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    ingerir(uf="DF", turno=1, data_dir=dados)
    with duckdb.connect(str(dados / "processed" / "urna.duckdb"), read_only=True) as con:
        tipos_bu = dict(
            con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'bu'"
            ).fetchall()
        )
        tipos_tot = dict(
            con.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'secao_total'"
            ).fetchall()
        )
    assert tipos_bu["votos"] == "BIGINT"
    assert tipos_bu["zona"] == "BIGINT"
    assert tipos_bu["urna"] == "BIGINT"
    assert tipos_bu["uf"] == "VARCHAR"
    assert tipos_bu["municipio_id"] == "VARCHAR"
    assert tipos_tot["votos"] == "BIGINT"
    assert tipos_tot["candidato_sq"] == "BIGINT"


def test_filtros_de_turno_uf_e_cargo(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    contagens = ingerir(uf="DF", turno=1, data_dir=dados)
    with duckdb.connect(str(dados / "processed" / "urna.duckdb"), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM bu WHERE turno <> 1").fetchone()[0] == 0
        assert (
            con.execute(
                "SELECT count(*) FROM secao_total WHERE turno <> 1 OR uf <> 'DF'"
            ).fetchone()[0]
            == 0
        )
        # Presidente veio só do arquivo BR; Governador, só do arquivo da UF.
        assert con.execute("SELECT count(*) FROM secao_total WHERE cargo_id = 1").fetchone()[
            0
        ] == len(SECOES) * len(VOTAVEIS_PRESIDENTE)
        assert con.execute("SELECT count(*) FROM secao_total WHERE cargo_id = 3").fetchone()[
            0
        ] == len(SECOES) * len(VOTAVEIS_GOVERNADOR)
        # Os três registros-armadilha do arquivo BR não podem entrar.
        assert con.execute("SELECT count(*) FROM secao_total").fetchone()[0] == (
            len(SECOES) * (len(VOTAVEIS_PRESIDENTE) + len(VOTAVEIS_GOVERNADOR))
        )
    assert contagens["linhas_bu"] == len(SECOES) * (
        len(VOTAVEIS_PRESIDENTE) + len(VOTAVEIS_GOVERNADOR)
    )


def test_leitura_latin1_preserva_acentos_e_aspas(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    ingerir(uf="DF", turno=1, data_dir=dados)
    with duckdb.connect(str(dados / "processed" / "urna.duckdb"), read_only=True) as con:
        municipio = con.execute("SELECT DISTINCT municipio FROM bu").fetchall()
    assert municipio == [("BRASÍLIA",)]


def test_pleito_tem_uma_linha_por_secao_e_cargo(tmp_path: Path):
    """Aptos/comparecimento repetem por votável no BU e variam por cargo — a tabela
    pleito deduplica por (seção, cargo) preservando o pleito do próprio cargo."""
    dados = _preparar_fontes(tmp_path)
    ingerir(uf="DF", turno=1, data_dir=dados)
    with duckdb.connect(str(dados / "processed" / "urna.duckdb"), read_only=True) as con:
        linhas = con.execute(
            "SELECT zona, secao, cargo_id, aptos, comparecimento, abstencoes "
            "FROM pleito ORDER BY zona, secao, cargo_id"
        ).fetchall()
    assert len(linhas) == len(SECOES) * 2
    presidente = [linha for linha in linhas if linha[2] == 1]
    governador = [linha for linha in linhas if linha[2] == 3]
    assert all(linha[3:] == (267, 230, 37) for linha in presidente)
    assert all(linha[3:] == (259, 209, 50) for linha in governador)


def test_locais_tem_uma_linha_por_secao_com_nome_do_local(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    contagens = ingerir(uf="DF", turno=1, data_dir=dados)
    with duckdb.connect(str(dados / "processed" / "urna.duckdb"), read_only=True) as con:
        linhas = con.execute(
            "SELECT zona, secao, local_votacao FROM locais ORDER BY zona, secao"
        ).fetchall()
    assert len(linhas) == len(SECOES)
    assert contagens["secoes"] == len(SECOES)
    assert contagens["zonas"] == 2
    assert contagens["locais"] == 2


def test_re_ingestao_substitui_o_banco_sem_erro(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    primeira = ingerir(uf="DF", turno=1, data_dir=dados)
    segunda = ingerir(uf="DF", turno=1, data_dir=dados)
    assert primeira == segunda
    assert _tabelas(dados / "processed" / "urna.duckdb") == [
        "bu",
        "locais",
        "pleito",
        "secao_total",
    ]


@pytest.mark.parametrize(
    ("arquivo", "coluna_ausente", "chave_colunas"),
    [
        ("bweb_1t_DF_051020221321.zip", "QT_COMPARECIMENTO", "colunas_bu"),
        ("votacao_secao_2022_DF.zip", "SQ_CANDIDATO", "colunas_tot_uf"),
        ("votacao_secao_2022_BR.zip", "NR_TURNO", "colunas_tot_br"),
    ],
)
def test_coluna_obrigatoria_ausente_falha_sem_ingestao_parcial(
    tmp_path: Path, arquivo: str, coluna_ausente: str, chave_colunas: str
):
    """Contrato de esquema: coluna ausente → erro que nomeia o arquivo e a coluna,
    orienta o que fazer, e não deixa banco parcial para trás."""
    colunas_danificadas = {
        "colunas_bu": COLUNAS_BU,
        "colunas_tot_uf": COLUNAS_TOT,
        "colunas_tot_br": COLUNAS_TOT,
    }
    colunas_danificadas[chave_colunas] = [
        c for c in colunas_danificadas[chave_colunas] if c != coluna_ausente
    ]
    dados = _preparar_fontes(tmp_path, **colunas_danificadas)
    with pytest.raises(IngestaoError) as excinfo:
        ingerir(uf="DF", turno=1, data_dir=dados)
    mensagem = str(excinfo.value)
    assert arquivo in mensagem  # nomeia o arquivo lido
    assert coluna_ausente in mensagem  # nomeia a coluna obrigatória ausente
    assert "dicionário" in mensagem.lower()  # orienta: conferir o LEIA-ME do TSE
    assert not (dados / "processed" / "urna.duckdb").exists()  # sem meia-ingestão


def test_manifesto_ausente_falha_de_forma_orientada(tmp_path: Path):
    (tmp_path / "raw").mkdir(parents=True)
    with pytest.raises(IngestaoError) as excinfo:
        ingerir(uf="DF", turno=1, data_dir=tmp_path)
    mensagem = str(excinfo.value)
    assert "manifest.json" in mensagem
    assert "download" in mensagem.lower()


def test_zip_sem_csv_falha_de_forma_orientada(tmp_path: Path):
    dados = _preparar_fontes(tmp_path)
    zip_bu = dados / "raw" / "bweb_1t_DF_051020221321.zip"
    with zipfile.ZipFile(zip_bu, "w") as arquivo:
        arquivo.writestr("apenas-leia-me.pdf", b"%PDF-1.4")
    with pytest.raises(IngestaoError) as excinfo:
        ingerir(uf="DF", turno=1, data_dir=dados)
    assert "bweb_1t_DF_051020221321.zip" in str(excinfo.value)
