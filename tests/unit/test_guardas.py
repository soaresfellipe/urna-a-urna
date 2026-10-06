"""Testes das guardas de publicação (VAL-DADOS-005).

Três guardas falham quando violadas e passam na execução limpa:

1. nenhum arquivo em public/data maior que 25 MiB (limite do config.yaml);
2. total de arquivos emitidos menor que 20 mil (limite do config.yaml);
3. dado bruto (data/) nunca trackeado no git (git ls-files).

Os casos de violação usam diretórios temporários e repositórios git de
teste — a suíte não depende de public/data/ preexistente (checkout limpo
continua válido); quando a emissão real existe, ela também é verificada.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pipeline import guardas
from pipeline.erros import GuardaError

REPOSITORIO = Path(__file__).resolve().parents[2]

MIB = 1024 * 1024


def _config_publicacao(tamanho_max_mb=25, total_max_arquivos=20000) -> dict:
    return {
        "publicacao": {
            "tamanho_max_mb": tamanho_max_mb,
            "total_max_arquivos": total_max_arquivos,
        }
    }


# ---------------------------------------------------------------------------
# Guarda 1 — tamanho por arquivo
# ---------------------------------------------------------------------------


def test_arquivo_acima_do_limite_e_violacao(tmp_path):
    grande = tmp_path / "zonas" / "1.json"
    grande.parent.mkdir()
    grande.write_bytes(b"x" * (26 * MIB))
    violacoes = guardas.violacoes_de_tamanho(tmp_path, limite_mb=25)
    assert len(violacoes) == 1
    assert "zonas/1.json" in violacoes[0]
    assert "26,00 MiB" in violacoes[0] or "26.00 MiB" in violacoes[0]


def test_arquivo_no_limite_exato_passa(tmp_path):
    no_limite = tmp_path / "sinalizacoes.json"
    no_limite.write_bytes(b"x" * (25 * MIB))
    assert guardas.violacoes_de_tamanho(tmp_path, limite_mb=25) == []


def test_pasta_vazia_nao_tem_violacao_de_tamanho(tmp_path):
    assert guardas.violacoes_de_tamanho(tmp_path, limite_mb=25) == []


def test_executar_levanta_guardaerror_com_caminho_e_tamanho(tmp_path):
    grande = tmp_path / "index.json"
    grande.write_bytes(b"x" * (26 * MIB))
    with pytest.raises(GuardaError) as excinfo:
        guardas.executar(tmp_path, _config_publicacao())
    mensagem = str(excinfo.value)
    assert "index.json" in mensagem
    assert "26" in mensagem


# ---------------------------------------------------------------------------
# Guarda 2 — total de arquivos
# ---------------------------------------------------------------------------


def test_total_acima_do_limite_e_violacao(tmp_path):
    for indice in range(6):
        (tmp_path / f"arquivo-{indice}.json").write_text("{}", encoding="utf-8")
    violacoes = guardas.violacoes_de_total(tmp_path, total_max=5)
    assert len(violacoes) == 1
    assert "6" in violacoes[0] and "5" in violacoes[0]


def test_total_abaixo_do_limite_passa(tmp_path):
    for indice in range(5):
        (tmp_path / f"arquivo-{indice}.json").write_text("{}", encoding="utf-8")
    assert guardas.violacoes_de_total(tmp_path, total_max=20000) == []


def test_executar_acumula_violacoes_de_tamanho_e_total(tmp_path):
    (tmp_path / "grande.json").write_bytes(b"x" * (26 * MIB))
    for indice in range(3):
        (tmp_path / f"arquivo-{indice}.json").write_text("{}", encoding="utf-8")
    with pytest.raises(GuardaError) as excinfo:
        guardas.executar(tmp_path, _config_publicacao(tamanho_max_mb=25, total_max_arquivos=3))
    mensagem = str(excinfo.value)
    assert "grande.json" in mensagem
    assert "4" in mensagem  # total de arquivos (grande.json + 3) contra o limite 3


# ---------------------------------------------------------------------------
# Guarda 3 — dado bruto nunca trackeado no git
# ---------------------------------------------------------------------------


def _repo_de_teste(tmp_path: Path, *, trackear: bool) -> Path:
    """Repositório git de teste; quando trackear, data/raw/x.zip entra no índice."""
    repo = tmp_path / "repo"
    (repo / "data" / "raw").mkdir(parents=True)
    (repo / "data" / "raw" / "x.zip").write_bytes(b"zip de teste")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if trackear:
        subprocess.run(["git", "-C", str(repo), "add", "-f", "data/raw/x.zip"], check=True)
    else:
        (repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    return repo


def test_dado_bruto_trackeado_e_violacao(tmp_path):
    repo = _repo_de_teste(tmp_path, trackear=True)
    violacoes = guardas.violacoes_dados_brutos(repo)
    assert len(violacoes) == 1
    assert "data/raw/x.zip" in violacoes[0]
    assert "trackeado" in violacoes[0]


def test_dado_bruto_nao_trackeado_passa(tmp_path):
    repo = _repo_de_teste(tmp_path, trackear=False)
    assert guardas.violacoes_dados_brutos(repo) == []


def test_repositorio_real_nao_trackeia_nada_em_data():
    """No repositório de trabalho, data/ inteiro fica fora do git."""
    assert guardas.violacoes_dados_brutos(REPOSITORIO) == []


# ---------------------------------------------------------------------------
# Guardas sobre a emissão real (public/data), quando ela existe
# ---------------------------------------------------------------------------


def test_public_data_quando_presente_respeita_os_limites():
    """A execução real no DF é validada pela mesma guarda dos testes."""
    dados_publicados = REPOSITORIO / "public" / "data"
    if not dados_publicados.is_dir():
        pytest.skip("public/data/ ausente (checkout limpo); gere os dados para validar")
    guardas.executar(dados_publicados, _config_publicacao())


def test_injecao_de_arquivo_grande_em_public_data_derruba_a_guarda():
    """Provocação do contrato: arquivo de 26 MiB injetado em public/data
    faz a guarda falhar nomeando o arquivo; a injeção é removida ao final."""
    dados_publicados = REPOSITORIO / "public" / "data"
    if not dados_publicados.is_dir():
        pytest.skip("public/data/ ausente (checkout limpo); gere os dados para validar")
    injetado = dados_publicados / "injecao-grande.tmp"
    injetado.write_bytes(b"x" * (26 * MIB))
    try:
        with pytest.raises(GuardaError) as excinfo:
            guardas.executar(dados_publicados, _config_publicacao())
        assert "injecao-grande.tmp" in str(excinfo.value)
    finally:
        injetado.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Config — os limites da guarda vêm do config.yaml
# ---------------------------------------------------------------------------


def test_limites_da_guarda_vem_do_config(tmp_path):
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.json").write_text("{}", encoding="utf-8")
    assert guardas.violacoes_de_total(tmp_path, total_max=3) == []
    assert guardas.violacoes_de_total(tmp_path, total_max=2) != []
