"""Testes de validação do config.yaml (única fonte de limiares, pares e referências)."""

import copy

import pytest

from pipeline.config import (
    RAIZ,
    carregar_config,
    multiplicador_por_eleitor,
    validar_config,
)
from pipeline.erros import ConfigError


@pytest.fixture
def config_valida():
    return copy.deepcopy(carregar_config())


def test_config_do_repositorio_carrega_e_valida():
    config = carregar_config()

    limiares = config["limiares"]["divergencia"]
    assert limiares["z_robusto_min"] == 5.0
    assert limiares["z_tamanho_min"] == 6.0
    assert limiares["dif_minima_pp"] == 8.0
    assert limiares["fdr_alpha"] == 0.01
    assert limiares["min_secoes_local"] == 6
    assert limiares["min_secoes_zona"] == 30
    assert limiares["min_votos_validos_secao"] == 30

    cruzado = config["limiares"]["cruzado"]
    assert cruzado["z_robusto_min"] == 5.0
    assert cruzado["dif_minima_pp"] == 8.0

    assert config["pleito"]["uf"] == "DF"
    assert config["pleito"]["ano"] == 2022
    assert config["pleito"]["turno"] == 1


def test_multiplicador_2022_senador_e_1(config_valida):
    assert multiplicador_por_eleitor(config_valida, 2022, "senador") == 1


def test_multiplicador_desconhecido_usa_padrao_1(config_valida):
    assert multiplicador_por_eleitor(config_valida, 2022, "presidente") == 1
    assert multiplicador_por_eleitor(config_valida, 2018, "senador") == 1


def test_pares_aprovados_do_voto_cruzado(config_valida):
    pares = config_valida["voto_cruzado"]["pares"]
    numeros = {(par["presidente"]["numero"], par["governador"]["numero"]) for par in pares}
    assert numeros == {(22, 15), (13, 43)}
    assert all(par["rotulo"] for par in pares)


def test_nivel_de_referencia_municipio_com_fallback_zona(config_valida):
    assert config_valida["referencia"]["nivel"] == "municipio"
    assert config_valida["referencia"]["fallback"] == "zona"


def test_fontes_ckan_presentes(config_valida):
    assert config_valida["fontes"]["ckan_base_url"].startswith("http")
    conjuntos = config_valida["fontes"]["conjuntos"]
    assert "{ano}" in conjuntos["boletim_de_urna"]
    assert "{ano}" in conjuntos["totalizacao"]


def test_chave_ausente_falha_com_mensagem_que_nomeia_o_caminho(config_valida):
    del config_valida["limiares"]["divergencia"]["z_robusto_min"]
    with pytest.raises(ConfigError, match="z_robusto_min"):
        validar_config(config_valida)


def test_secao_inteira_ausente_falha(config_valida):
    del config_valida["limiares"]["cruzado"]
    with pytest.raises(ConfigError, match="limiares.cruzado"):
        validar_config(config_valida)


def test_tipo_errado_em_limiar_falha(config_valida):
    config_valida["limiares"]["divergencia"]["dif_minima_pp"] = "8,0"
    with pytest.raises(ConfigError, match="dif_minima_pp"):
        validar_config(config_valida)


def test_tipo_errado_em_ano_falha(config_valida):
    config_valida["pleito"]["ano"] = "2022"
    with pytest.raises(ConfigError, match="pleito.ano"):
        validar_config(config_valida)


def test_par_sem_numero_falha(config_valida):
    del config_valida["voto_cruzado"]["pares"][0]["governador"]["numero"]
    with pytest.raises(ConfigError, match="n[úu]mero"):
        validar_config(config_valida)


def test_par_sem_rotulo_falha(config_valida):
    del config_valida["voto_cruzado"]["pares"][1]["rotulo"]
    with pytest.raises(ConfigError, match="rotulo"):
        validar_config(config_valida)


def test_fdr_alpha_fora_da_faixa_falha(config_valida):
    config_valida["limiares"]["cruzado"]["fdr_alpha"] = 1.5
    with pytest.raises(ConfigError, match="fdr_alpha"):
        validar_config(config_valida)


def test_lista_de_pares_vazia_falha(config_valida):
    config_valida["voto_cruzado"]["pares"] = []
    with pytest.raises(ConfigError, match="pares"):
        validar_config(config_valida)


def test_arquivo_inexistente_falha_com_mensagem_clara(tmp_path):
    with pytest.raises(ConfigError, match="config"):
        carregar_config(tmp_path / "nao_existe.yaml")


def test_raiz_do_projeto_aponta_para_o_config(tmp_path=None):
    assert (RAIZ / "config.yaml").exists()
