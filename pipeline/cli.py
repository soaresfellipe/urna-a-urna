"""Linha de comando do pipeline: orquestração das etapas (python -m pipeline)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pipeline.config import carregar_config
from pipeline.divergencia import conferir as conferir_divergencia
from pipeline.divergencia import relatar as relatar_divergencia
from pipeline.download import baixar_fontes
from pipeline.emitir import emitir as emitir_arquivos
from pipeline.emitir import relatar as relatar_emissao
from pipeline.erros import ConfigError, PipelineError
from pipeline.ingest import ingerir
from pipeline.integridade import conferir as conferir_integridade
from pipeline.integridade import relatar as relatar_integridade
from pipeline.totais import conferir as conferir_totais
from pipeline.totais import relatar as relatar_totais
from pipeline.totais import validar as validar_totais

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


def _executar_download(
    args: argparse.Namespace, config: dict[str, object], estado: dict[str, object]
) -> None:
    baixar_fontes(
        config,
        uf=str(args.uf),
        ano=int(args.ano),
        turno=int(args.turno),
        data_dir=Path(args.data_dir),
    )


def _executar_ingest(
    args: argparse.Namespace, config: dict[str, object], estado: dict[str, object]
) -> None:
    ingerir(uf=str(args.uf), turno=int(args.turno), data_dir=Path(args.data_dir))


def _executar_totais(
    args: argparse.Namespace, config: dict[str, object], estado: dict[str, object]
) -> None:
    relatorio = conferir_totais(
        uf=str(args.uf), turno=int(args.turno), data_dir=Path(args.data_dir)
    )
    relatar_totais(relatorio)
    validar_totais(relatorio)
    estado["relatorio_totais"] = relatorio


def _executar_integridade(
    args: argparse.Namespace, config: dict[str, object], estado: dict[str, object]
) -> None:
    resultado = conferir_integridade(
        uf=str(args.uf),
        turno=int(args.turno),
        ano=int(args.ano),
        data_dir=Path(args.data_dir),
        config=config,
    )
    relatar_integridade(resultado)
    estado["resultado_integridade"] = resultado


def _executar_divergencia(
    args: argparse.Namespace, config: dict[str, object], estado: dict[str, object]
) -> None:
    resultado = conferir_divergencia(
        uf=str(args.uf),
        turno=int(args.turno),
        ano=int(args.ano),
        data_dir=Path(args.data_dir),
        config=config,
    )
    relatar_divergencia(resultado)
    estado["resultado_divergencia"] = resultado


def _executar_emitir(
    args: argparse.Namespace, config: dict[str, object], estado: dict[str, object]
) -> None:
    # A emissão espelha a conferência de totais e os resultados dos módulos no
    # index.json; quando as etapas rodaram nesta mesma invocação os
    # resultados são reutilizados — os números do terminal e do JSON são
    # exatamente os mesmos.
    relatorio = estado.get("relatorio_totais")
    if relatorio is None:
        relatorio = conferir_totais(
            uf=str(args.uf), turno=int(args.turno), data_dir=Path(args.data_dir)
        )
        validar_totais(relatorio)
    resultado = estado.get("resultado_integridade")
    if resultado is None:
        resultado = conferir_integridade(
            uf=str(args.uf),
            turno=int(args.turno),
            ano=int(args.ano),
            data_dir=Path(args.data_dir),
            config=config,
        )
    resultado_div = estado.get("resultado_divergencia")
    if resultado_div is None:
        resultado_div = conferir_divergencia(
            uf=str(args.uf),
            turno=int(args.turno),
            ano=int(args.ano),
            data_dir=Path(args.data_dir),
            config=config,
        )
    resumo = emitir_arquivos(
        uf=str(args.uf),
        ano=int(args.ano),
        turno=int(args.turno),
        data_dir=Path(args.data_dir),
        out_dir=Path(args.out_dir),
        config=config,
        relatorio_totais=relatorio,
        resultado_integridade=resultado,
        resultado_divergencia=resultado_div,
    )
    relatar_emissao(resumo)


def _executar(etapas: list[str], args: argparse.Namespace, config: dict[str, object]) -> None:
    estado: dict[str, object] = {}
    acoes = {
        "download": _executar_download,
        "ingest": _executar_ingest,
        "totais": _executar_totais,
        "integridade": _executar_integridade,
        "divergencia": _executar_divergencia,
        "emitir": _executar_emitir,
    }
    for etapa in etapas:
        acao = acoes.get(etapa)
        if acao is None:
            raise PipelineError(
                f"Etapa '{etapa}' ainda não implementada neste estágio do projeto — "
                "as próximas features do pipeline acrescentam voto cruzado, ranking "
                "e o fechamento do encadeamento."
            )
        acao(args, config, estado)


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
