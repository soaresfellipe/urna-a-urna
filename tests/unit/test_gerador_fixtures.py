"""Fidelidade do gerador de fixtures ao esquema real dos CSVs do TSE.

Cada teste trava uma característica verificada nos arquivos reais
(library/tse-schema.md): encoding Latin-1, delimitador ';', TODOS os campos
entre aspas, colunas exatas do BU (45) e da totalização (26) na ordem
original, '#NULO#' marcando valor ausente e o 2º turno misturado no arquivo
nacional (filtrado pela ingestão).
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from tests.fixtures import gerador


def _bytes(caminho: Path) -> bytes:
    return caminho.read_bytes()


def _texto(caminho: Path) -> str:
    return _bytes(caminho).decode("latin-1")


def _linhas_csv(caminho: Path) -> list[list[str]]:
    return list(csv.reader(io.StringIO(_texto(caminho)), delimiter=";", quotechar='"'))


def _arquivo_com_pleito(pasta: Path) -> tuple[Path, Path]:
    pleito = gerador.pleito_padrao()
    caminho_bu = pasta / "bu.csv"
    caminho_tot = pasta / "tot.csv"
    gerador.escrever_boletim_de_urna(caminho_bu, pleito)
    gerador.escrever_totalizacao(
        caminho_tot, pleito, cargos=[gerador.CARGO_GOVERNADOR], nacional=False
    )
    return caminho_bu, caminho_tot


def test_cabecalho_bu_tem_as_45_colunas_na_ordem_real(tmp_path):
    caminho_bu, _ = _arquivo_com_pleito(tmp_path)
    primeira = _texto(caminho_bu).splitlines()[0]
    assert primeira == ";".join(f'"{coluna}"' for coluna in gerador.COLUNAS_BU)
    assert len(gerador.COLUNAS_BU) == 45


def test_cabecalho_totalizacao_tem_as_26_colunas_na_ordem_real(tmp_path):
    _, caminho_tot = _arquivo_com_pleito(tmp_path)
    primeira = _texto(caminho_tot).splitlines()[0]
    assert primeira == ";".join(f'"{coluna}"' for coluna in gerador.COLUNAS_TOT)
    assert len(gerador.COLUNAS_TOT) == 26


def test_csv_e_latin1_e_nao_decodifica_como_utf8(tmp_path):
    caminho_bu, caminho_tot = _arquivo_com_pleito(tmp_path)
    brutos = _bytes(caminho_bu) + _bytes(caminho_tot)
    texto = brutos.decode("latin-1")  # Latin-1 decodifica qualquer byte
    assert "BRASÍLIA" in texto and "CONCEIÇÃO" in texto
    # Prova de que o arquivo NÃO é UTF-8: acentos em Latin-1 são sequências
    # inválidas de UTF-8 (byte Í = 0xCD seguido de letra não-continuação).
    bytes_nao_ascii = [b for b in brutos if b > 0x7F]
    assert bytes_nao_ascii, "fixture sem acentos não prova o encoding"
    for sequencia in (b"BRAS\xcdLIA",):
        with_erro = False
        try:
            sequencia.decode("utf-8")
        except UnicodeDecodeError:
            with_erro = True
        assert with_erro, "sequência Latin-1 decodificou como UTF-8"


def test_todos_os_campos_entre_aspas(tmp_path):
    caminho_bu, caminho_tot = _arquivo_com_pleito(tmp_path)
    for caminho in (caminho_bu, caminho_tot):
        for linha in _texto(caminho).splitlines():
            campos = linha.split(";")
            for campo in campos:
                assert campo.startswith('"') and campo.endswith('"'), campo
            assert len(campos) in (45, 26)


def test_valor_ausente_e_marcado_com_nulo_textual(tmp_path):
    pleito = gerador.pleito_padrao()
    caminho_bu = tmp_path / "bu.csv"
    caminho_tot = tmp_path / "tot.csv"
    gerador.escrever_boletim_de_urna(caminho_bu, pleito)
    gerador.escrever_totalizacao(
        caminho_tot, pleito, cargos=[gerador.CARGO_GOVERNADOR], nacional=False
    )
    colunas_bu = gerador.COLUNAS_BU
    indice_agregadas = colunas_bu.index("DS_AGREGADAS")
    for campos in _linhas_csv(caminho_bu)[1:]:
        assert campos[indice_agregadas] == "#NULO#"  # nenhum agregado na base
    colunas_tot = gerador.COLUNAS_TOT
    indice_sq = colunas_tot.index("SQ_CANDIDATO")
    indice_votavel = colunas_tot.index("NR_VOTAVEL")
    for campos in _linhas_csv(caminho_tot)[1:]:
        if campos[indice_votavel] in ("95", "96"):
            assert campos[indice_sq] == "#NULO#"


def test_arquivo_nacional_mistura_os_dois_turnos(tmp_path):
    pleito = gerador.pleito_padrao()
    caminho = tmp_path / "tot_br.csv"
    gerador.escrever_totalizacao(
        caminho,
        pleito,
        cargos=[gerador.CARGO_PRESIDENTE],
        nacional=True,
        incluir_turno_2=True,
    )
    linhas = _linhas_csv(caminho)
    turnos = {campos[gerador.COLUNAS_TOT.index("NR_TURNO")] for campos in linhas[1:]}
    assert turnos == {"1", "2"}  # pegadinha real: o BR mistura turnos


def test_injetar_r1_muda_o_comparecimento_sem_mudar_a_soma():
    pleito = gerador.pleito_padrao()
    alvo = pleito.buscar_pleito_do_cargo(1, 2, gerador.CARGO_PRESIDENTE)
    soma_antes = alvo.soma_votos()
    gerador.injetar_r1(
        pleito, zona=1, secao=2, cargo_id=gerador.CARGO_PRESIDENTE, comparecimento=248
    )
    assert alvo.comparecimento == 248
    assert alvo.soma_votos() == soma_antes == 250
    assert alvo.aptos == 267
    assert alvo.abstencoes == 267 - 248


def test_injetar_r2_deixa_o_comparecimento_acima_dos_aptos():
    pleito = gerador.pleito_padrao()
    alvo = pleito.buscar_pleito_do_cargo(1, 30, gerador.CARGO_GOVERNADOR)
    gerador.injetar_r2(
        pleito, zona=1, secao=30, cargo_id=gerador.CARGO_GOVERNADOR, comparecimento=310
    )
    assert alvo.aptos == 300
    assert alvo.comparecimento == 310 > alvo.aptos
    # soma dos votos acompanha o comparecimento: a única regra violada é R2
    assert alvo.soma_votos() == 310


def test_injetar_r3_diverge_somente_na_totalizacao():
    pleito = gerador.pleito_padrao()
    gerador.injetar_r3(
        pleito,
        zona=2,
        secao=40,
        cargo_id=gerador.CARGO_PRESIDENTE,
        votavel=22,
        votos_totalizacao=118,
    )
    p = pleito.buscar_pleito_do_cargo(2, 40, gerador.CARGO_PRESIDENTE)
    assert p.votos == {22: 120, 13: 70, 95: 20, 96: 30}  # BU intacto
    assert p.votos_para_totalizacao()[22] == 118  # totalização divergente
    assert {v: v_ for v, v_ in p.votos_para_totalizacao().items() if v != 22} == {
        13: 70,
        95: 20,
        96: 30,
    }


def test_empacotar_cria_os_tres_zips_e_o_manifesto_pelos_papeis(tmp_path):
    pleito = gerador.pleito_padrao()
    caminhos = gerador.empacotar(tmp_path, pleito)
    assert set(caminhos) == {"boletim_de_urna", "totalizacao_uf", "totalizacao_presidente"}
    for caminho in caminhos.values():
        assert caminho.is_file() and caminho.suffix == ".zip"
        with zipfile.ZipFile(caminho) as arquivo:
            membros = [nome for nome in arquivo.namelist() if nome.endswith(".csv")]
        assert len(membros) == 1
    manifesto = json.loads((tmp_path / "raw" / "manifest.json").read_text(encoding="utf-8"))
    papeis = {entrada["papel"] for entrada in manifesto["arquivos"].values()}
    assert papeis == {"boletim_de_urna", "totalizacao_uf", "totalizacao_presidente"}
