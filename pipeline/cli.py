"""Linha de comando do pipeline: orquestração das etapas (python -m pipeline)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pipeline.config import carregar_config
from pipeline.erros import ConfigError, PipelineError

ETAPAS = (
    "download",
    "ingest",
    "totais",
    "integridade",
    "divergencia",
    "cruzado",
    "ranking",
    "emitir",
)


def _criar_parser(config: dict[str, object]) -> argparse.ArgumentParser:
    pleito = config["pleito"]
    caminhos = config["caminhos"]
    assert isinstance(pleito, dict) and isinstance(caminhos, dict)
    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description=(
            "Triagem estatística de seções eleitorais atípicas com dados públicos do TSE. "
            "Limiares, pares e referências vêm do config.yaml."
        ),
    )
    parser.add_argument("--uf", default=pleito["uf"], help="UF analisada (padrão: %(default)s)")
    parser.add_argument(
        "--ano", type=int, default=pleito["ano"], help="Ano do pleito (padrão: %(default)s)"
    )
    parser.add_argument(
        "--turno", type=int, default=pleito["turno"], help="Turno do pleito (padrão: %(default)s)"
    )
    parser.add_argument(
        "--data-dir", default=caminhos["data_dir"], help="Pasta de dados (padrão: %(default)s)"
    )
    parser.add_argument(
        "--out-dir",
        default=caminhos["out_dir"],
        help="Pasta de saída dos JSONs (padrão: %(default)s)",
    )
    parser.add_argument(
        "--etapas",
        default="tudo",
        help="Etapas a executar, separadas por vírgula "
        f"(padrão: tudo; válidas: {','.join(ETAPAS)})",
    )
    return parser


def _resolver_etapas(parser: argparse.ArgumentParser, texto: str) -> list[str]:
    if texto.strip() == "tudo":
        return list(ETAPAS)
    pedidas = [parte.strip() for parte in texto.split(",") if parte.strip()]
    desconhecidas = [nome for nome in pedidas if nome not in ETAPAS]
    if desconhecidas:
        parser.error(
            f"etapa desconhecida: {', '.join(desconhecidas)} — etapas válidas: {','.join(ETAPAS)}"
        )
    return [nome for nome in ETAPAS if nome in pedidas]


def _executar(etapas: list[str], args: argparse.Namespace, config: dict[str, object]) -> None:
    for etapa in etapas:
        raise PipelineError(
            f"Etapa '{etapa}' ainda não implementada neste estágio do projeto — "
            "as próximas features do pipeline acrescentam download, ingestão, módulos, "
            "ranking e emissão."
        )


def main(argv: Sequence[str] | None = None, caminho_config: str | None = None) -> int:
    """Ponto de entrada do CLI; devolve o código de saída do processo."""
    try:
        config = carregar_config(None if caminho_config is None else caminho_config)
    except ConfigError as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1
    parser = _criar_parser(config)
    args = parser.parse_args(argv)
    etapas = _resolver_etapas(parser, args.etapas)
    try:
        _executar(etapas, args, config)
    except PipelineError as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 1
    return 0
