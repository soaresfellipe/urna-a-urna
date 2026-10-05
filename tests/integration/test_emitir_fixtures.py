"""Integração da emissão com fixtures sintéticas (architecture.md §4.8).

O mini-pleito é empacotado e ingerido pelo caminho REAL do pipeline; a
emissão grava public/data com sinalizacoes.json (ranqueado, agrupado por
seção), zonas/{zona}.json (números por seção para a página de detalhe) e
index.json (espelho dos limiares do config, conferência de totais, contagens
por módulo, checksums das fontes e manifesto dos arquivos emitidos).
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from pipeline import RAIZ, emitir, ingest, integridade, totais
from pipeline.config import carregar_config
from tests.fixtures import gerador


def _emitir_na_fixture(pasta: Path, pleito: gerador.PleitoSintetico, config=None) -> dict:
    gerador.empacotar(pasta, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=pasta)
    relatorio_totais = totais.conferir(uf="DF", turno=1, data_dir=pasta)
    resultado = integridade.conferir(
        uf="DF", turno=1, ano=2022, data_dir=pasta, config=carregar_config()
    )
    saida = pasta / "public" / "data"
    return emitir.emitir(
        uf="DF",
        ano=2022,
        turno=1,
        data_dir=pasta,
        out_dir=saida,
        config=config or carregar_config(),
        relatorio_totais=relatorio_totais,
        resultado_integridade=resultado,
    )


def _ler_json(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def test_pleito_integro_emite_os_tres_arquivos(tmp_path):
    resumo = _emitir_na_fixture(tmp_path, gerador.pleito_padrao())
    saida = tmp_path / "public" / "data"
    assert (saida / "index.json").is_file()
    assert (saida / "sinalizacoes.json").is_file()
    assert sorted(p.name for p in (saida / "zonas").glob("*.json")) == ["1.json", "2.json"]
    assert resumo["arquivos"] == [
        "index.json",
        "sinalizacoes.json",
        "zonas/1.json",
        "zonas/2.json",
    ]
    alvos = [
        saida / "index.json",
        saida / "sinalizacoes.json",
        *sorted((saida / "zonas").glob("*.json")),
    ]
    for caminho in alvos:
        json.loads(caminho.read_text(encoding="utf-8"))  # todos parseáveis


def test_contagens_do_modulo_1_no_index_em_pleito_integro(tmp_path):
    resumo = _emitir_na_fixture(tmp_path, gerador.pleito_padrao())
    indice = _ler_json(tmp_path / "public" / "data" / "index.json")
    contagens = indice["contagens"]["integridade"]
    assert contagens["secoes_sinalizadas"] == 0
    assert contagens["achados"] == 0
    assert contagens["secoes_analisadas"] == 4
    assert contagens["percentual_sinalizado"] == 0.0
    assert contagens["por_regra"] == {"R1": 0, "R2": 0, "R3": 0}
    assert resumo["contagens"]["integridade"] == contagens
    assert indice["total_secoes"] == 4


def test_pleito_integro_nao_tem_sinalizacoes(tmp_path):
    _emitir_na_fixture(tmp_path, gerador.pleito_padrao())
    sinalizacoes = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")
    assert sinalizacoes["sinalizacoes"] == []
    assert sinalizacoes["total_secoes"] == 4


def test_index_espelha_os_limiares_do_config(tmp_path):
    config = copy.deepcopy(carregar_config())
    config["limiares"]["divergencia"]["z_robusto_min"] = 9.9
    _emitir_na_fixture(tmp_path, gerador.pleito_padrao(), config=config)
    indice = _ler_json(tmp_path / "public" / "data" / "index.json")
    config_real = carregar_config()
    assert indice["limiares"] == config["limiares"]  # espelho, não do real
    assert indice["limiares"]["divergencia"]["z_robusto_min"] == 9.9
    assert indice["limiares"] != config_real["limiares"]
    assert indice["limiares"]["divergencia"]["min_votos_validos_secao"] == 30
    assert indice["referencia"] == config["referencia"]
    assert indice["voto_cruzado"] == config["voto_cruzado"]
    assert indice["multiplicador_votos_por_eleitor"] == config["multiplicador_votos_por_eleitor"]


def test_index_traz_a_conferencia_de_totais_e_os_checksums(tmp_path):
    _emitir_na_fixture(tmp_path, gerador.pleito_padrao())
    indice = _ler_json(tmp_path / "public" / "data" / "index.json")
    conferencia = indice["conferencia_totais"]
    assert conferencia["consistente"] is True
    assert conferencia["diferenca"] == 0
    manifesto_bruto = json.loads((tmp_path / "raw" / "manifest.json").read_text(encoding="utf-8"))
    checksums = {c["papel"]: c["sha256"] for c in indice["checksums_fontes"]}
    brutos = {
        entrada["papel"]: entrada["sha256"] for entrada in manifesto_bruto["arquivos"].values()
    }
    assert checksums == brutos


def test_manifesto_lista_todo_arquivo_emitido(tmp_path):
    _emitir_na_fixture(tmp_path, gerador.pleito_padrao())
    indice = _ler_json(tmp_path / "public" / "data" / "index.json")
    manifesto = {a["caminho"] for a in indice["manifesto_arquivos"]}
    saida = tmp_path / "public" / "data"
    esperados = {"index.json", "sinalizacoes.json", "zonas/1.json", "zonas/2.json"}
    assert manifesto == esperados
    sinalizacoes_entrada = next(
        a for a in indice["manifesto_arquivos"] if a["caminho"] == "sinalizacoes.json"
    )
    assert sinalizacoes_entrada["tamanho_bytes"] == (saida / "sinalizacoes.json").stat().st_size
    assert len(sinalizacoes_entrada["sha256"]) == 64


def test_zona_json_traz_os_numeros_por_secao(tmp_path):
    _emitir_na_fixture(tmp_path, gerador.pleito_padrao())
    zona1 = _ler_json(tmp_path / "public" / "data" / "zonas" / "1.json")
    assert zona1["zona"] == 1
    assert zona1["municipio_id"] == "97012"
    assert zona1["municipio"] == "BRASÍLIA"
    por_secao = {s["secao"]: s for s in zona1["secoes"]}
    assert set(por_secao) == {12, 2, 30}
    secao12 = por_secao[12]
    assert secao12["local"] == "ESCOLA CLASSE CONCEIÇÃO DE MACEDO"
    cargos = {c["cargo_id"]: c for c in secao12["cargos"]}
    presidente = cargos[gerador.CARGO_PRESIDENTE]
    assert presidente["cargo"] == "Presidente"
    assert presidente["aptos"] == 267
    assert presidente["comparecimento"] == 250
    assert presidente["validos"] == 230
    assert presidente["brancos"] == 8
    assert presidente["nulos"] == 12
    votaveis = {v["votavel"]: v for v in presidente["votaveis"]}
    assert votaveis[22]["votos"] == 120
    assert votaveis[22]["votos_totalizacao"] == 120
    assert votaveis[22]["percentual_validos"] == 52.17  # 120/230, 2 casas
    assert votaveis[95]["nome"] == "VOTO BRANCO"  # nome da totalização
    assert votaveis[96]["nome"] == "VOTO NULO"
    assert presidente["integridade"]["achados"] == []
    governador = cargos[gerador.CARGO_GOVERNADOR]
    assert governador["comparecimento"] == 209  # pleito do PRÓPRIO cargo
    assert governador["validos"] == 190


def test_zona_json_registra_excecoes_e_achados_da_secao(tmp_path):
    # A divergência R3 é local: uma seção perde 2 votos do votável 22 e outra
    # compensa — a conferência GLOBAL de totais segue com diferença 0 (a
    # emissão falha quando os totais globais não batem).
    pleito = gerador.pleito_padrao()
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
    _emitir_na_fixture(tmp_path, pleito)
    zona2 = _ler_json(tmp_path / "public" / "data" / "zonas" / "2.json")
    secao40 = next(s for s in zona2["secoes"] if s["secao"] == 40)
    cargos = {c["cargo_id"]: c for c in secao40["cargos"]}
    presidente = cargos[gerador.CARGO_PRESIDENTE]
    votaveis = {v["votavel"]: v for v in presidente["votaveis"]}
    assert votaveis[22]["votos"] == 120  # lado do BU
    assert votaveis[22]["votos_totalizacao"] == 118  # lado da totalização
    achados = presidente["integridade"]["achados"]
    assert [a["regra"] for a in achados] == ["R3"]
    assert achados[0]["valor_observado"] == 120
    assert achados[0]["valor_esperado"] == 118
    # As duas seções divergentes aparecem no ranqueado, cada uma com o seu R3.
    sinalizacoes = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")
    entradas = {(s["zona"], s["secao"]): s for s in sinalizacoes["sinalizacoes"]}
    assert set(entradas) == {(2, 40), (1, 12)}
    for chave, (votavel_totalizacao, esperado) in {
        (2, 40): (118, 120),
        (1, 12): (122, 120),
    }.items():
        achado = entradas[chave]["achados"][0]
        assert achado["regra"] == "R3"
        assert achado["valor_observado"] == esperado
        assert achado["valor_esperado"] == votavel_totalizacao


def test_anomalia_r1_aparece_agrupada_no_sinalizacoes_json(tmp_path):
    # VAL-INT-001 pela superfície de emissão: achado com valores, frase e módulo
    pleito = gerador.pleito_padrao()
    gerador.injetar_r1(
        pleito, zona=1, secao=2, cargo_id=gerador.CARGO_PRESIDENTE, comparecimento=248
    )
    _emitir_na_fixture(tmp_path, pleito)
    sinalizacoes = _ler_json(tmp_path / "public" / "data" / "sinalizacoes.json")
    entradas = sinalizacoes["sinalizacoes"]
    assert len(entradas) == 1  # uma entrada por seção, achados agrupados
    entrada = entradas[0]
    assert entrada["zona"] == 1
    assert entrada["secao"] == 2
    assert entrada["municipio"] == "BRASÍLIA"
    assert entrada["local"] == "ESCOLA CLASSE CONCEIÇÃO DE MACEDO"
    assert len(entrada["achados"]) == 1
    achado = entrada["achados"][0]
    assert achado["modulo"] == "integridade"
    assert achado["regra"] == "R1"
    assert achado["cargo"] == "Presidente"
    assert achado["valor_observado"] == 250
    assert achado["valor_esperado"] == 248
    assert "250" in achado["frase"] and "248" in achado["frase"]


def test_reexecucao_reemite_o_mesmo_conteudo(tmp_path):
    pleito = gerador.pleito_padrao()
    primeiro = _emitir_na_fixture(tmp_path, pleito)

    def _conteudo(pasta: Path) -> dict[str, str]:
        saida = pasta / "public" / "data"
        conteudo = {}
        for caminho in sorted(saida.rglob("*.json")):
            dados = json.loads(caminho.read_text(encoding="utf-8"))
            dados.pop("data_geracao", None)  # idempotência exceto a data de geração
            conteudo[str(caminho.relative_to(saida))] = json.dumps(dados, sort_keys=True)
        return conteudo

    conteudo_1 = _conteudo(tmp_path)
    segundo = _emitir_na_fixture(tmp_path, pleito)
    conteudo_2 = _conteudo(tmp_path)
    assert primeiro["arquivos"] == segundo["arquivos"]
    assert conteudo_1 == conteudo_2  # emissão é idempotente


def test_cli_emite_public_data_sobre_a_fixture(tmp_path, capsys):
    gerador.empacotar(tmp_path, gerador.pleito_padrao())
    saida = tmp_path / "public" / "data"
    processo = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline",
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
            "--etapas",
            "ingest,totais,integridade,emitir",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0, processo.stdout + processo.stderr
    saida_terminal = processo.stdout + processo.stderr
    assert "[emissao]" in saida_terminal
    assert "seções sinalizadas" in saida_terminal
    assert (saida / "index.json").is_file()
    assert (saida / "sinalizacoes.json").is_file()
    assert (saida / "zonas" / "1.json").is_file()
    indice = _ler_json(saida / "index.json")
    assert indice["contagens"]["integridade"]["secoes_analisadas"] == 4


def test_cli_emitir_sem_banco_falha_de_forma_orientada(tmp_path, capsys):
    from pipeline.cli import main

    codigo = main(
        ["--etapas", "emitir", "--data-dir", str(tmp_path), "--out-dir", str(tmp_path / "sai")]
    )
    assert codigo != 0
    erro = capsys.readouterr().err
    assert "emitir" in erro
    assert "ingest" in erro
    assert not (tmp_path / "sai").exists()  # nada emitido sem banco
