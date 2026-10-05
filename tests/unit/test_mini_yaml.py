"""Testes do leitor do subconjunto de YAML usado no config.yaml."""

import pytest

from pipeline.mini_yaml import YamlError, loads


def test_mapeamento_aninhado():
    doc = loads('a:\n  b:\n    c: 1\n    d: "texto"\n')
    assert doc == {"a": {"b": {"c": 1, "d": "texto"}}}


def test_escalares_tipados():
    doc = loads("i: 42\nf: 0.01\nb: true\nn: null\ns: DF\n")
    assert doc == {"i": 42, "f": 0.01, "b": True, "n": None, "s": "DF"}


def test_lista_de_escalares_no_mesmo_nivel_da_chave():
    doc = loads("etapas:\n- um\n- dois\n")
    assert doc == {"etapas": ["um", "dois"]}


def test_lista_de_escalares_indentada():
    doc = loads("etapas:\n  - um\n  - dois\n")
    assert doc == {"etapas": ["um", "dois"]}


def test_lista_de_mapeamentos_com_aninhamento():
    doc = loads(
        "pares:\n"
        '  - rotulo: "A (22)"\n'
        "    presidente:\n"
        "      numero: 22\n"
        '      nome: "A"\n'
        "    governador:\n"
        "      numero: 15\n"
        '  - rotulo: "B (13)"\n'
        "    presidente:\n"
        "      numero: 13\n"
    )
    assert doc == {
        "pares": [
            {
                "rotulo": "A (22)",
                "presidente": {"numero": 22, "nome": "A"},
                "governador": {"numero": 15},
            },
            {"rotulo": "B (13)", "presidente": {"numero": 13}},
        ]
    }


def test_comentarios_linha_inteira_e_final():
    doc = loads('# cabeçalho\nchave: valor # explicação\noutra: "com # cerquilha"\n')
    assert doc == {"chave": "valor", "outra": "com # cerquilha"}


def test_valor_com_dois_pontos_na_url():
    doc = loads("url: https://dadosabertos.tse.jus.br\n")
    assert doc == {"url": "https://dadosabertos.tse.jus.br"}


def test_chave_ano_como_texto():
    doc = loads("multiplicador:\n  2022:\n    senador: 1\n")
    assert doc == {"multiplicador": {"2022": {"senador": 1}}}


def test_indentacao_invalida_falha():
    with pytest.raises(YamlError):
        loads("a:\n    b: 1\n  c: 2\n")


def test_duas_chaves_iguais_falha():
    with pytest.raises(YamlError):
        loads("a: 1\na: 2\n")
