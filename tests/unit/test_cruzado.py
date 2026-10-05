"""Unitários do módulo 3: pares do config, nível de referência e erro combinado."""

from __future__ import annotations

import math
import re

import pytest

from pipeline.config import carregar_config
from pipeline.cruzado import nivel_efetivo, pares_do_config, z_tamanho_erro_combinado
from pipeline.erros import CruzadoError

# ---------------------------------------------------------------------------
# Pares EXCLUSIVAMENTE do config.yaml
# ---------------------------------------------------------------------------


def test_pares_vem_do_config_com_rotulos():
    pares = pares_do_config(carregar_config())
    assert [p["rotulo"] for p in pares] == [
        "Bolsonaro (22) × Ibaneis (15)",
        "Lula (13) × Grass (43)",
    ]
    assert [(p["presidente"]["numero"], p["governador"]["numero"]) for p in pares] == [
        (22, 15),
        (13, 43),
    ]
    assert [(p["presidente"]["nome"], p["governador"]["nome"]) for p in pares] == [
        ("Bolsonaro", "Ibaneis Rocha"),
        ("Lula", "Leandro Grass"),
    ]


def test_config_sem_pares_interrompe_com_mensagem_orientada():
    config = {"limiares": {"cruzado": {}}, "referencia": {"nivel": "municipio"}}
    with pytest.raises(CruzadoError) as excinfo:
        pares_do_config(config)
    assert "voto_cruzado.pares" in str(excinfo.value)
    assert "config.yaml" in str(excinfo.value)


def test_par_sem_rotulo_ou_numero_e_rejeitado():
    config = {
        "voto_cruzado": {
            "pares": [
                {"presidente": {"numero": 22}, "governador": {"numero": 15}},
            ]
        }
    }
    with pytest.raises(CruzadoError) as excinfo:
        pares_do_config(config)
    assert "rotulo" in str(excinfo.value)


def test_codigo_nunca_fixa_candidato_ou_numero():
    # Varredura do VAL-CRUZ-001: nenhum nome/par aprovado no código do pipeline;
    # os números e rótulos só entram via config.yaml.
    from pipeline import RAIZ

    proibidos = ("Bolsonaro", "Ibaneis", "Lula", "Grass")
    numericos = ("22", "13", "15", "43")
    for caminho in sorted((RAIZ / "pipeline").glob("*.py")):
        texto = caminho.read_text(encoding="utf-8")
        for nome in proibidos:
            assert nome not in texto, f"{caminho.name} fixa '{nome}' fora do config.yaml"
        for numero in numericos:
            assert not re.findall(rf"(?<!\d){numero}(?!\d)", texto), (
                f"{caminho.name} fixa o número {numero} fora do config.yaml"
            )


# ---------------------------------------------------------------------------
# Nível de referência: config município com fallback zona (DF degenerado)
# ---------------------------------------------------------------------------


def test_nivel_efetivo_usa_fallback_com_municipio_unico():
    config = {"referencia": {"nivel": "municipio", "fallback": "zona"}}
    assert nivel_efetivo(config, {"97012"}) == "zona"


def test_nivel_efetivo_mantem_municipio_com_varios():
    config = {"referencia": {"nivel": "municipio", "fallback": "zona"}}
    assert nivel_efetivo(config, {"97012", "97013"}) == "municipio"


def test_nivel_efetivo_honra_zona_direto():
    config = {"referencia": {"nivel": "zona", "fallback": "local"}}
    assert nivel_efetivo(config, {"97012"}) == "zona"


def test_nivel_efetivo_sem_referencia_interrompe():
    with pytest.raises(CruzadoError):
        nivel_efetivo({}, {"97012"})


# ---------------------------------------------------------------------------
# Z de tamanho com erro combinado dos dois cargos
# ---------------------------------------------------------------------------


def test_z_tamanho_usa_erro_combinado_dos_dois_cargos():
    # se1 = sqrt(0,56·0,44/5000) = 0,007020; se2 = sqrt(0,375·0,625/4000) = 0,0076547;
    # erro = sqrt(se1² + se2²) = 0,0103862; z = 0,20 / erro.
    z = z_tamanho_erro_combinado(
        d=0.185,
        mediana_grupo=-0.015,
        n_presidente=5000,
        n_governador=4000,
        p_presidente=0.56,
        p_governador=0.375,
    )
    assert z == pytest.approx(19.2563, rel=1e-4)


def test_z_tamanho_erro_combinado_zero_com_diferenca_e_infinito():
    # p1 e p2 em 0/1 anulam o erro combinado; com diferença, z é infinito.
    assert math.isinf(z_tamanho_erro_combinado(0.2, 0.0, 500, 400, 0.0, 1.0))


def test_z_tamanho_erro_combinado_zero_sem_diferenca_e_zero():
    assert z_tamanho_erro_combinado(0.0, 0.0, 500, 400, 0.0, 1.0) == 0.0


def test_z_tamanho_cresce_com_o_tamanho_das_secoes():
    # Dobrar n1 e n2 divide o erro pelo mesmo fator: com n ×100, z ×10.
    pequena = z_tamanho_erro_combinado(0.2, 0.0, 200, 200, 0.5, 0.5)
    grande = z_tamanho_erro_combinado(0.2, 0.0, 20000, 20000, 0.5, 0.5)
    assert grande == pytest.approx(10 * pequena)
    assert grande > 6.0  # seção grande com 20 pp: critério de tamanho passageiro
