"""Guarda de vocabulário (VAL-METODO-002).

Varre o repositório — código-fonte, interface (HTML/CSS/JS), documentação,
configuração e os JSONs emitidos em public/data/ quando existirem — buscando
os termos proibidos definidos no contrato (e suas flexões) e FALHA ao
encontrar qualquer ocorrência. O vocabulário permitido é exclusivamente
"atípico" e "divergente".

Este arquivo nunca contém os termos completos: os padrões e os casos de
injeção são montados por concatenação de fragmentos em tempo de execução,
para que a guarda não se auto-sinalize na própria varredura. A detecção
sobre JSONs é provada com arquivos de injeção em diretório temporário, de
modo que a guarda continua válida num checkout limpo sem public/data/.
"""

import re
from pathlib import Path

import pytest

REPOSITORIO = Path(__file__).resolve().parents[2]

# Termos proibidos montados por fragmentos (a guarda não pode conter o termo
# completo no próprio código-fonte, nem nos comentários).
_FRAGMENTO_UM = ("fr", "au", "d")
_FRAGMENTO_DOIS = ("irre", "gular")
STEM_UM = "".join(_FRAGMENTO_UM)  # cobre as flexões do 1º termo do contrato
STEM_DOIS = "".join(_FRAGMENTO_DOIS)  # cobre as flexões do 2º termo do contrato
TERMOS_PROIBIDOS = (STEM_UM, STEM_DOIS)
PADROES = [re.compile(termo, re.IGNORECASE) for termo in TERMOS_PROIBIDOS]

EXTENSOES_VARRIDAS = {
    ".py",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".js",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
}
PASTAS_TECNICAS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}
ARQUIVOS_IGNORADOS = {"uv.lock"}


def arquivos_para_varrer(raiz: Path) -> list[Path]:
    """Lista arquivos varríveis sob raiz, por extensão e pastas técnicas."""
    if raiz.is_file():
        return [raiz]
    if not raiz.is_dir():
        return []
    encontrados = []
    for caminho in sorted(raiz.rglob("*")):
        if not caminho.is_file():
            continue
        if PASTAS_TECNICAS & set(caminho.parts):
            continue
        if caminho.name in ARQUIVOS_IGNORADOS:
            continue
        if caminho.suffix.lower() not in EXTENSOES_VARRIDAS:
            continue
        encontrados.append(caminho)
    return encontrados


def varrer(raiz: Path, *, ignorar_primeiro_nivel: frozenset[str] = frozenset()) -> list[str]:
    """Devolve "caminho:linha" para cada ocorrência de termo proibido."""
    ocorrencias: list[str] = []
    for caminho in arquivos_para_varrer(raiz):
        if ignorar_primeiro_nivel:
            relativo = caminho.relative_to(raiz)
            if relativo.parts and relativo.parts[0] in ignorar_primeiro_nivel:
                continue
        try:
            linhas = caminho.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            # Binários (zips, CSVs do TSE) estão fora do escopo da guarda.
            continue
        for numero, linha in enumerate(linhas, start=1):
            if any(padrao.search(linha) for padrao in PADROES):
                ocorrencias.append(f"{caminho}:{numero}")
    return ocorrencias


# Casos de injeção por superfície — termos montados por fragmentos.
_CASOS_SUPERFICIE = [
    ("dados.json", '{\n  "descricao": "caso ' + STEM_UM + 'e na seção"\n}\n'),
    ("leia.md", "# Nota\ntexto com a palavra " + STEM_UM + "es no meio.\n"),
    ("modulo.py", "# comentário\nTEXTO = 'comportamento " + STEM_UM.upper() + "ULENTO'\n"),
    ("pagina.html", "<!-- cabeçalho -->\n<p>seção " + STEM_DOIS + "es do pleito</p>\n"),
    ("app.js", "// nota:\nvar msg = '" + STEM_DOIS + "idade detectada';\n"),
    ("estilos.css", "/* bloco */\n/* comentário " + STEM_DOIS + "es */\n"),
    ("config.yaml", "secao:\n  chave: valor " + STEM_DOIS + "\n"),
]

# Flexões e variações que a guarda precisa cobrir (também por fragmentos).
_FLEXOES = [
    STEM_UM + "e",
    STEM_UM.capitalize() + "es",
    STEM_UM.upper() + "ULENTA",
    STEM_UM + "ulentos",
    "des" + STEM_UM + "ar",
    STEM_DOIS,
    STEM_DOIS.capitalize() + "es",
    STEM_DOIS.upper() + "IDADE",
    STEM_DOIS + "idades",
    STEM_DOIS + "mente",
]


def test_repositorio_sem_termos_proibidos():
    """O repositório inteiro (código, interface, docs, config) está limpo.

    A pasta data/ no primeiro nível é ignorada: contém downloads brutos do
    TSE (gitignored), que não são publicação nossa. Os JSONs publicados em
    public/data/, quando presentes, SÃO varridos.
    """
    ocorrencias = varrer(REPOSITORIO, ignorar_primeiro_nivel=frozenset({"data"}))
    assert ocorrencias == [], (
        "Termos proibidos encontrados (use apenas 'atípico'/'divergente'): "
        + "; ".join(ocorrencias)
    )


@pytest.mark.parametrize(("nome", "conteudo"), _CASOS_SUPERFICIE)
def test_injecao_em_cada_superficie_e_sinalizada(tmp_path, nome, conteudo):
    """Termo proibido injetado em arquivo de teste é detectado com arquivo e linha."""
    alvo = tmp_path / nome
    alvo.write_text(conteudo, encoding="utf-8")
    ocorrencias = varrer(tmp_path)
    assert ocorrencias == [str(alvo) + ":2"], ocorrencias


@pytest.mark.parametrize("termo", _FLEXOES)
def test_flexoes_e_variacoes_sao_cobertas(tmp_path, termo):
    alvo = tmp_path / "caso.txt"
    alvo.write_text("linha limpa\ntexto com " + termo + " dentro\n", encoding="utf-8")
    assert varrer(tmp_path) == [f"{alvo}:2"]


@pytest.mark.parametrize(
    "conteudo",
    [
        "seção atípica para revisão humana",
        "percentual divergente da mediana do local",
        "processo regular conforme a lei",
        "um fragmento qualquer de texto",
        "freguesia e fragilidade não disparam a guarda",
    ],
)
def test_vocabulario_permitido_nao_dispara(tmp_path, conteudo):
    alvo = tmp_path / "limpo.json"
    alvo.write_text('{"frase": "' + conteudo + '"}\n', encoding="utf-8")
    assert varrer(tmp_path) == []


def test_json_publicado_aninhado_e_sinalizado(tmp_path):
    """JSONs emitidos em subpastas (como zonas/) também são varridos."""
    pasta = tmp_path / "zonas"
    pasta.mkdir()
    alvo = pasta / "14.json"
    alvo.write_text('{"frase": "aponta ' + STEM_UM + 'e"}\n', encoding="utf-8")
    (tmp_path / "index.json").write_text('{"ok": true}\n', encoding="utf-8")
    assert varrer(tmp_path) == [f"{alvo}:1"]


def test_public_data_quando_presente_esta_limpo():
    """Varredura dos JSONs reais emitidos (pula silenciosamente se ausentes).

    A guarda não depende de public/data/ para ser válida: a detecção sobre
    JSON é exercida pelos testes de injeção acima. Quando a emissão existe
    (como nas verificações com o DF real), ela é varrida de fato.
    """
    dados_publicados = REPOSITORIO / "public" / "data"
    if not dados_publicados.is_dir():
        pytest.skip("public/data/ ausente (checkout limpo); injeções cobrem a detecção")
    ocorrencias = varrer(dados_publicados)
    assert ocorrencias == [], "Termo proibido em JSON publicado: " + "; ".join(ocorrencias)
