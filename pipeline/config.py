"""Carrega e valida o config.yaml — única fonte de limiares, pares e referências.

O código nunca fixa limiares estatísticos: módulos leem tudo daqui. Qualquer
chave ausente, tipo errado ou valor fora da faixa interrompe o pipeline com
mensagem que nomeia o caminho da chave.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.erros import ConfigError
from pipeline.mini_yaml import YamlError, loads

RAIZ = Path(__file__).resolve().parent.parent
CAMINHO_PADRAO = RAIZ / "config.yaml"

_NIVEIS_REFERENCIA = ("municipio", "local", "zona")
_FALLBACKS_REFERENCIA = ("local", "zona")


def carregar_config(caminho: Path | None = None) -> dict[str, object]:
    """Lê o config.yaml e devolve o dicionário validado."""
    caminho = caminho if caminho is not None else CAMINHO_PADRAO
    try:
        texto = Path(caminho).read_text(encoding="utf-8")
    except OSError as erro:
        raise ConfigError(
            f"Não foi possível ler o arquivo de configuração '{caminho}': {erro}. "
            "Confirme que o config.yaml está na raiz do repositório."
        ) from erro
    try:
        dados = loads(texto)
    except YamlError as erro:
        raise ConfigError(f"config.yaml: erro de sintaxe ({erro}).") from erro
    return validar_config(dados)


def _mapeamento(dados: object, caminho: str) -> dict[str, object]:
    if not isinstance(dados, dict):
        raise ConfigError(f"config.yaml: '{caminho}' deve ser um mapeamento.")
    return dados


def _obter(dados: dict[str, object], caminho: str) -> object:
    atual: object = dados
    percorrido: list[str] = []
    for chave in caminho.split("."):
        percorrido.append(chave)
        nivel = _mapeamento(atual, ".".join(percorrido[:-1]) or "raiz")
        if chave not in nivel:
            raise ConfigError(
                f"config.yaml: chave ausente: {'.'.join(percorrido)} — "
                "compare com o dicionário da configuração de referência."
            )
        atual = nivel[chave]
    return atual


def _numero(
    dados: dict[str, object],
    caminho: str,
    tipo: type,
    minimo: float = 0.0,
) -> int | float:
    valor = _obter(dados, caminho)
    if isinstance(valor, bool) or not isinstance(valor, tipo):
        nome = "inteiro" if tipo is int else "número"
        raise ConfigError(f"config.yaml: '{caminho}' deve ser {nome} (recebido: {valor!r}).")
    if valor < minimo:
        raise ConfigError(f"config.yaml: '{caminho}' deve ser >= {minimo} (recebido: {valor!r}).")
    return valor


def _texto(
    dados: dict[str, object], caminho: str, permitidos: tuple[str, ...] | None = None
) -> str:
    valor = _obter(dados, caminho)
    if not isinstance(valor, str) or not valor.strip():
        raise ConfigError(f"config.yaml: '{caminho}' deve ser texto não vazio.")
    if permitidos and valor not in permitidos:
        lista = ", ".join(permitidos)
        raise ConfigError(
            f"config.yaml: '{caminho}' deve ser um de: {lista} (recebido: '{valor}')."
        )
    return valor


def _validar_limiares(dados: dict[str, object], modulo: str) -> None:
    _numero(dados, f"limiares.{modulo}.z_robusto_min", float, minimo=0.0)
    _numero(dados, f"limiares.{modulo}.z_tamanho_min", float, minimo=0.0)
    _numero(dados, f"limiares.{modulo}.dif_minima_pp", float, minimo=0.0)
    alfa = _numero(dados, f"limiares.{modulo}.fdr_alpha", float, minimo=0.0)
    if alfa >= 1.0:
        raise ConfigError(
            f"config.yaml: 'limiares.{modulo}.fdr_alpha' deve ser < 1 (recebido: {alfa})."
        )


def validar_config(dados: dict[str, object]) -> dict[str, object]:
    """Valida estrutura e tipos do config; devolve os dados quando válidos."""
    _mapeamento(_obter(dados, "pleito"), "pleito")
    uf = _texto(dados, "pleito.uf")
    if len(uf) != 2 or not uf.isalpha():
        raise ConfigError(f"config.yaml: 'pleito.uf' deve ser a sigla da UF (recebido: '{uf}').")
    _numero(dados, "pleito.ano", int, minimo=1940)
    turno = _numero(dados, "pleito.turno", int, minimo=1)
    if turno > 2:
        raise ConfigError(f"config.yaml: 'pleito.turno' deve ser 1 ou 2 (recebido: {turno}).")

    _texto(dados, "caminhos.data_dir")
    _texto(dados, "caminhos.out_dir")

    _validar_limiares(dados, "divergencia")
    _validar_limiares(dados, "cruzado")
    _numero(dados, "limiares.divergencia.min_secoes_local", int, minimo=1)
    _numero(dados, "limiares.divergencia.min_secoes_zona", int, minimo=1)
    _numero(dados, "limiares.divergencia.min_votos_validos_secao", int, minimo=1)

    _validar_multiplicadores(
        _mapeamento(
            _obter(dados, "multiplicador_votos_por_eleitor"), "multiplicador_votos_por_eleitor"
        )
    )
    _validar_pares(_mapeamento(_obter(dados, "voto_cruzado"), "voto_cruzado"))

    _texto(dados, "referencia.nivel", _NIVEIS_REFERENCIA)
    _texto(dados, "referencia.fallback", _FALLBACKS_REFERENCIA)

    _texto(dados, "particao.sinalizacoes", ("unico", "por_zona"))
    _texto(dados, "particao.nivel")
    _numero(dados, "particao.tamanho_max_mb", int, minimo=1)

    url = _texto(dados, "fontes.ckan_base_url")
    if not url.startswith("http"):
        raise ConfigError(
            f"config.yaml: 'fontes.ckan_base_url' deve ser uma URL http(s) (recebido: '{url}')."
        )
    conjuntos = _mapeamento(_obter(dados, "fontes.conjuntos"), "fontes.conjuntos")
    for chave in ("boletim_de_urna", "totalizacao"):
        if chave not in conjuntos or "{ano}" not in str(conjuntos[chave]):
            raise ConfigError(
                f"config.yaml: 'fontes.conjuntos.{chave}' deve ser o id do conjunto CKAN "
                f"com '{{ano}}' para substituição (recebido: {conjuntos.get(chave)!r})."
            )
    return dados


def _validar_multiplicadores(multiplicadores: dict[str, object]) -> None:
    if not multiplicadores:
        raise ConfigError(
            "config.yaml: 'multiplicador_votos_por_eleitor' deve ter ao menos um ano."
        )
    for ano, cargos in multiplicadores.items():
        if not str(ano).isdigit():
            raise ConfigError(
                f"config.yaml: chave de ano inválida em 'multiplicador_votos_por_eleitor': {ano!r}."
            )
        for cargo, valor in _mapeamento(cargos, f"multiplicador_votos_por_eleitor.{ano}").items():
            if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
                raise ConfigError(
                    f"config.yaml: 'multiplicador_votos_por_eleitor.{ano}.{cargo}' deve ser "
                    f"inteiro >= 1 (recebido: {valor!r})."
                )


def _validar_pares(voto_cruzado: dict[str, object]) -> None:
    pares = voto_cruzado.get("pares")
    if not isinstance(pares, list) or not pares:
        raise ConfigError(
            "config.yaml: 'voto_cruzado.pares' deve ser uma lista com ao menos um par."
        )
    for indice, par in enumerate(pares):
        contexto = f"voto_cruzado.pares[{indice}]"
        par_map = _mapeamento(par, contexto)
        if not isinstance(par_map.get("rotulo"), str) or not par_map["rotulo"].strip():
            raise ConfigError(f"config.yaml: '{contexto}.rotulo' deve ser texto não vazio.")
        for lado in ("presidente", "governador"):
            if lado not in par_map:
                raise ConfigError(f"config.yaml: chave ausente: {contexto}.{lado}")
            numero = _mapeamento(par_map[lado], f"{contexto}.{lado}").get("numero")
            if isinstance(numero, bool) or not isinstance(numero, int):
                raise ConfigError(
                    f"config.yaml: '{contexto}.{lado}.numero' deve ser o número do candidato "
                    f"(inteiro; recebido: {numero!r})."
                )


def multiplicador_por_eleitor(
    config: dict[str, object], ano: int, cargo: str, padrao: int = 1
) -> int:
    """Multiplicador de votos por eleitor para (ano, cargo); padrão 1 quando não definido."""
    multiplicadores = _mapeamento(
        config.get("multiplicador_votos_por_eleitor", {}), "multiplicador_votos_por_eleitor"
    )
    do_ano = _mapeamento(
        multiplicadores.get(str(ano), {}), f"multiplicador_votos_por_eleitor.{ano}"
    )
    valor = do_ano.get(cargo, padrao)
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < 1:
        raise ConfigError(f"config.yaml: multiplicador para {ano}/{cargo} inválido: {valor!r}.")
    return valor
