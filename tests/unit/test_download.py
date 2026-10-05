"""Testes da seleção de recursos CKAN por metadados e do manifesto em data/raw."""

import json
from pathlib import Path

import pytest

from pipeline.download import (
    carregar_manifesto,
    salvar_manifesto,
    selecionar_boletim_de_urna,
    selecionar_hash_boletim,
    selecionar_totalizacao_presidente,
    selecionar_totalizacao_uf,
)
from pipeline.erros import DownloadError

CDN = "https://cdn.tse.jus.br/exemplo"

RECURSOS_BU = [
    {
        "name": "AC - Boletim de Urna - Primeiro turno - 05.10.2022",
        "url": f"{CDN}/bweb_1t_AC_051020221321.zip",
    },
    {
        "name": "AC - Hash SHA2 - Primeiro turno - 05.10.2022",
        "url": f"{CDN}/bweb_1t_AC_051020221321.zip.sha512",
    },
    {
        "name": "DF - Boletim de Urna - Primeiro turno - 05.10.2022",
        "url": f"{CDN}/bweb_1t_DF_051020221321.zip",
    },
    {
        "name": "DF - Hash SHA2 - Primeiro turno - 05.10.2022",
        "url": f"{CDN}/bweb_1t_DF_051020221321.zip.sha512",
    },
    {
        "name": "DF - Boletim de Urna - Segundo turno - 31.10.2022",
        "url": f"{CDN}/bweb_2t_DF_311020221535.zip",
    },
    {
        "name": "DF - Hash SHA2 - Segundo turno - 31.10.2022",
        "url": f"{CDN}/bweb_2t_DF_311020221535.zip.sha512",
    },
]

RECURSOS_TOT = [
    {
        "name": "Presidente - Votação por seção eleitoral - 2022",
        "url": f"{CDN}/votacao_secao_2022_BR.zip",
    },
    {
        "name": "AC - Votação por seção eleitoral - 2022",
        "url": f"{CDN}/votacao_secao_2022_AC.zip",
    },
    {
        "name": "DF - Votação por seção eleitoral - 2022",
        "url": f"{CDN}/votacao_secao_2022_DF.zip",
    },
]


def test_seleciona_boletim_da_uf_e_turno_pelos_metadados():
    recurso = selecionar_boletim_de_urna(RECURSOS_BU, uf="DF", turno=1)
    assert recurso["url"] == f"{CDN}/bweb_1t_DF_051020221321.zip"
    assert "Segundo" not in recurso["name"]


def test_seleciona_boletim_do_segundo_turno_quando_pedido():
    recurso = selecionar_boletim_de_urna(RECURSOS_BU, uf="DF", turno=2)
    assert recurso["url"] == f"{CDN}/bweb_2t_DF_311020221535.zip"


def test_seleciona_hash_do_boletim_pelo_mesmo_turno():
    recurso = selecionar_hash_boletim(RECURSOS_BU, uf="DF", turno=1)
    assert recurso is not None
    assert recurso["url"] == f"{CDN}/bweb_1t_DF_051020221321.zip.sha512"


def test_hash_ausente_retorna_none_sem_erro():
    assert selecionar_hash_boletim(RECURSOS_TOT, uf="DF", turno=1) is None


def test_seleciona_totalizacao_da_uf():
    recurso = selecionar_totalizacao_uf(RECURSOS_TOT, uf="DF", ano=2022)
    assert recurso["url"] == f"{CDN}/votacao_secao_2022_DF.zip"


def test_seleciona_totalizacao_presidente_do_arquivo_br():
    recurso = selecionar_totalizacao_presidente(RECURSOS_TOT, ano=2022)
    assert recurso["url"] == f"{CDN}/votacao_secao_2022_BR.zip"


def test_recurso_ausente_falha_com_mensagem_orientada():
    with pytest.raises(DownloadError) as excinfo:
        selecionar_totalizacao_uf(RECURSOS_TOT, uf="ZZ", ano=2022, conjunto_id="resultados-2022")
    mensagem = str(excinfo.value)
    assert "resultados-2022" in mensagem  # nomeia o conjunto
    assert "ZZ" in mensagem  # nomeia o que foi procurado
    assert "portal" in mensagem.lower()


def test_recurso_ambiguo_falha_com_mensagem_orientada():
    duplicados = RECURSOS_TOT + [dict(RECURSOS_TOT[2])]
    with pytest.raises(DownloadError) as excinfo:
        selecionar_totalizacao_uf(duplicados, uf="DF", ano=2022, conjunto_id="resultados-2022")
    mensagem = str(excinfo.value)
    assert "Mais de um" in mensagem
    assert "DF" in mensagem


def test_manifesto_inexistente_le_vazio(tmp_path: Path):
    assert carregar_manifesto(tmp_path / "manifest.json") == {}


def test_manifesto_roundtrip_preserva_conteudo(tmp_path: Path):
    caminho = tmp_path / "manifest.json"
    entrada = {
        "arquivos": {
            "bweb_1t_DF_051020221321.zip": {
                "tamanho_bytes": 10,
                "sha256": "abc",
                "recurso": "DF - Boletim de Urna - Primeiro turno - 05.10.2022",
            }
        }
    }
    salvar_manifesto(caminho, entrada)
    assert carregar_manifesto(caminho) == entrada
    # JSON válido e parseável por terceiros (validadores usam jq).
    assert json.loads(caminho.read_text(encoding="utf-8"))["arquivos"]


def test_manifesto_corrompido_e_tratado_como_vazio(tmp_path: Path):
    caminho = tmp_path / "manifest.json"
    caminho.write_text("{não é json", encoding="utf-8")
    assert carregar_manifesto(caminho) == {}


def test_codigo_do_download_nao_presume_url_de_arquivo():
    """A descoberta é por metadados: nenhuma URL de arquivo do TSE fixa no código."""
    texto = Path("pipeline/download.py").read_text(encoding="utf-8")
    for padrao_proibido in ("cdn.tse.jus.br", "bweb_", "votacao_secao_2022", ".zip"):
        assert padrao_proibido not in texto, (
            f"pipeline/download.py contém o padrão fixo '{padrao_proibido}' — as URLs dos "
            "arquivos devem vir dos metadados da API CKAN, nunca do código"
        )
