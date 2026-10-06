# Urna a Urna

Triagem estatística de seções eleitorais atípicas com os dados públicos do TSE (piloto: DF; validação com 2022, 1º turno; ano e UF são parâmetros de linha de comando). A ferramenta analisa os Boletins de Urna seção por seção e produz uma lista ranqueada de seções atípicas para revisão humana, com painel estático de consulta.

Usa exclusivamente o vocabulário "atípico" e "divergente": o objetivo é apontar candidatos a revisão humana, nunca afirmar coisa alguma sobre o resultado da apuração.

## Limites do projeto

- **Escopo de triagem.** Uma seção atípica é um sinal estatístico para revisão humana, **não** uma evidência de problema na apuração. Podem existir causas ordinárias para um padrão local distinto (perfil demográfico do local, tamanho pequeno da seção, referência local imperfeita). Cada sinalização traz o valor observado, a referência usada, o z e o q-valor, para que a revisão humana decida.
- **Cobertura.** Piloto no DF, validação com os dados de 2022 (1º turno); cargos analisados: Presidente e Governador (os demais cargos entram só nas checagens de integridade).
- **Vocabulário controlado.** Os termos proibidos pelo contrato — e suas flexões — não aparecem em código, interface, dados publicados ou documentação; o texto da triagem usa exclusivamente "atípico" e "divergente". Há guarda automatizada em teste que barra qualquer ocorrência.
- **Limites de publicação.** Nenhum arquivo emitido em `public/data/` excede 25 MiB; o total de arquivos emitidos fica abaixo de 20 mil; dado bruto (`data/`) nunca vai para o git. Os limites vêm de `config.yaml` (bloco `publicacao`) e são conferidos por testes.
- **Fonte dos dados.** Boletins de Urna e resultados por seção do portal de dados abertos do TSE (CKAN), licença CC-BY. Os checksums calculados ficam registrados em `data/raw/manifest.json`.

## Instalação

Exige [uv](https://docs.astral.sh/uv/) e Python >= 3.12.

```
uv sync
```

O `uv.lock` é commitado: a entrada acima reproduz exatamente o ambiente de desenvolvimento (runtime: `duckdb` e `pyyaml`; dev: `pytest` e `ruff`).

## Comando único

```
uv run python -m pipeline --uf DF --ano 2022 --turno 1
```

A execução completa — download (aproveitando o cache), ingestão no DuckDB, conferência de totais por candidato, módulos 1 (integridade), 2 (divergência) e 3 (voto cruzado), ranking e emissão — roda em uma única invocação, com progresso e contagens por módulo no terminal, e grava:

- `public/data/index.json` — limiares (espelho de `config.yaml`), conferência de totais por candidato, contagens por módulo, checksums das fontes e manifesto dos arquivos emitidos;
- `public/data/sinalizacoes.json` (ou particionado em `public/data/sinalizacoes/`) — ranking combinado com frase explicativa por achado;
- `public/data/zonas/{zona}.json` — números por seção para o painel.

Opções úteis: `--data-dir` (default `data`), `--out-dir` (default `public/data`) e `--etapas` para execução parcial (`download,ingest,totais,integridade,divergencia,cruzado,ranking,emitir`; por ex.: `--etapas ingest`). Re-executar o comando não baixa nada de novo: com o checksum conferindo, o cache em `data/raw/` é aproveitado e o resultado é idêntico (exceto a data de geração no `index.json`). `data/` e `public/data/` são gitignored: `data/` contém downloads brutos e o banco DuckDB local; `public/data/` é artefato gerado.

## Configuração

`config.yaml` é a única fonte de verdade: pleito padrão do CLI, limiares dos três módulos, pares do voto cruzado, multiplicadores, níveis de referência, particionamento e limites de publicação. Alterar um limiar muda a triagem sem tocar em código e o novo valor aparece espelhado no `public/data/index.json`.

## Testes

```
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

A suíte (unitária e integração, sobre fixtures sintéticas fiéis ao esquema do TSE — Latin-1, `;`, aspas) inclui as **guardas de publicação**: vocabulário controlado, nenhum arquivo emitido acima de 25 MiB, total abaixo de 20 mil e dado bruto (`data/`) nunca trackeado no git. Os testes não dependem de `public/data/` preexistente: num checkout limpo eles continuam válidos. Quando a emissão existir (após rodar o comando de cima), as guardas também a verificam.

## Painel local

Com `public/data/` gerado pelo comando único, sirva a pasta `public/`:

```
python3 -m http.server 3123 --directory public
```

Abra `http://127.0.0.1:3123/index.html`. `index.html` tem a tabela ranqueada com filtros por município, zona e módulo, contador de resultados, exportação CSV do conteúdo filtrado e link para o detalhe de cada seção (`secao.html`); `metodologia.html` explica cada teste com os limiares reais lidos do `index.json` e as limitações da triagem.

## Deploy no Cloudflare Pages (preparado, não executado)

Os workflows em `.github/workflows/` estão prontos e **nada é publicado até o dono habilitar**:

- **`ci.yml`** — em push na branch principal e em pull request: `uv sync` → `pytest` (com as guardas dentro da suíte) → `ruff check` → `ruff format --check`. Não precisa de segredo.
- **`pipeline-deploy.yml`** — em push na branch principal e acionamento manual (`workflow_dispatch`): `uv sync` → pipeline completo (`python -m pipeline --uf DF --ano 2022 --turno 1`, que gera `public/data` antes das guardas e da publicação) → guardas de publicação sobre a pasta emitida → `wrangler pages deploy public`. Os segredos aparecem como **placeholders** (`${{ secrets.CLOUDFLARE_API_TOKEN }}` e `${{ secrets.CLOUDFLARE_ACCOUNT_ID }}`) — nenhum token é fixado no repositório.

Para habilitar o deploy:

1. Publique o repositório no GitHub.
2. Defina os dois segredos em *Settings → Secrets and variables → Actions*:
   - `CLOUDFLARE_API_TOKEN` (token de API com permissão de edição no Cloudflare Pages);
   - `CLOUDFLARE_ACCOUNT_ID` (identificador da conta Cloudflare).
3. Crie o projeto no Cloudflare Pages e ajuste, se necessário, o `--project-name` (o workflow usa `urna-a-urna`).

Sem os segredos definidos, o passo de deploy do `pipeline-deploy.yml` falha por autenticação — nenhuma publicação acontece. Durante o desenvolvimento desta missão, nenhum deploy foi executado.
