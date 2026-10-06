"""Integração do encadeamento completo (architecture.md §4.1).

Um portal CKAN simulado serve os zips do pleito sintético nos mesmos recursos
do portal real do TSE; um config.yaml idêntico ao da raiz (apenas o
ckan_base_url apontando para o portal simulado) faz o CLI executar, em UMA
invocação e sem --etapas (o padrão 'tudo'): download → ingest → totais →
integridade → divergência → cruzado → ranking → emissão. A re-execução com o
cache populado não retransmite nenhum arquivo e reemite o mesmo conteúdo,
exceto a data de geração do index.json (VAL-DADOS-001 e VAL-DADOS-006).
"""

import hashlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline import RAIZ
from pipeline.cli import main
from tests.fixtures import gerador

ROTA_BU = "/api/3/action/package_show?id=resultados-2022-boletim-de-urna"
ROTA_TOT = "/api/3/action/package_show?id=resultados-2022"

# Nomes reais dos arquivos no portal (os seletores casam por metadados).
NOMES_POR_PAPEL = {
    "boletim_de_urna": "bweb_1t_DF_051020221321.zip",
    "totalizacao_uf": "votacao_secao_2022_DF.zip",
    "totalizacao_presidente": "votacao_secao_2022_BR.zip",
}
HASH_BU = NOMES_POR_PAPEL["boletim_de_urna"] + ".sha512"

RECURSOS = {
    ROTA_BU: [
        {
            "name": "DF - Boletim de Urna - Primeiro turno - 05.10.2022",
            "url": "{base}/arquivo/" + NOMES_POR_PAPEL["boletim_de_urna"],
        },
        {
            "name": "DF - Hash SHA2 - Primeiro turno - 05.10.2022",
            "url": "{base}/arquivo/" + HASH_BU,
        },
    ],
    ROTA_TOT: [
        {
            "name": "Presidente - Votação por seção eleitoral - 2022",
            "url": "{base}/arquivo/" + NOMES_POR_PAPEL["totalizacao_presidente"],
        },
        {
            "name": "DF - Votação por seção eleitoral - 2022",
            "url": "{base}/arquivo/" + NOMES_POR_PAPEL["totalizacao_uf"],
        },
    ],
}


class _PortalHandler(BaseHTTPRequestHandler):
    """Serve rotas[rota]; valor bytes → 200, int → erro HTTP."""

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
def portal(tmp_path_factory) -> Iterator[SimpleNamespace]:
    """Portal CKAN simulado servindo os zips do pleito padrão do gerador."""
    pasta = tmp_path_factory.mktemp("portal-ckan")
    caminhos = gerador.empacotar(pasta, gerador.pleito_padrao())
    zips = {papel: caminho.read_bytes() for papel, caminho in caminhos.items()}

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

    for rota, recursos in RECURSOS.items():
        lista = [{"name": r["name"], "url": r["url"].format(base=base)} for r in recursos]
        rotas[rota] = json.dumps({"success": True, "result": {"resources": lista}}).encode("utf-8")
    for papel, nome in NOMES_POR_PAPEL.items():
        rotas[f"/arquivo/{nome}"] = zips[papel]
    rotas[f"/arquivo/{HASH_BU}"] = (
        f"{hashlib.sha512(zips['boletim_de_urna']).hexdigest()}  "
        f"{NOMES_POR_PAPEL['boletim_de_urna']}\n"
    ).encode()

    # config.yaml idêntico ao da raiz, só o portal de dados apontando para o simulado.
    texto = (RAIZ / "config.yaml").read_text(encoding="utf-8")
    assert "https://dadosabertos.tse.jus.br" in texto
    caminho_config = pasta / "config.yaml"
    caminho_config.write_text(
        texto.replace("https://dadosabertos.tse.jus.br", base), encoding="utf-8"
    )

    ambiente = SimpleNamespace(
        base=base, rotas=rotas, requisicoes=handler.requisicoes, caminho_config=caminho_config
    )
    yield ambiente
    servidor.shutdown()
    servidor.server_close()


def _rodar(ambiente: SimpleNamespace, tmp_path: Path, capsys) -> tuple[int, str]:
    """Uma invocação do CLI SEM --etapas (o padrão 'tudo' roda o ciclo completo)."""
    saida = tmp_path / "public" / "data"
    codigo = main(
        [
            "--uf",
            "DF",
            "--ano",
            "2022",
            "--turno",
            "1",
            "--data-dir",
            str(tmp_path),
            "--out-dir",
            str(saida),
        ],
        caminho_config=ambiente.caminho_config,
    )
    capturado = capsys.readouterr()
    return codigo, capturado.out + capturado.err


def _conteudo(saida: Path) -> dict[str, str]:
    """Conteúdo dos JSONs emitidos, exceto a data de geração (idempotência)."""
    conteudo = {}
    for caminho in sorted(saida.rglob("*.json")):
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        dados.pop("data_geracao", None)
        conteudo[str(caminho.relative_to(saida))] = json.dumps(dados, sort_keys=True)
    return conteudo


def test_ciclo_completo_em_uma_unica_invocacao(portal, tmp_path, capsys):
    codigo, texto = _rodar(portal, tmp_path, capsys)
    assert codigo == 0, texto

    # Cada etapa relata o próprio progresso no terminal.
    for marcador in (
        "[download]",
        "[ingest]",
        "[totais]",
        "[integridade]",
        "[divergencia]",
        "[cruzado]",
        "[ranking]",
        "[emissao]",
    ):
        assert marcador in texto, f"faltou o relato da etapa {marcador}"

    # Conferência de totais com diferença 0 e contagens de todos os módulos.
    assert "Diferença total: 0" in texto
    saida = tmp_path / "public" / "data"
    indice = json.loads((saida / "index.json").read_text(encoding="utf-8"))
    sinalizacoes = json.loads((saida / "sinalizacoes.json").read_text(encoding="utf-8"))
    for modulo in ("integridade", "divergencia", "cruzado"):
        contagem = indice["contagens"][modulo]
        assert "secoes_sinalizadas" in contagem
        assert "achados" in contagem
        assert "percentual_sinalizado" in contagem
    conferencia = indice["conferencia_totais"]
    assert conferencia["consistente"] is True
    assert conferencia["diferenca"] == 0

    # Pleito íntegro: nenhuma sinalização; arquivos de zona completos.
    assert sinalizacoes["sinalizacoes"] == []
    assert sorted(p.name for p in (saida / "zonas").glob("*.json")) == ["1.json", "2.json"]
    assert indice["total_secoes"] == 4


def test_reexecucao_usa_o_cache_e_reemite_o_mesmo_conteudo(portal, tmp_path, capsys):
    codigo1, texto1 = _rodar(portal, tmp_path, capsys)
    assert codigo1 == 0, texto1
    primeira = _conteudo(tmp_path / "public" / "data")
    assert len(primeira) == 4  # index, sinalizacoes e duas zonas
    portal.requisicoes.clear()

    codigo2, texto2 = _rodar(portal, tmp_path, capsys)
    assert codigo2 == 0, texto2

    # Nenhuma retransmissão: o cache por checksum atende a todas as fontes.
    assert "0 arquivo(s) baixado(s)" in texto2
    assert not [rota for rota in portal.requisicoes if "/arquivo/" in rota]
    assert _conteudo(tmp_path / "public" / "data") == primeira  # exceto data_geracao
