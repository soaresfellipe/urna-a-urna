"""Leitor do subconjunto de YAML usado no config.yaml.

O projeto restringe as dependências de runtime ao duckdb, então o carregamento
do config.yaml usa este leitor mínimo em vez do PyYAML. Ele cobre apenas o
subconjunto block-style que o config.yaml emprega: mapeamentos aninhados,
listas de escalares e de mapeamentos, comentários, strings entre aspas e
escalares tipados (int, float, bool, null). Recursos fora desse subconjunto
(âncoras, blocos literais, flow styles complexos) não são suportados.
"""

from __future__ import annotations


class YamlError(ValueError):
    """Erro de sintaxe no subconjunto de YAML suportado."""

    def __init__(self, mensagem: str, numero_linha: int | None = None) -> None:
        prefixo = f"linha {numero_linha}: " if numero_linha else ""
        super().__init__(f"{prefixo}{mensagem}")


def _sem_comentario(linha: str) -> str:
    """Remove comentário final, preservando '#' dentro de aspas."""
    em_aspas: str | None = None
    for indice, caractere in enumerate(linha):
        if em_aspas:
            if caractere == em_aspas:
                em_aspas = None
        elif caractere in ('"', "'"):
            em_aspas = caractere
        elif caractere == "#" and (indice == 0 or linha[indice - 1] in " \t"):
            return linha[:indice]
    return linha


def _tokenizar(texto: str) -> list[tuple[int, int, str]]:
    """Retorna (número da linha, indentação, conteúdo) das linhas úteis."""
    linhas: list[tuple[int, int, str]] = []
    for numero, bruta in enumerate(texto.splitlines(), 1):
        limpa = _sem_comentario(bruta)
        conteudo = limpa.strip()
        if not conteudo or conteudo == "---":
            continue
        indentacao = len(limpa) - len(limpa.lstrip(" "))
        linhas.append((numero, indentacao, conteudo))
    return linhas


def _dividir_chave(conteudo: str, numero: int) -> tuple[str, bool, str]:
    """Separa 'chave: resto'; a chave termina em ':' seguido de espaço ou fim."""
    em_aspas: str | None = None
    for indice, caractere in enumerate(conteudo):
        if em_aspas:
            if caractere == em_aspas:
                em_aspas = None
        elif caractere in ('"', "'"):
            em_aspas = caractere
        elif caractere == ":" and (indice + 1 == len(conteudo) or conteudo[indice + 1] in " \t"):
            chave = conteudo[:indice].strip()
            if not chave:
                raise YamlError("chave vazia", numero)
            return chave, True, conteudo[indice + 1 :].strip()
    return conteudo, False, ""


def _escalar(texto: str, numero: int) -> object:
    if not texto:
        return None
    if texto[0] == '"':
        if len(texto) < 2 or not texto.endswith('"'):
            raise YamlError("string entre aspas duplas sem fechamento", numero)
        return texto[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if texto[0] == "'":
        if len(texto) < 2 or not texto.endswith("'"):
            raise YamlError("string entre aspas simples sem fechamento", numero)
        return texto[1:-1].replace("''", "'")
    if texto in ("true", "True"):
        return True
    if texto in ("false", "False"):
        return False
    if texto in ("null", "Null", "~"):
        return None
    try:
        return int(texto.replace("_", ""))
    except ValueError:
        pass
    try:
        return float(texto)
    except ValueError:
        pass
    return texto


def _parse_bloco(linhas: list[tuple[int, int, str]], i: int, min_indent: int) -> tuple[object, int]:
    numero, indentacao, conteudo = linhas[i]
    if indentacao <= min_indent:
        raise YamlError("indentação inesperada", numero)
    if conteudo == "-" or conteudo.startswith("- "):
        return _parse_lista(linhas, i, indentacao)
    return _parse_mapeamento(linhas, i, indentacao)


def _parse_mapeamento(
    linhas: list[tuple[int, int, str]], i: int, indentacao: int
) -> tuple[dict[str, object], int]:
    resultado: dict[str, object] = {}
    while i < len(linhas):
        numero, ind, conteudo = linhas[i]
        if ind < indentacao:
            break
        if ind > indentacao:
            raise YamlError("indentação inesperada", numero)
        chave, tem_valor, resto = _dividir_chave(conteudo, numero)
        if chave in resultado:
            raise YamlError(f"chave duplicada: {chave}", numero)
        if tem_valor and resto:
            resultado[chave] = _escalar(resto, numero)
            i += 1
            continue
        # 'chave:' sem valor na linha: o bloco vem na sequência, mais indented
        # (mapeamento) ou no mesmo nível (lista).
        if i + 1 < len(linhas):
            _, prox_ind, prox_conteudo = linhas[i + 1]
            if prox_ind > indentacao:
                resultado[chave], i = _parse_bloco(linhas, i + 1, indentacao)
                continue
            if prox_ind == indentacao and (prox_conteudo == "-" or prox_conteudo.startswith("- ")):
                resultado[chave], i = _parse_lista(linhas, i + 1, indentacao)
                continue
        resultado[chave] = None
        i += 1
    return resultado, i


def _parse_lista(
    linhas: list[tuple[int, int, str]], i: int, indentacao: int
) -> tuple[list[object], int]:
    itens: list[object] = []
    while i < len(linhas):
        numero, ind, conteudo = linhas[i]
        if ind < indentacao:
            break
        if ind > indentacao:
            raise YamlError("indentação inesperada em lista", numero)
        if not (conteudo == "-" or conteudo.startswith("- ")):
            break
        restante = conteudo[2:].strip() if conteudo.startswith("- ") else ""
        if not restante:
            # '-' isolado: o item vem do bloco seguinte.
            i += 1
            if i < len(linhas) and linhas[i][1] > indentacao:
                valor, i = _parse_bloco(linhas, i, indentacao)
            else:
                valor = None
            itens.append(valor)
        elif _dividir_chave(restante, numero)[1]:
            # Item é mapeamento: reescreve a linha no indent virtual após '- '.
            indent_virtual = indentacao + (len(conteudo) - len(conteudo[2:].lstrip()))
            linhas[i] = (numero, indent_virtual, restante)
            valor, i = _parse_mapeamento(linhas, i, indent_virtual)
            itens.append(valor)
        else:
            itens.append(_escalar(restante, numero))
            i += 1
    return itens, i


def loads(texto: str) -> dict[str, object]:
    """Analisa o texto YAML (subconjunto suportado) em dicionário."""
    linhas = _tokenizar(texto)
    if not linhas:
        return {}
    valor, proximo = _parse_bloco(linhas, 0, -1)
    if not isinstance(valor, dict):
        raise YamlError("o documento deve ser um mapeamento", linhas[0][0])
    if proximo != len(linhas):
        raise YamlError("conteúdo fora de bloco", linhas[proximo][0])
    return valor
