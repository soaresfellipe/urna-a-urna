# Urna a Urna

Triagem estatística de seções eleitorais atípicas com os dados públicos do TSE (piloto: DF, validação com 2022). A ferramenta analisa os Boletins de Urna seção por seção e produz uma lista ranqueada de seções atípicas para revisão humana.

## Instalação

Exige [uv](https://docs.astral.sh/uv/) e Python >= 3.12.

```
uv sync
```

## Uso

```
uv run python -m pipeline --uf DF --ano 2022 --turno 1
```

Todos os limiares, pares e referências ficam em `config.yaml` — única fonte de verdade.

## Testes e verificação

```
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```
