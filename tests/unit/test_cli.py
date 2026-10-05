"""Testes do CLI: opções, defaults vindos do config e tratamento de erros."""

import pytest

from pipeline.cli import ETAPAS, _criar_parser, main
from pipeline.config import carregar_config


def test_help_exibe_todas_as_opcoes(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    saida = capsys.readouterr().out
    for opcao in ("--uf", "--ano", "--turno", "--data-dir", "--out-dir", "--etapas"):
        assert opcao in saida


def test_defaults_vindos_do_config():
    config = carregar_config()
    parser = _criar_parser(config)
    args = parser.parse_args([])
    assert args.uf == config["pleito"]["uf"]
    assert args.ano == config["pleito"]["ano"]
    assert args.turno == config["pleito"]["turno"]
    assert args.data_dir == config["caminhos"]["data_dir"]
    assert args.out_dir == config["caminhos"]["out_dir"]


def test_etapas_padrao_e_tudo():
    parser = _criar_parser(carregar_config())
    args = parser.parse_args([])
    assert args.etapas == "tudo"


def test_etapa_desconhecida_rejeitada_com_codigo_nao_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--etapas", "inexistente"])
    assert excinfo.value.code != 0
    erro = capsys.readouterr().err
    assert "inexistente" in erro
    assert "download" in erro  # nomeia as etapas válidas


def test_config_invalido_falha_com_codigo_nao_zero_e_mensagem_clara(tmp_path, capsys):
    caminho = tmp_path / "config.yaml"
    caminho.write_text("pleito:\n  uf: DF\n", encoding="utf-8")
    codigo = main([], caminho_config=caminho)
    assert codigo != 0
    erro = capsys.readouterr().err
    assert "config.yaml" in erro
    assert "ausente" in erro


def test_etapa_nao_implementada_falha_de_forma_orientada(capsys):
    # 'divergencia' já existe; a etapa ainda não implementada é o voto cruzado.
    codigo = main(["--etapas", "cruzado"])
    assert codigo != 0
    erro = capsys.readouterr().err
    assert "cruzado" in erro
    assert "implementada" in erro


def test_etapa_integridade_sem_banco_falha_de_forma_orientada(tmp_path, capsys):
    codigo = main(["--etapas", "integridade", "--data-dir", str(tmp_path)])
    assert codigo != 0
    erro = capsys.readouterr().err
    assert "ingest" in erro
    assert "integridade" in erro


def test_etapa_totais_sem_banco_falha_de_forma_orientada(tmp_path, capsys):
    codigo = main(["--etapas", "totais", "--data-dir", str(tmp_path)])
    assert codigo != 0
    erro = capsys.readouterr().err
    assert "ingest" in erro
    assert "totais" in erro


def test_lista_de_etapas_conhece_o_ciclo_completo():
    assert ETAPAS == (
        "download",
        "ingest",
        "totais",
        "integridade",
        "divergencia",
        "cruzado",
        "ranking",
        "emitir",
    )
