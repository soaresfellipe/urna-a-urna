"""Integração do módulo 3 com fixtures sintéticas e diferenças `d` plantadas.

O mini-pleito é empacotado como zips + manifesto, ingerido pelo caminho REAL
de pipeline/ingest.py e analisado pelo módulo 3 de verdade (pipeline/
cruzado.py). Uma seção plantada com diferença extrema do par é sinalizada
com o par identificado pelo rótulo e a referência local registrada; seções
típicas do grupo NÃO podem ser sinalizadas (sem falsos positivos). Trocar os
pares no config.yaml muda o resultado sem alterar código.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import RAIZ, cruzado, ingest
from pipeline.config import carregar_config
from tests.fixtures import gerador

# Grupo homogêneo: pres validos 500 (p22 = 36%, p13 = 44%);
# gov validos 400 (p15 = 40%, p43 = 60%).
PRES_HOMOGEO = {22: 180, 13: 220, 15: 100}
GOV_HOMOGEO = {15: 160, 43: 240}
# d do par 1 (22×15): −4%; d do par 2 (13×43): −16%.

# Seção plantada: pres validos 5000 (p22 = 56%, p13 = 42%);
# gov validos 4000 (p15 = 40%, p43 = 60%).
# d do par 1: +16% (20 pp acima da mediana do grupo);
# d do par 2: −18% (2 pp — não sinaliza).
PRES_PLANTADA = {22: 2800, 13: 2100, 15: 100}
GOV_PLANTADA = {15: 1600, 43: 2400}


def _cargo(cargo_id: int, votos: dict[int, int]):
    return gerador.pleito_do_cargo(cargo_id, aptos=sum(votos.values()) + 150, votos=dict(votos))


def _adicionar(
    pleito: gerador.PleitoSintetico,
    *,
    zona: int,
    secao: int,
    local_id: int,
    local_nome: str,
    pres: dict[int, int],
    gov: dict[int, int],
) -> None:
    pleito.adicionar_secao(
        zona=zona,
        secao=secao,
        local_id=local_id,
        local_nome=local_nome,
        local_endereco="SQS TESTE",
        cargos={
            gerador.CARGO_PRESIDENTE: _cargo(gerador.CARGO_PRESIDENTE, pres),
            gerador.CARGO_GOVERNADOR: _cargo(gerador.CARGO_GOVERNADOR, gov),
        },
    )


def _homogeneas(
    pleito: gerador.PleitoSintetico,
    *,
    zona: int,
    local_id: int,
    local_nome: str,
    quantidade: int,
    primeira_secao: int = 1,
    pres: dict[int, int] | None = None,
    gov: dict[int, int] | None = None,
) -> None:
    for indice in range(quantidade):
        _adicionar(
            pleito,
            zona=zona,
            secao=primeira_secao + indice,
            local_id=local_id,
            local_nome=local_nome,
            pres=pres if pres is not None else PRES_HOMOGEO,
            gov=gov if gov is not None else GOV_HOMOGEO,
        )


def _analisar(pasta: Path, pleito: gerador.PleitoSintetico, config=None) -> dict:
    gerador.empacotar(pasta, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=pasta)
    return cruzado.conferir(
        uf="DF", turno=1, ano=2022, data_dir=pasta, config=config or carregar_config()
    )


def _pleito_zona_1() -> gerador.PleitoSintetico:
    """Zona 1 com 16 seções homogêneas e 1 plantada com d do par 1 a +20 pp."""
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=1, local_id=101, local_nome="ESCOLA A", quantidade=16)
    _adicionar(
        pleito,
        zona=1,
        secao=999,
        local_id=101,
        local_nome="ESCOLA A",
        pres=PRES_PLANTADA,
        gov=GOV_PLANTADA,
    )
    return pleito


def test_diferenca_extrema_plantada_e_sinalizada_com_par_e_referencia(tmp_path):
    resultado = _analisar(tmp_path, _pleito_zona_1())
    achados = resultado["achados"]
    # Só a seção plantada, só o par 1 (o d do par 2 muda só 2 pp).
    assert {(a["zona"], a["secao"]) for a in achados} == {(1, 999)}
    assert [a["rotulo"] for a in achados] == ["Bolsonaro (22) × Ibaneis (15)"]
    achado = achados[0]
    assert achado["modulo"] == "cruzado"
    assert achado["diferenca"] == pytest.approx(16.0)  # d = 56% − 40%
    assert achado["mediana"] == pytest.approx(-4.0)
    assert achado["mad"] == pytest.approx(0.0)
    assert achado["diferenca_pp"] == pytest.approx(20.0, abs=1e-6)
    assert math.isinf(achado["z_robusto"])  # MAD 0 com divergência → z infinito
    assert achado["z_tamanho"] == pytest.approx(19.1320, rel=1e-4)
    assert achado["q_valor"] <= 0.01
    # Referência: município único/degenerado → cai na zona (config do DF).
    assert achado["nivel_referencia"] == "zona"
    assert resultado["nivel_referencia_usado"] == "zona"
    assert achado["tamanho_grupo"] == 16  # leave-one-out: 17 seções da zona menos a própria
    assert achado["percentual_presidente"] == pytest.approx(56.0)
    assert achado["percentual_governador"] == pytest.approx(40.0)
    assert achado["votos_validos_presidente"] == 5000
    assert achado["votos_validos_governador"] == 4000
    assert resultado["secoes_sinalizadas"] == 1
    assert resultado["secoes_total"] == 17
    assert resultado["testes"] == 34  # 17 seções × 2 pares do config


def test_secoes_tipicas_nao_sao_sinalizadas(tmp_path):
    resultado = _analisar(tmp_path, _pleito_zona_1())
    assert resultado["secoes_sinalizadas"] == 1  # apenas a plantada
    assert len({(a["zona"], a["secao"]) for a in resultado["achados"]}) == 1


def test_violacao_de_um_so_criterio_nao_sinaliza(tmp_path):
    # Seção grande com d a 6,5 pp da mediana (< 8 pp): a sinalização exige
    # TODOS os critérios — a diferença mínima falha e a seção fica sem
    # achado (também nos dois cargos do par 2: d muda só 1,5 pp).
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=1, local_id=101, local_nome="ESCOLA A", quantidade=16)
    # pres validos 10000 (p22 = 42,5%) → d = 2,5%; mediana −4% → 6,5 pp.
    _adicionar(
        pleito,
        zona=1,
        secao=999,
        local_id=101,
        local_nome="ESCOLA A",
        pres={22: 4250, 13: 4250, 15: 1500},
        gov={15: 1600, 43: 2400},
    )
    resultado = _analisar(tmp_path, pleito)
    assert resultado["achados"] == []
    assert resultado["secoes_sinalizadas"] == 0


def test_cada_secao_e_julgada_pela_mediana_do_proprio_grupo(tmp_path):
    # Duas zonas com medianas de d distintas (−1,5% e −10%); a MESMA plantada
    # (p22 = 60%, p15 do gov homogêneo da zona) diverge nas duas, mas cada
    # achado registra a mediana da PRÓPRIA zona — comparação local, nunca um
    # número fixo no código.
    pleito = gerador.PleitoSintetico()
    _homogeneas(pleito, zona=1, local_id=101, local_nome="ESCOLA A", quantidade=16)
    _homogeneas(
        pleito,
        zona=2,
        local_id=201,
        local_nome="ESCOLA B",
        quantidade=16,
        # pres validos 500 (p22 = 40%); gov validos 400 (p15 = 50%): d = −10%.
        pres={22: 200, 13: 250, 15: 50},
        gov={15: 200, 43: 200},
    )
    for zona, local_id, local_nome, gov in (
        (1, 101, "ESCOLA A", GOV_HOMOGEO),
        (2, 201, "ESCOLA B", {15: 2000, 43: 2000}),  # validos 4000; p15 = 50%
    ):
        _adicionar(
            pleito,
            zona=zona,
            secao=900 + zona,
            local_id=local_id,
            local_nome=local_nome,
            pres={22: 3000, 13: 1900, 15: 100},  # validos 5000; p22 = 60%
            gov=gov,
        )
    resultado = _analisar(tmp_path, pleito)
    por_zona = {
        a["zona"]: a for a in resultado["achados"] if a["rotulo"] == "Bolsonaro (22) × Ibaneis (15)"
    }
    assert set(por_zona) == {1, 2}
    # Zona 1: d = 60 − 40 = +20% contra mediana de −4% (24 pp).
    assert por_zona[1]["mediana"] == pytest.approx(-4.0)
    assert por_zona[1]["diferenca_pp"] == pytest.approx(24.0, abs=1e-6)
    # Zona 2: d = 60 − 50 = +10% contra mediana de −10% (20 pp).
    assert por_zona[2]["mediana"] == pytest.approx(-10.0)
    assert por_zona[2]["diferenca_pp"] == pytest.approx(20.0, abs=1e-6)


def test_remover_par_do_config_muda_o_resultado_sem_mudar_codigo(tmp_path):
    # VAL-CRUZ-001: com o par 13×43 removido, nenhuma seção é analisada por
    # ele; o par 1 segue intacto.
    config = carregar_config()
    config["voto_cruzado"]["pares"] = [config["voto_cruzado"]["pares"][0]]
    resultado = _analisar(tmp_path, _pleito_zona_1(), config)
    rotulos = {a["rotulo"] for a in resultado["achados"]}
    assert rotulos == {"Bolsonaro (22) × Ibaneis (15)"}
    assert list(resultado["contagens_por_par"]) == ["Bolsonaro (22) × Ibaneis (15)"]
    assert resultado["testes"] == 17  # 17 seções × 1 par


def test_par_sintetico_adicionado_no_config_produz_resultado_proprio(tmp_path):
    # VAL-CRUZ-001: par sintético 22×43 no config roda e produz o próprio
    # achado (d = 56% − 60% = −4% contra mediana de −24% = 20 pp).
    config = carregar_config()
    config["voto_cruzado"]["pares"] = [
        config["voto_cruzado"]["pares"][0],
        {
            "rotulo": "Sintético (22) × Grass (43)",
            "presidente": {"numero": 22, "nome": "Sintético"},
            "governador": {"numero": 43, "nome": "Grass"},
        },
    ]
    resultado = _analisar(tmp_path, _pleito_zona_1(), config)
    achado_sintetico = next(
        a for a in resultado["achados"] if a["rotulo"] == "Sintético (22) × Grass (43)"
    )
    assert (achado_sintetico["zona"], achado_sintetico["secao"]) == (1, 999)
    assert achado_sintetico["diferenca"] == pytest.approx(-4.0)
    assert achado_sintetico["mediana"] == pytest.approx(-24.0)
    assert achado_sintetico["diferenca_pp"] == pytest.approx(20.0, abs=1e-6)
    assert set(resultado["contagens_por_par"]) == {
        "Bolsonaro (22) × Ibaneis (15)",
        "Sintético (22) × Grass (43)",
    }


def test_limiares_do_config_controlam_a_sinalizacao(tmp_path):
    pleito = _pleito_zona_1()
    gerador.empacotar(tmp_path, pleito)
    ingest.ingerir(uf="DF", turno=1, data_dir=tmp_path)

    config = carregar_config()
    config["limiares"]["cruzado"]["z_tamanho_min"] = 1000.0
    assert (
        cruzado.conferir(uf="DF", turno=1, ano=2022, data_dir=tmp_path, config=config)["achados"]
        == []
    )

    config = carregar_config()
    config["limiares"]["cruzado"]["dif_minima_pp"] = 100.0
    assert (
        cruzado.conferir(uf="DF", turno=1, ano=2022, data_dir=tmp_path, config=config)["achados"]
        == []
    )


def test_contagens_por_par_fecham_no_total(tmp_path):
    resultado = _analisar(tmp_path, _pleito_zona_1())
    assert resultado["testes"] == 34  # 17 seções × 2 pares
    # Os achados por par somam os achados totais; seção sinalizada por par.
    assert sum(c["achados"] for c in resultado["contagens_por_par"].values()) == len(
        resultado["achados"]
    )
    assert sum(c["secoes_sinalizadas"] for c in resultado["contagens_por_par"].values()) == len(
        {(a["rotulo"], a["zona"], a["secao"]) for a in resultado["achados"]}
    )


def test_cli_roda_a_etapa_cruzado_sobre_a_fixture(tmp_path):
    gerador.empacotar(tmp_path, gerador.pleito_padrao())
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
            "--etapas",
            "ingest,cruzado",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0, processo.stdout + processo.stderr
    assert "[cruzado]" in processo.stdout
    # pleito_padrao: 4 seções típicas → nenhum achado.
    assert "achados: 0" in processo.stdout
    # Os dois pares do config aparecem no relato do terminal.
    for rotulo in ("Bolsonaro (22) × Ibaneis (15)", "Lula (13) × Grass (43)"):
        assert rotulo in processo.stdout


def test_emissao_registra_as_contagens_do_modulo_3(tmp_path):
    gerador.empacotar(tmp_path, gerador.pleito_padrao())
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
            str(tmp_path / "public" / "data"),
            "--etapas",
            "ingest,totais,integridade,divergencia,cruzado,emitir",
        ],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0, processo.stdout + processo.stderr
    caminho_index = tmp_path / "public" / "data" / "index.json"
    assert caminho_index.exists(), processo.stdout + processo.stderr
    indice = json.loads(caminho_index.read_text("utf-8"))
    rubrica = indice["contagens"]["cruzado"]
    assert rubrica["secoes_total"] == 4
    assert rubrica["achados"] == 0
    assert rubrica["por_par"] == {
        "Bolsonaro (22) × Ibaneis (15)": {"achados": 0, "secoes_sinalizadas": 0},
        "Lula (13) × Grass (43)": {"achados": 0, "secoes_sinalizadas": 0},
    }
