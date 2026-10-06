"""Verificação dos workflows GitHub Actions (VAL-AUTO-001).

Os workflows são PREPARADOS e NÃO executados nesta missão: os testes apenas
confirmam que os arquivos existem, fazem parse como YAML válido, cobrem as
etapas exigidas (uv sync → pytest → ruff no CI; pipeline → guardas → deploy
no pipeline-deploy) e usam placeholders de segredos — nada é executado.

O UF, ano e turno do comando do deploy são conferidos contra o config.yaml,
única fonte de verdade do pleito.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.config import carregar_config

REPOSITORIO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORIO / ".github" / "workflows"


def _l(nome: str) -> dict[str, object]:
    caminho = WORKFLOWS / nome
    assert caminho.is_file(), f"workflow ausente: .github/workflows/{nome}"
    conteudo = caminho.read_text(encoding="utf-8")
    doc = yaml.safe_load(conteudo)
    assert isinstance(doc, dict), f"{nome} não faz parse como mapeamento YAML"
    return doc


def _gatilho(doc: dict[str, object]) -> dict[str, object]:
    """Bloco de gatilhos; o PyYAML lê a chave 'on' como booleano True."""
    gatilho = doc.get("on", doc.get(True))
    assert isinstance(gatilho, dict), "workflows precisam do bloco 'on:' de gatilhos"
    return gatilho


def _passos(doc: dict[str, object]) -> list[str]:
    """Comandos executáveis dos steps, na ordem em que aparecem no job."""
    jobs = doc.get("jobs", {})
    assert isinstance(jobs, dict) and len(jobs) >= 1, "workflow precisa de jobs"
    passos: list[str] = []
    for job in jobs.values():
        assert isinstance(job, dict)
        por_job = job.get("steps", [])
        assert isinstance(por_job, list)
        for passo in por_job:
            assert isinstance(passo, dict)
            for chave in ("run", "script", "uses"):
                comando = passo.get(chave)
                if isinstance(comando, str):
                    passos.append(comando)
    assert passos, "workflow sem steps"
    return passos


def _texto_passos(passos: list[str]) -> str:
    return "\n".join(passos)


def _indice(passos: list[str], trecho: str) -> int:
    """Posição do primeiro passo cujo texto contém o trecho."""
    for indice, passo in enumerate(passos):
        if trecho in passo:
            return indice
    raise AssertionError(f"nenhum passo contém {trecho!r}")


def _primeiro_run(passo: object) -> str:
    """Comandos 'run:' de um único step (bloco multilinha inclusivo)."""
    assert isinstance(passo, dict)
    comando = passo.get("run", "")
    assert isinstance(comando, str)
    return comando


# ---------------------------------------------------------------------------
# ci.yml — uv sync → pytest → ruff, guardas dentro do pytest
# ---------------------------------------------------------------------------


def test_ci_tem_ciclo_sync_teste_ruff_em_ordem():
    doc = _l("ci.yml")
    passos = _passos(doc)
    enough = {
        "uv sync": _indice(passos, "uv sync"),
        "pytest": _indice(passos, "pytest"),
        "ruff check": _indice(passos, "ruff check"),
        "ruff format": _indice(passos, "ruff format --check"),
    }
    valores = list(enough.values())
    assert valores == sorted(valores), f"ordem das etapas do CI errada: {valores}"


def test_ci_documenta_proposito_e_checkout_limpo():
    texto = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    antes_do_name = texto.split("name:", maxsplit=1)[0]
    comentarios = [linha for linha in antes_do_name.splitlines() if linha.startswith("#")]
    assert comentarios, "ci.yml deve abrir com comentário documentando o propósito"
    assert any("public/data" in linha for linha in comentarios), (
        "ci.yml deve documentar que não depende de public/data/ (checkout limpo)"
    )


# ---------------------------------------------------------------------------
# pipeline-deploy.yml — gatilhos, pipeline, guardas, deploy com placeholders
# ---------------------------------------------------------------------------


def test_deploy_dispara_em_push_na_main_e_workflow_dispatch():
    gatilho = _gatilho(_l("pipeline-deploy.yml"))
    assert "workflow_dispatch" in gatilho
    push = gatilho.get("push")
    assert isinstance(push, dict), "gatilho de push ausente"
    ramos = push.get("branches")
    assert isinstance(ramos, list) and "main" in ramos


def test_deploy_roda_o_pipeline_com_o_pleito_do_config():
    config = carregar_config()
    pleito = config["pleito"]
    assert isinstance(pleito, dict)
    doc = _l("pipeline-deploy.yml")
    passos = _passos(doc)
    trecho_pipeline = "python -m pipeline"
    indice_pipeline = _indice(passos, trecho_pipeline)
    comando_pipeline = passos[indice_pipeline]
    assert f"--uf {pleito['uf']}" in comando_pipeline
    assert f"--ano {pleito['ano']}" in comando_pipeline
    assert f"--turno {pleito['turno']}" in comando_pipeline


def test_deploy_gera_dados_e_roda_guardas_antes_da_publicacao():
    doc = _l("pipeline-deploy.yml")
    passos = _passos(doc)
    indice_pipeline = _indice(passos, "python -m pipeline")
    indice_guarda = _indice(passos, "guardas")
    indice_deploy = _indice(passos, "pages deploy")
    assert indice_pipeline < indice_guarda < indice_deploy, (
        "ordem exigida: pipeline (gera public/data) → guardas → deploy"
    )
    # A guarda de publicação do deploy roda sobre a pasta emitida com o
    # executar() de pipeline/guardas.py (mesma função da emissão).
    assert any("guardas.executar" in passo for passo in passos)
    assert any("public/data" in passo for passo in passos)


def test_deploy_usa_placeholders_de_segredos_sem_token_fixo():
    texto = (WORKFLOWS / "pipeline-deploy.yml").read_text(encoding="utf-8")
    assert "${{ secrets.CLOUDFLARE_API_TOKEN }}" in texto
    assert "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}" in texto
    assert "CLOUDFLARE_API_TOKEN" in texto and "CLOUDFLARE_ACCOUNT_ID" in texto
    # Nenhum token literal (placeholder é referência de segredo).
    for linha in texto.splitlines():
        for segredo in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"):
            if segredo in linha:
                assert "secrets." in linha or linha.lstrip().startswith("#"), (
                    f"linha com {segredo} sem placeholder de secrets: {linha}"
                )


def test_deploy_documenta_como_habilitar_no_cabecalho():
    texto = (WORKFLOWS / "pipeline-deploy.yml").read_text(encoding="utf-8")
    antes_do_name = texto.split("name:", maxsplit=1)[0]
    comentarios = [linha for linha in antes_do_name.splitlines() if linha.startswith("#")]
    assert comentarios, "pipeline-deploy.yml deve abrir com comentário documentando o propósito"
    assert any("README" in linha or "segredos" in linha for linha in comentarios), (
        "cabeçalho deve apontar como o dono habilita o deploy (segredos no GitHub)"
    )


def test_deploy_publica_a_pasta_public_com_wrangler():
    doc = _l("pipeline-deploy.yml")
    texto = _texto_passos(_passos(doc))
    assert "wrangler" in texto
    assert "pages deploy public" in texto
