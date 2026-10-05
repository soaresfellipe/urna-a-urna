"""Testes unitários da emissão: contagens, frases e relato no terminal."""

from __future__ import annotations

from pipeline import emitir


def _contagens(sinalizadas=2, achados=3, analisadas=6748, regras=None):
    return {
        "integridade": {
            "secoes_sinalizadas": sinalizadas,
            "achados": achados,
            "secoes_analisadas": analisadas,
            "percentual_sinalizado": round(sinalizadas / analisadas * 100, 2),
            "por_regra": regras or {"R1": 1, "R2": 0, "R3": 2},
        }
    }


def test_percentual_sinalizado_e_calculado_sobre_o_total_de_secoes():
    contagens = emitir._contagens_por_modulo(
        {
            "secoes_analisadas": 6748,
            "secoes_sinalizadas": 21,
            "achados": [{}] * 21,
            "contagens": {"R1": 0, "R2": 0, "R3": 21},
        }
    )
    integridade = contagens["integridade"]
    assert integridade["secoes_sinalizadas"] == 21
    assert integridade["achados"] == 21
    assert integridade["secoes_analisadas"] == 6748
    assert integridade["percentual_sinalizado"] == round(21 / 6748 * 100, 2)
    assert integridade["por_regra"] == {"R1": 0, "R2": 0, "R3": 21}


def test_frase_r1_cita_os_valores_observado_e_esperado():
    frase = emitir._frase_achado(
        {
            "regra": "R1",
            "cargo": "Presidente",
            "zona": 1,
            "secao": 2,
            "valor_observado": 1234,
            "valor_esperado": 1230,
            "votavel": None,
            "votavel_nome": None,
        }
    )
    assert "1.234" in frase and "1.230" in frase
    assert "Presidente" in frase
    assert "R1" in frase


def test_frase_r3_identifica_o_votavel_divergente():
    frase = emitir._frase_achado(
        {
            "regra": "R3",
            "cargo": "Governador",
            "zona": 2,
            "secao": 40,
            "valor_observado": 120,
            "valor_esperado": 118,
            "votavel": 15,
            "votavel_nome": "IBANEIS ROCHA",
        }
    )
    assert "15" in frase and "IBANEIS ROCHA" in frase
    assert "120" in frase and "118" in frase


def test_relatar_imprime_contagens_por_modulo(capsys):
    resumo = {
        "out_dir": "public/data",
        "total_secoes": 6748,
        "arquivos": ["index.json", "sinalizacoes.json", "zonas/1.json"],
        "zonas": 1,
        "contagens": _contagens(),
    }
    emitir.relatar(resumo)
    saida = capsys.readouterr().out
    assert "[emissao]" in saida
    assert "integridade" in saida
    assert "2 seções sinalizadas" in saida
    assert "3 achados" in saida
    assert "6.748" in saida
    assert "0,03%" in saida
    assert "index.json" in saida


def test_relatar_sem_sinalizacoes_mostra_zero(capsys):
    resumo = {
        "out_dir": "public/data",
        "total_secoes": 6748,
        "arquivos": ["index.json", "sinalizacoes.json", "zonas/1.json", "zonas/2.json"],
        "zonas": 2,
        "contagens": _contagens(sinalizadas=0, achados=0, regras={"R1": 0, "R2": 0, "R3": 0}),
    }
    emitir.relatar(resumo)
    saida = capsys.readouterr().out
    assert "0 seções sinalizadas" in saida
    assert "0,00%" in saida
