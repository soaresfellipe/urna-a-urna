"""Download das fontes do TSE: descoberta pela API CKAN, checksums e cache.

A descoberta é sempre por metadados: o código consulta `package_show` dos
conjuntos do ano (ids vindos do config.yaml) e seleciona os recursos pelos
nomes publicados — nenhuma URL de arquivo é presumida ou fixada aqui.

Comportamento:
- Verifica o SHA-512 quando o portal publica (conjunto de Boletim de Urna);
  a ausência de checksum publicado não é erro — o SHA-256 calculado é
  registrado em data/raw/manifest.json de qualquer forma.
- Cache por checksum: arquivo presente e SHA-256 conferindo com o manifesto
  → nada é baixado de novo.
- Falha de download → DownloadError com mensagem que nomeia o conjunto/recurso
  e orienta o próximo passo; nada é escrito parcialmente (arquivo em `.part`
  promovido por renomeação atômica).
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.erros import DownloadError

_USUARIO = "urna-a-urna/0.1 (pipeline de triagem estatística; dados públicos TSE)"
_TIMEOUT_API = 60
_TIMEOUT_ARQUIVO = 300
_LIMIAR_TURNO = {1: "primeiro turno", 2: "segundo turno"}


# ---------------------------------------------------------------------------
# Seleção por metadados
# ---------------------------------------------------------------------------


def _normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, sem pontuação — para comparar nomes publicados."""
    sem_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", sem_acentos.casefold()).strip()


def _unico(
    achados: list[dict[str, Any]],
    descricao: str,
    conjunto_id: str,
    criterio: str,
) -> dict[str, Any]:
    if len(achados) == 1:
        return achados[0]
    if not achados:
        raise DownloadError(
            f"Recurso não encontrado no conjunto '{conjunto_id}' do portal do TSE: {descricao} "
            f"(critério: '{criterio}'). Confira o conjunto no portal "
            "(https://dadosabertos.tse.jus.br) e rode o pipeline novamente — o cache "
            "preserva o que já está íntegro."
        )
    nomes = ", ".join(str(recurso.get("name", "?")) for recurso in achados)
    raise DownloadError(
        f"Mais de um recurso corresponde a {descricao} no conjunto '{conjunto_id}' "
        f"({nomes}). A seleção por metadados ficou ambígua — confira o conjunto no portal."
    )


def selecionar_boletim_de_urna(
    recursos: list[dict[str, Any]], uf: str, turno: int, conjunto_id: str = ""
) -> dict[str, Any]:
    """Recurso do Boletim de Urna da UF no turno, pelo nome publicado no portal."""
    criterio = _normalizar(f"{uf} boletim de urna {_LIMIAR_TURNO[turno]}")
    achados = [
        recurso
        for recurso in recursos
        if _normalizar(str(recurso.get("name", ""))).startswith(criterio)
    ]
    return _unico(
        achados, f"o Boletim de Urna da UF {uf} ({_LIMIAR_TURNO[turno]})", conjunto_id, criterio
    )


def selecionar_hash_boletim(
    recursos: list[dict[str, Any]], uf: str, turno: int
) -> dict[str, Any] | None:
    """Arquivo de checksum SHA-512 do Boletim de Urna; None quando o portal não publica."""
    criterio = _normalizar(f"{uf} hash")
    turno_extenso = _normalizar(_LIMIAR_TURNO[turno])
    for recurso in recursos:
        nome = _normalizar(str(recurso.get("name", "")))
        if nome.startswith(criterio) and turno_extenso in nome:
            return recurso
    return None


def _selecionar_totalizacao(
    recursos: list[dict[str, Any]], prefixo: str, descricao: str, ano: int, conjunto_id: str
) -> dict[str, Any]:
    criterio = f"{prefixo} votacao por secao eleitoral"
    achados = [
        recurso
        for recurso in recursos
        if _normalizar(str(recurso.get("name", ""))).startswith(criterio)
        and str(ano) in _normalizar(str(recurso.get("name", "")))
    ]
    return _unico(achados, descricao, conjunto_id, criterio)


def selecionar_totalizacao_uf(
    recursos: list[dict[str, Any]], uf: str, ano: int, conjunto_id: str = ""
) -> dict[str, Any]:
    """Recurso de votação por seção da UF (Governador e demais cargos estaduais)."""
    return _selecionar_totalizacao(
        recursos, _normalizar(uf), f"a votação por seção da UF {uf}", ano, conjunto_id
    )


def selecionar_totalizacao_presidente(
    recursos: list[dict[str, Any]], ano: int, conjunto_id: str = ""
) -> dict[str, Any]:
    """Recurso nacional (BR) de votação por seção — único com Presidente por seção."""
    return _selecionar_totalizacao(
        recursos, "presidente", "a votação por seção do arquivo BR (Presidente)", ano, conjunto_id
    )


# ---------------------------------------------------------------------------
# Checksums e cache
# ---------------------------------------------------------------------------


def _sha_de_arquivo(caminho: Path, algoritmo: str) -> str:
    digest = hashlib.new(algoritmo)
    with caminho.open("rb") as conteudo:
        for bloco in iter(lambda: conteudo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def carregar_manifesto(caminho: Path) -> dict[str, Any]:
    """Lê data/raw/manifest.json; manifesto ausente ou corrompido lê como vazio."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def salvar_manifesto(caminho: Path, manifesto: dict[str, Any]) -> None:
    """Escreve o manifesto de forma atômica (temporário + renomeação)."""
    manifesto["gerado_em"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporario = caminho.with_suffix(".json.part")
    temporario.write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporario.replace(caminho)


def _nome_do_arquivo(url: str) -> str:
    caminho = urllib.parse.urlparse(url).path
    nome = Path(caminho).name
    if not nome:
        raise DownloadError(
            f"Recurso do portal do TSE sem nome de arquivo na URL ({url!r}) — "
            "confira os metadados do conjunto."
        )
    return urllib.parse.unquote(nome)


def _cache_valido(caminho: Path, entrada: dict[str, Any] | None) -> bool:
    """Cache confere quando tamanho e SHA-256 do arquivo batem com o manifesto."""
    if entrada is None or not caminho.is_file():
        return False
    return caminho.stat().st_size == entrada.get("tamanho_bytes") and _sha_de_arquivo(
        caminho, "sha256"
    ) == entrada.get("sha256")


def _transferir(url: str, destino: Path) -> tuple[int, str, str]:
    """Baixa `url` para `destino` em streaming; devolve (tamanho, sha256, sha512).

    Escreve em arquivo temporário `.part` e promove por renomeação atômica;
    em qualquer falha o temporário é removido (nunca fica estado parcial).
    """
    parte = destino.with_suffix(destino.suffix + ".part")
    sha256, sha512 = hashlib.sha256(), hashlib.sha512()
    try:
        with (
            urllib.request.urlopen(  # noqa: S310 (base vem do config.yaml do dono)
                urllib.request.Request(url, headers={"User-Agent": _USUARIO}),
                timeout=_TIMEOUT_ARQUIVO,
            ) as resposta,
            parte.open("wb") as saida,
        ):
            while bloco := resposta.read(1024 * 1024):
                saida.write(bloco)
                sha256.update(bloco)
                sha512.update(bloco)
        tamanho = parte.stat().st_size
        parte.replace(destino)
    except (urllib.error.URLError, OSError) as erro:
        parte.unlink(missing_ok=True)
        raise DownloadError(
            f"Falha ao baixar '{_nome_do_arquivo(url)}' do portal do TSE ({url}): {erro}. "
            "Verifique a conexão e rode o pipeline novamente — os arquivos já íntegros "
            "no cache são preservados."
        ) from erro
    return tamanho, sha256.hexdigest(), sha512.hexdigest()


def _sha512_publicado(recurso: dict[str, Any] | None, bruto: Path) -> str | None:
    """Baixa e interpreta o arquivo .sha512 do portal; None quando não publicado."""
    if recurso is None:
        return None
    url_hash = str(recurso.get("url", ""))
    if not url_hash:
        return None
    caminho_hash = bruto / _nome_do_arquivo(url_hash)
    _transferir(url_hash, caminho_hash)
    hex_sha512 = caminho_hash.read_text(encoding="latin-1").split()[0].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{128}", hex_sha512):
        raise DownloadError(
            f"O arquivo de checksum do portal '{caminho_hash.name}' não contém um SHA-512 "
            "reconhecível — confira o recurso de hash no conjunto do Boletim de Urna."
        )
    return hex_sha512


# ---------------------------------------------------------------------------
# API CKAN e orquestração
# ---------------------------------------------------------------------------


def _consultar_conjunto(base_url: str, conjunto_id: str) -> list[dict[str, Any]]:
    """package_show do conjunto; devolve a lista de resources dos metadados."""
    url = f"{base_url}/api/3/action/package_show?id={urllib.parse.quote(conjunto_id)}"
    try:
        with urllib.request.urlopen(  # noqa: S310 (base vem do config.yaml do dono)
            urllib.request.Request(url, headers={"User-Agent": _USUARIO}), timeout=_TIMEOUT_API
        ) as resposta:
            corpo = json.loads(resposta.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as erro:
        raise DownloadError(
            f"Não foi possível consultar o conjunto '{conjunto_id}' na API CKAN do portal "
            f"do TSE ({base_url}): {erro}. Verifique a conexão e rode o pipeline novamente — "
            "os arquivos já íntegros no cache são preservados."
        ) from erro
    resultado = corpo.get("result") if isinstance(corpo, dict) else None
    recursos = resultado.get("resources") if isinstance(resultado, dict) else None
    if not isinstance(recursos, list):
        raise DownloadError(
            f"A API CKAN do portal do TSE não devolveu os recursos do conjunto "
            f"'{conjunto_id}'. Verifique a disponibilidade do portal e rode novamente."
        )
    return [recurso for recurso in recursos if isinstance(recurso, dict)]


def _descrever(tamanho: int) -> str:
    return f"{tamanho / (1024 * 1024):.1f} MB"


def baixar_fontes(
    config: dict[str, object], uf: str, ano: int, turno: int, data_dir: Path
) -> dict[str, int]:
    """Descobre, baixa (com cache) e registra as três fontes do pleito.

    Devolve {'baixados': n, 'do_cache': n} e escreve data/raw/manifest.json com
    nome, tamanho e SHA-256 de cada arquivo (e o SHA-512 do portal, quando publicado).
    """
    fontes = config["fontes"]
    assert isinstance(fontes, dict)
    base_url = str(fontes["ckan_base_url"]).rstrip("/")
    conjuntos = fontes["conjuntos"]
    assert isinstance(conjuntos, dict)
    conjunto_bu = str(conjuntos["boletim_de_urna"]).format(ano=ano)
    conjunto_tot = str(conjuntos["totalizacao"]).format(ano=ano)

    bruto = Path(data_dir) / "raw"
    bruto.mkdir(parents=True, exist_ok=True)
    caminho_manifesto = bruto / "manifest.json"
    manifesto = carregar_manifesto(caminho_manifesto)
    arquivos = dict(manifesto.get("arquivos", {}))
    contador = {"baixados": 0, "do_cache": 0}

    def registrar(nome: str, entrada: dict[str, Any]) -> None:
        arquivos[nome] = entrada

    def garantir(
        recurso: dict[str, Any], conjunto_id: str, papel: str, hash_recurso: dict[str, Any] | None
    ) -> None:
        """Baixa o recurso (ou aceita o cache), verificando o checksum do portal."""
        nome = _nome_do_arquivo(str(recurso["url"]))
        caminho = bruto / nome
        entrada = arquivos.get(nome)
        if isinstance(entrada, dict) and _cache_valido(caminho, entrada):
            contador["do_cache"] += 1
            print(f"  [cache] {nome} ({_descrever(caminho.stat().st_size)}; checksum confere)")
            return
        # O .sha512 do portal só é consultado quando o arquivo precisa de verificação.
        sha512_portal = _sha512_publicado(hash_recurso, bruto) if hash_recurso is not None else None
        if caminho.is_file() and not isinstance(entrada, dict):
            if sha512_portal is not None and _sha_de_arquivo(caminho, "sha512") != sha512_portal:
                caminho.unlink()  # cópia local não confere com o portal → re-baixa abaixo
            else:
                # Arquivo presente sem registro no manifesto (ex.: inspeção inicial):
                # registra o checksum calculado na primeira verificação, sem retransmitir.
                registrar(
                    nome,
                    {
                        "papel": papel,
                        "recurso": str(recurso.get("name", "")),
                        "conjunto": conjunto_id,
                        "url": str(recurso["url"]),
                        "tamanho_bytes": caminho.stat().st_size,
                        "sha256": _sha_de_arquivo(caminho, "sha256"),
                        "checksum_portal_verificado": sha512_portal is not None,
                        **({"sha512_portal": sha512_portal} if sha512_portal else {}),
                    },
                )
                contador["do_cache"] += 1
                print(
                    f"  [cache] {nome} ({_descrever(caminho.stat().st_size)}; "
                    "registrado no manifesto)"
                )
                return
        tamanho, sha256, sha512 = _transferir(str(recurso["url"]), caminho)
        if sha512_portal is not None and sha512 != sha512_portal:
            caminho.unlink(missing_ok=True)
            raise DownloadError(
                f"O arquivo '{nome}' baixado do portal do TSE não confere com o SHA-512 "
                f"publicado (esperado {sha512_portal[:16]}…). Rode o pipeline novamente — "
                "se o erro persistir, confira o recurso no portal do TSE."
            )
        registrar(
            nome,
            {
                "papel": papel,
                "recurso": str(recurso.get("name", "")),
                "conjunto": conjunto_id,
                "url": str(recurso["url"]),
                "tamanho_bytes": tamanho,
                "sha256": sha256,
                "checksum_portal_verificado": sha512_portal is not None,
                **({"sha512_portal": sha512_portal} if sha512_portal else {}),
            },
        )
        contador["baixados"] += 1
        print(f"  [novo] {nome} ({_descrever(tamanho)}; sha256 registrado)")

    # Conjunto do Boletim de Urna: o zip da UF + o checksum publicado pelo portal.
    print(f"[download] Conjunto '{conjunto_bu}': descobrindo recursos na API CKAN…")
    recursos_bu = _consultar_conjunto(base_url, conjunto_bu)
    boletim = selecionar_boletim_de_urna(recursos_bu, uf=uf, turno=turno, conjunto_id=conjunto_bu)
    hash_boletim = selecionar_hash_boletim(recursos_bu, uf=uf, turno=turno)
    garantir(boletim, conjunto_bu, "boletim_de_urna", hash_boletim)
    if hash_boletim is not None:
        nome_hash = _nome_do_arquivo(str(hash_boletim["url"]))
        caminho_hash = bruto / nome_hash
        if caminho_hash.is_file():
            registrar(
                nome_hash,
                {
                    "papel": "hash_boletim_de_urna",
                    "recurso": str(hash_boletim.get("name", "")),
                    "conjunto": conjunto_bu,
                    "url": str(hash_boletim["url"]),
                    "tamanho_bytes": caminho_hash.stat().st_size,
                    "sha256": _sha_de_arquivo(caminho_hash, "sha256"),
                },
            )

    # Conjunto de totalização: votação por seção da UF + BR (Presidente só no BR).
    print(f"[download] Conjunto '{conjunto_tot}': descobrindo recursos na API CKAN…")
    recursos_tot = _consultar_conjunto(base_url, conjunto_tot)
    totalizacao_uf = selecionar_totalizacao_uf(
        recursos_tot, uf=uf, ano=ano, conjunto_id=conjunto_tot
    )
    garantir(totalizacao_uf, conjunto_tot, "totalizacao_uf", None)
    totalizacao_presidente = selecionar_totalizacao_presidente(
        recursos_tot, ano=ano, conjunto_id=conjunto_tot
    )
    garantir(totalizacao_presidente, conjunto_tot, "totalizacao_presidente", None)

    manifesto["arquivos"] = arquivos
    salvar_manifesto(caminho_manifesto, manifesto)
    print(
        f"[download] {contador['baixados']} arquivo(s) baixado(s), "
        f"{contador['do_cache']} do cache — manifesto em {caminho_manifesto}"
    )
    return contador
