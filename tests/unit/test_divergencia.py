"""Unitários do módulo 2: mediana, MAD, leave-one-out, z e FDR (BH)."""

from __future__ import annotations

import math

import pytest

from pipeline.divergencia import (
    mad,
    mediana,
    mediana_leave_um_fora,
    p_valor_normal_dupla,
    q_valores_bh,
    z_robusto,
    z_tamanho,
)


def test_mediana_de_lista_impar():
    assert mediana([1.0, 3.0, 5.0]) == 3.0


def test_mediana_de_lista_par():
    assert mediana([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_mad_basico():
    # mediana 3; desvios |x-3| = [2, 1, 0, 1, 2]; mediana dos desvios = 1.
    assert mad([1.0, 2.0, 3.0, 4.0, 5.0]) == 1.0


def test_mad_de_grupo_homogeneo_e_zero():
    assert mad([0.48] * 10) == 0.0


def test_mediana_leave_um_fora_com_extremo():
    # leave-one-out retira o valor da própria seção: a mediana cai de 0,325
    # (com o extremo) para 0,3 (sem ele) — base da comparação local.
    valores = [0.2, 0.25, 0.3, 0.35, 0.4, 0.9]
    m, dispersao = mediana_leave_um_fora(valores, indice=5)
    assert m == pytest.approx(0.3)
    assert dispersao == pytest.approx(0.05)  # MAD dos 5 restantes em torno de 0,3


def test_mediana_leave_um_fora_com_valor_central():
    valores = [1.0, 2.0, 3.0, 4.0, 5.0]
    m, dispersao = mediana_leave_um_fora(valores, indice=2)
    assert m == pytest.approx(3.0)  # mediana de [1, 2, 4, 5]
    assert dispersao == pytest.approx(1.5)  # desvios [2, 1, 1, 2] em torno de 3


def test_z_robusto_com_mad_positivo():
    # (0,75 - 0,48) / (1,4826 * 0,02) = 0,27 / 0,029652
    assert z_robusto(0.75, 0.48, 0.02) == pytest.approx(9.1059, rel=1e-4)


def test_z_robusto_com_mad_zero_e_divergencia_e_infinito():
    assert math.isinf(z_robusto(0.75, 0.48, 0.0))
    assert z_robusto(0.75, 0.48, 0.0) > 0


def test_z_robusto_com_mad_zero_e_valores_iguais_e_zero():
    assert z_robusto(0.48, 0.48, 0.0) == 0.0


def test_z_tamanho_usa_o_erro_binomial():
    # se = sqrt(0,48 * 0,52 / 520) = 0,0219089...; z = 0,27 / se
    assert z_tamanho(0.75, 0.48, 520) == pytest.approx(12.3232, rel=1e-4)


def test_z_tamanho_de_secao_pequena_fica_baixo():
    # seção de 40 votos válidos com 14,5 pp acima: z de tamanho ~1,8 (< 6)
    assert z_tamanho(0.625, 0.48, 40) == pytest.approx(1.8356, rel=1e-3)


def test_z_tamanho_com_mediana_extrema_nao_divide_por_zero():
    # mediana 0 (ou 1) zera o erro binomial; com divergência, z é infinito.
    assert math.isinf(z_tamanho(0.1, 0.0, 500))
    assert z_tamanho(0.0, 0.0, 500) == 0.0


def test_p_valor_normal_dupla():
    assert p_valor_normal_dupla(0.0) == 1.0
    assert p_valor_normal_dupla(1.96) == pytest.approx(0.05, abs=1e-3)
    assert p_valor_normal_dupla(12.0) == pytest.approx(0.0, abs=1e-30)
    assert p_valor_normal_dupla(float("inf")) == 0.0


def test_q_valores_bh_caso_conhecido():
    # BH passo a passo: q_i = min sobre postos superiores de p_j * N / j.
    p = [0.001, 0.02, 0.03, 0.2, 0.5]
    q = q_valores_bh(p)
    assert q[0] == pytest.approx(0.005)  # 0,001 * 5 / 1
    assert q[1] == pytest.approx(0.05)  # 0,02 * 5 / 2
    assert q[2] == pytest.approx(0.05)  # 0,03 * 5 / 3, limitado pelo de rank 2
    assert q[3] == pytest.approx(0.25)  # 0,2 * 5 / 4
    assert q[4] == pytest.approx(0.5)  # 0,5 * 5 / 5


def test_q_valores_bh_mantem_a_ordem_de_entrada():
    p = [0.2, 0.001, 0.03]
    q = q_valores_bh(p)
    assert q[0] == pytest.approx(0.2)  # posto 3: 0,2 * 3 / 3
    assert q[1] == pytest.approx(0.003)  # posto 1: 0,001 * 3 / 1
    assert q[2] == pytest.approx(0.045)  # posto 2: 0,03 * 3 / 2, monótona


def test_q_valores_bh_limita_em_um():
    assert q_valores_bh([0.9]) == [0.9]
    assert q_valores_bh([0.6, 0.7]) == [pytest.approx(0.7), pytest.approx(0.7)]
    assert q_valores_bh([]) == []


def test_q_valores_bh_varios_zerados_baixo_o_resto():
    # p = 0 domina o passo a passo: o menor q é propagado para trás.
    q = q_valores_bh([0.0, 0.0, 0.5, 0.5])
    assert q[0] == 0.0 and q[1] == 0.0
    assert q[2] == pytest.approx(0.5)
    assert q[3] == pytest.approx(0.5)
