"""Sanidade da suíte e do esqueleto: pacote importável e comando --help de verdade."""

import subprocess
import sys

from pipeline import RAIZ


def test_python_m_pipeline_help_funciona():
    processo = subprocess.run(
        [sys.executable, "-m", "pipeline", "--help"],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert processo.returncode == 0
    assert "--uf" in processo.stdout
    assert "--etapas" in processo.stdout


def test_arquivos_do_esqueleto_existem():
    for nome in ("config.yaml", "pyproject.toml", "uv.lock", "README.md"):
        assert (RAIZ / nome).exists(), f"faltando {nome} na raiz"


def test_versao_do_pacote_definida():
    import pipeline

    assert isinstance(pipeline.__version__, str)
