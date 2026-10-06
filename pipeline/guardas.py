"""Guardas de publicação (VAL-DADOS-005; architecture.md §4.8).

Três guardas de limite, todas com os valores de ``config.yaml`` (bloco
``publicacao``) e mensagem que identifica a violação:

1. **Tamanho** — nenhum arquivo em ``public/data`` maior que 25 MiB;
2. **Total** — o total de arquivos emitidos é menor que 20 mil;
3. **Dados brutos** — nada sob ``data/`` trackeado no git (``git ls-files``).

As guardas 1 e 2 rodam ao fim da emissão (``executar``, chamado por
``pipeline/emitir.py``) e como testes automatizados; a guarda 3 roda como
teste (unitária/integração) e pode ser executada sobre qualquer raiz de
repositório. Qualquer violação levanta ``GuardaError`` com o caminho e o
tamanho do arquivo ofensor.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pipeline.erros import GuardaError

MIB = 1024 * 1024

# Defaults de emergência: refletem os limites da missão; o config.yaml é a
# fonte de verdade e o bloco 'publicacao' os define explicitamente.
_TAMANHO_MAX_MB_PADRAO = 25.0
_TOTAL_MAX_ARQUIVOS_PADRAO = 20000


def _limites(config: dict[str, object]) -> tuple[float, int]:
    publicacao = config.get("publicacao") or {}
    if not isinstance(publicacao, dict):
        publicacao = {}
    tamanho = publicacao.get("tamanho_max_mb", _TAMANHO_MAX_MB_PADRAO)
    total = publicacao.get("total_max_arquivos", _TOTAL_MAX_ARQUIVOS_PADRAO)
    return float(tamanho), int(total)


def arquivos_publicados(pasta: Path) -> list[Path]:
    """Todos os arquivos sob pasta (recursivo), em ordem estável."""
    if not pasta.is_dir():
        return []
    return sorted(caminho for caminho in pasta.rglob("*") if caminho.is_file())


def violacoes_de_tamanho(pasta: Path, limite_mb: float) -> list[str]:
    """Arquivos acima do limite, com caminho relativo e tamanho na mensagem."""
    limite_bytes = limite_mb * MIB
    violacoes: list[str] = []
    for caminho in arquivos_publicados(pasta):
        tamanho = caminho.stat().st_size
        if tamanho > limite_bytes:
            relativo = caminho.relative_to(pasta)
            violacoes.append(
                f"{relativo}: {tamanho / MIB:.2f} MiB excede o limite de {limite_mb:g} MiB"
            )
    return violacoes


def violacoes_de_total(pasta: Path, total_max: int) -> list[str]:
    """Contagem de arquivos igual ou acima do total máximo permitido."""
    total = len(arquivos_publicados(pasta))
    if total >= total_max:
        return [f"{pasta}: {total} arquivos emitidos (limite: menos que {total_max})"]
    return []


def rastreados_em_data(raiz: Path) -> list[str]:
    """Caminhos sob data/ trackeados no índice do git da raiz informada."""
    processo = subprocess.run(
        ["git", "-C", str(raiz), "ls-files", "--", "data"],
        capture_output=True,
        text=True,
        check=False,
    )
    if processo.returncode != 0:
        detalhe = processo.stderr.strip() or f"código {processo.returncode}"
        raise GuardaError(f"git ls-files falhou em {raiz}: {detalhe}")
    return [linha for linha in processo.stdout.splitlines() if linha.strip()]


def violacoes_dados_brutos(raiz: Path) -> list[str]:
    """Um caminho por arquivo bruto trackeado — dado bruto nunca entra no git."""
    return [
        f"{caminho} está trackeado no git (dado bruto nunca é commitado)"
        for caminho in rastreados_em_data(raiz)
    ]


def executar(
    pasta_publica: Path, config: dict[str, object], *, raiz: Path | None = None
) -> dict[str, object]:
    """Roda as guardas sobre pasta_publica e devolve o resumo dos limites.

    Com ``raiz`` informada, inclui a guarda de dados brutos no git. Qualquer
    violação levanta ``GuardaError`` listando todas as ocorrências.
    """
    limite_mb, total_max = _limites(config)
    violacoes = [
        *violacoes_de_tamanho(pasta_publica, limite_mb),
        *violacoes_de_total(pasta_publica, total_max),
        *(violacoes_dados_brutos(raiz) if raiz is not None else []),
    ]
    if violacoes:
        raise GuardaError("Guardas de publicação violadas:\n  - " + "\n  - ".join(violacoes))
    return {
        "pasta": str(pasta_publica),
        "arquivos": len(arquivos_publicados(pasta_publica)),
        "tamanho_max_mb": limite_mb,
        "total_max_arquivos": total_max,
    }
