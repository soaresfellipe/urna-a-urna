"""Integração do particionamento da emissão (architecture.md §4.8).

Quando ``sinalizacoes.json`` excede o ``particao.tamanho_max_mb`` do config,
o emissor quebra a lista ranqueada por zona em ``sinalizacoes/{zona}.json`` e
o ``index.json`` lista os arquivos no manifesto. Nos testes, o limite mínimo
do config (1 MiB não basta para a fixture, então usa-se 0 via monkeypatch do
dicionário passado à emissão) força o caminho particionado sem arquivos
grandes; a emissão real do DF permanece em arquivo único.
"""

from __future__ import annotations

import copy

from pipeline import guardas
from pipeline.config import carregar_config
from tests.fixtures import gerador
from tests.integration.test_emitir_fixtures import _emitir_na_fixture, _ler_json


def _config_particionado() -> dict:
    """Limite 0 MiB força o particionamento (a fixture emite poucos KB).

    O config passado à emissão não passa pela validação do CLI (que exige
    >= 1 MiB); o objetivo do teste é exercitar o caminho particionado sem
    arquivos grandes na suíte.
    """
    config = copy.deepcopy(carregar_config())
    config["particao"]["tamanho_max_mb"] = 0
    config["particao"]["sinalizacoes"] = "por_zona"
    return config


def _pleito_com_achados() -> gerador.PleitoSintetico:
    """Pleito com achados nas duas zonas (lista ranqueada não vazia).

    Como em test_emitir_fixtures, as duas divergências R3 se compensam:
    a conferência GLOBAL de totais segue com diferença 0.
    """
    pleito = gerador.pleito_padrao()
    gerador.injetar_r1(
        pleito, zona=1, secao=2, cargo_id=gerador.CARGO_PRESIDENTE, comparecimento=248
    )
    gerador.injetar_r3(
        pleito,
        zona=2,
        secao=40,
        cargo_id=gerador.CARGO_PRESIDENTE,
        votavel=22,
        votos_totalizacao=118,
    )
    gerador.injetar_r3(
        pleito,
        zona=1,
        secao=12,
        cargo_id=gerador.CARGO_PRESIDENTE,
        votavel=22,
        votos_totalizacao=122,
    )
    return pleito


def test_acima_do_limite_particiona_por_zona(tmp_path):
    resumo = _emitir_na_fixture(tmp_path, _pleito_com_achados(), config=_config_particionado())
    saida = tmp_path / "public" / "data"
    particionados = sorted(p.name for p in (saida / "sinalizacoes").glob("*.json"))
    assert particionados == ["1.json", "2.json"]
    assert not (saida / "sinalizacoes.json").exists()
    assert "sinalizacoes/1.json" in resumo["arquivos"]
    assert "sinalizacoes/2.json" in resumo["arquivos"]


def test_manifesto_do_index_lista_os_arquivos_particionados(tmp_path):
    _emitir_na_fixture(tmp_path, _pleito_com_achados(), config=_config_particionado())
    indice = _ler_json(tmp_path / "public" / "data" / "index.json")
    manifesto = {a["caminho"] for a in indice["manifesto_arquivos"]}
    assert manifesto == {
        "index.json",
        "sinalizacoes/1.json",
        "sinalizacoes/2.json",
        "zonas/1.json",
        "zonas/2.json",
    }
    for entrada in indice["manifesto_arquivos"]:
        if entrada["caminho"] == "index.json":
            continue
        caminho = tmp_path / "public" / "data" / entrada["caminho"]
        assert caminho.stat().st_size == entrada["tamanho_bytes"]


def test_partes_particionadas_conteem_as_mesmas_sinalizacoes(tmp_path):
    # A mesma emissão em arquivo único serve de espelho do conteúdo.
    unica = _emitir_na_fixture(tmp_path / "unico", _pleito_com_achados())
    particionada = _emitir_na_fixture(
        tmp_path / "partes", _pleito_com_achados(), config=_config_particionado()
    )
    assert unica["contagens"] == particionada["contagens"]
    lista_unica = _ler_json(tmp_path / "unico" / "public" / "data" / "sinalizacoes.json")
    todas = []
    for zona in (1, 2):
        parte = _ler_json(tmp_path / "partes" / "public" / "data" / "sinalizacoes" / f"{zona}.json")
        assert parte["zona"] == zona
        todas.extend(parte["sinalizacoes"])
    assert todas == lista_unica["sinalizacoes"]


def test_emissao_particionada_passa_nas_guardas(tmp_path):
    _emitir_na_fixture(tmp_path, _pleito_com_achados(), config=_config_particionado())
    guardas.executar(
        tmp_path / "public" / "data",
        {
            "publicacao": {
                "tamanho_max_mb": 25,
                "total_max_arquivos": 20000,
            }
        },
    )
