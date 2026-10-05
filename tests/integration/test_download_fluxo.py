"""Fluxo completo de download contra um portal CKAN simulado em localhost.

Cobre: primeira execução com registro no manifesto, cache por checksum
(zero requisições na segunda passagem), verificação de SHA-512 quando o
portal publica, ausência de checksum não derruba o pipeline, indisponibilidade
falha com mensagem orientada e sem estado parcial.
"""

import hashlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.download import baixar_fontes
from pipeline.erros import DownloadError

CONTEUDO_BU = b"zip fake do boletim de urna" * 100
CONTEUDO_TOT_UF = b"zip fake da totalizacao DF" * 100
CONTEUDO_TOT_BR = b"zip fake da totalizacao BR" * 100
SHA512_BU = hashlib.sha512(CONTEUDO_BU).hexdigest()

ROTA_BU = "/api/3/action/package_show?id=resultados-2022-boletim-de-urna"
ROTA_TOT = "/api/3/action/package_show?id=resultados-2022"

RECURSOS = {
    ROTA_BU: [
        {
            "name": "DF - Boletim de Urna - Primeiro turno - 05.10.2022",
            "url": "{base}/arquivo/bweb_1t_DF_051020221321.zip",
        },
        {
            "name": "DF - Hash SHA2 - Primeiro turno - 05.10.2022",
            "url": "{base}/arquivo/bweb_1t_DF_051020221321.zip.sha512",
        },
    ],
    ROTA_TOT: [
        {
            "name": "Presidente - Votação por seção eleitoral - 2022",
            "url": "{base}/arquivo/votacao_secao_2022_BR.zip",
        },
        {
            "name": "DF - Votação por seção eleitoral - 2022",
            "url": "{base}/arquivo/votacao_secao_2022_DF.zip",
        },
    ],
}


class _PortalHandler(BaseHTTPRequestHandler):
    """Serve rotas rotas[ereco]; valor bytes → 200, int → erro HTTP."""

    rotas: dict[str, bytes | int] = {}
    requisicoes: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 (API do http.server)
        self.__class__.requisicoes.append(self.path)
        rota = self.__class__.rotas.get(self.path)
        if rota is None:
            self.send_error(404, "recurso ausente no portal simulado")
            return
        if isinstance(rota, int):
            self.send_error(rota)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(rota)))
        self.end_headers()
        self.wfile.write(rota)

    def log_message(self, *args: object) -> None:  # silencia o log de teste
        pass


@pytest.fixture
def portal(monkeypatch) -> Iterator[object]:
    rotas: dict[str, bytes | int] = {}
    handler = type("_Handler", (_PortalHandler,), {"rotas": rotas, "requisicoes": []})
    servidor: ThreadingHTTPServer | None = None
    for porta in range(3124, 3140):
        try:
            servidor = ThreadingHTTPServer(("127.0.0.1", porta), handler)
            break
        except OSError:
            continue
    assert servidor is not None, "não conseguiu reservar porta na faixa 3124–3139"
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{servidor.server_address[1]}"
    rotas[ROTA_BU] = json.dumps(
        {"success": True, "result": {"resources": _com_recursos(ROTA_BU, base)}}
    ).encode("utf-8")
    rotas[ROTA_TOT] = json.dumps(
        {"success": True, "result": {"resources": _com_recursos(ROTA_TOT, base)}}
    ).encode("utf-8")

    portal = SimpleNamespace(base=base, rotas=rotas, requisicoes=handler.requisicoes)
    yield portal
    servidor.shutdown()
    servidor.server_close()


def _com_recursos(rota: str, base: str) -> list[dict[str, str]]:
    return [{"name": r["name"], "url": r["url"].format(base=base)} for r in RECURSOS[rota]]


def _config(base: str) -> dict[str, object]:
    return {
        "fontes": {
            "ckan_base_url": base,
            "conjuntos": {
                "boletim_de_urna": "resultados-2022-boletim-de-urna",
                "totalizacao": "resultados-2022",
            },
        }
    }


def _preparar_portal_com_checksum(portal: object) -> None:
    portal.rotas["/arquivo/bweb_1t_DF_051020221321.zip"] = CONTEUDO_BU
    portal.rotas["/arquivo/bweb_1t_DF_051020221321.zip.sha512"] = (
        f"{SHA512_BU}  bweb_1t_DF_051020221321.zip\n".encode()
    )
    portal.rotas["/arquivo/votacao_secao_2022_DF.zip"] = CONTEUDO_TOT_UF
    portal.rotas["/arquivo/votacao_secao_2022_BR.zip"] = CONTEUDO_TOT_BR


def test_primeira_execucao_baixa_registra_manifesto_e_verifica_sha512(
    portal: object, tmp_path: Path
) -> None:
    _preparar_portal_com_checksum(portal)
    resumo = baixar_fontes(_config(portal.base), uf="DF", ano=2022, turno=1, data_dir=tmp_path)

    bruto = tmp_path / "raw"
    assert (bruto / "bweb_1t_DF_051020221321.zip").read_bytes() == CONTEUDO_BU
    manifesto = json.loads((bruto / "manifest.json").read_text(encoding="utf-8"))
    arquivos = manifesto["arquivos"]

    bu = arquivos["bweb_1t_DF_051020221321.zip"]
    assert bu["sha256"] == hashlib.sha256(CONTEUDO_BU).hexdigest()
    assert bu["tamanho_bytes"] == len(CONTEUDO_BU)
    assert bu["sha512_portal"] == SHA512_BU  # verificado e registrado
    # Sem checksum publicado no portal para a totalização — e isso não é erro.
    tot = arquivos["votacao_secao_2022_DF.zip"]
    assert tot["sha256"] == hashlib.sha256(CONTEUDO_TOT_UF).hexdigest()
    assert "sha512_portal" not in tot
    assert resumo["baixados"] == 3
    assert resumo["do_cache"] == 0


def test_segunda_execucao_usa_cache_sem_nenhuma_requisicao_de_arquivo(
    portal: object, tmp_path: Path
) -> None:
    _preparar_portal_com_checksum(portal)
    config = _config(portal.base)
    baixar_fontes(config, uf="DF", ano=2022, turno=1, data_dir=tmp_path)
    portal.requisicoes.clear()

    resumo = baixar_fontes(config, uf="DF", ano=2022, turno=1, data_dir=tmp_path)

    assert resumo["baixados"] == 0
    assert resumo["do_cache"] == 3
    # Só consulta os metadados; nenhum arquivo é retransmitido.
    assert all("/arquivo/" not in caminho for caminho in portal.requisicoes)


def test_arquivo_corrompido_no_cache_e_re_baixado(portal: object, tmp_path: Path) -> None:
    _preparar_portal_com_checksum(portal)
    config = _config(portal.base)
    baixar_fontes(config, uf="DF", ano=2022, turno=1, data_dir=tmp_path)

    bruto = tmp_path / "raw"
    (bruto / "votacao_secao_2022_DF.zip").write_bytes(b"conteudo corrompido")
    portal.requisicoes.clear()

    resumo = baixar_fontes(config, uf="DF", ano=2022, turno=1, data_dir=tmp_path)
    assert resumo["baixados"] == 1
    assert (bruto / "votacao_secao_2022_DF.zip").read_bytes() == CONTEUDO_TOT_UF


def test_checksum_do_portal_divergente_falha_sem_estado_parcial(
    portal: object, tmp_path: Path
) -> None:
    _preparar_portal_com_checksum(portal)
    portal.rotas["/arquivo/bweb_1t_DF_051020221321.zip.sha512"] = (
        f"{'0' * 128}  bweb_1t_DF_051020221321.zip\n".encode()
    )
    saida = tmp_path / "public" / "data"
    saida.mkdir(parents=True)
    marcador = saida / "index.json"
    marcador.write_text("{}", encoding="utf-8")

    with pytest.raises(DownloadError) as excinfo:
        baixar_fontes(_config(portal.base), uf="DF", ano=2022, turno=1, data_dir=tmp_path)

    mensagem = str(excinfo.value)
    assert "bweb_1t_DF_051020221321.zip" in mensagem  # nomeia o recurso
    assert "SHA-512" in mensagem
    bruto = tmp_path / "raw"
    assert not (bruto / "bweb_1t_DF_051020221321.zip").exists()  # nada parcial
    assert not list(bruto.glob("*.part"))
    assert marcador.read_text(encoding="utf-8") == "{}"  # saída intacta


def test_portal_indisponivel_falha_com_mensagem_orientada_e_sem_parcial(
    portal: object, tmp_path: Path
) -> None:
    _preparar_portal_com_checksum(portal)
    portal.rotas[ROTA_TOT] = 500
    saida = tmp_path / "public" / "data"
    saida.mkdir(parents=True)
    marcador = saida / "index.json"
    marcador.write_text("{}", encoding="utf-8")

    with pytest.raises(DownloadError) as excinfo:
        baixar_fontes(_config(portal.base), uf="DF", ano=2022, turno=1, data_dir=tmp_path)

    mensagem = str(excinfo.value)
    assert "resultados-2022" in mensagem  # nomeia o conjunto que falhou
    assert "conex" in mensagem.lower()  # orienta o próximo passo
    bruto = tmp_path / "raw"
    assert not list(bruto.glob("*.part"))
    assert marcador.read_text(encoding="utf-8") == "{}"


def test_arquivos_preexistentes_sem_manifesto_sao_registrados_sem_re_baixar(
    portal: object, tmp_path: Path
) -> None:
    """Estado da inspeção inicial: zips já em data/raw, manifesto ainda não existe."""
    _preparar_portal_com_checksum(portal)
    bruto = tmp_path / "raw"
    bruto.mkdir()
    (bruto / "votacao_secao_2022_DF.zip").write_bytes(CONTEUDO_TOT_UF)

    resumo = baixar_fontes(_config(portal.base), uf="DF", ano=2022, turno=1, data_dir=tmp_path)

    assert resumo["do_cache"] == 1
    manifesto = json.loads((bruto / "manifest.json").read_text(encoding="utf-8"))
    tot = manifesto["arquivos"]["votacao_secao_2022_DF.zip"]
    assert tot["sha256"] == hashlib.sha256(CONTEUDO_TOT_UF).hexdigest()
    # O BU (sem cópia local) foi baixado normalmente.
    assert (bruto / "bweb_1t_DF_051020221321.zip").read_bytes() == CONTEUDO_BU
