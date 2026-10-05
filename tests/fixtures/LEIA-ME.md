# Fixtures sintéticas — LEIA-ME

Gerador de dados sintéticos **fiéis ao esquema real** dos CSVs do TSE, para testes
rápidos do pipeline (`uv run pytest -q` roda em segundos; nenhum arquivo grande
entra na suíte). Fonte dos fatos de esquema: `library/tse-schema.md` (missão) e
`data/reports/schema-2022-df.md` (repositório, gitignored).

Módulo usado: `tests/fixtures/gerador.py` (sem dependências além da biblioteca
padrão). Este documento explica o modelo, as APIs prontas e como injetar anomalias
— pensado para reuso pelos módulos 2 (divergência) e 3 (voto cruzado).

## O que o gerador garante (fidelidade ao esquema real)

- Encoding **Latin-1**, delimitador `;`, **todos os campos entre aspas**
  (`csv.QUOTE_ALL`), terminador de linha `\r\n`, cabeçalho na 1ª linha.
- Colunas **exatas** do BU (45, `COLUNAS_BU`) e da totalização (26,
  `COLUNAS_TOT`), **na ordem original** dos arquivos reais de 2022.
- `#NULO#` marcando valor ausente (ex.: `SQ_CANDIDATO` de branco/nulo,
  `DS_AGREGADAS`, partido de votável não nominal); `#NULO#` = -1 na tipagem.
- BU: 1 linha por **urna × cargo × votável** (`NR_URNA_EFETIVADA` determinístico
  por (zona, seção) — 1 urna por seção, como no DF real); branco = votável 95,
  nulo = 96; nomes de urna vs. nomes civis na totalização (cruzamento sempre por
  `NR_VOTAVEL`).
- Totalização: 1 linha por **seção × cargo × votável**, sem aptos/comparecimento.
  Com `incluir_turno_2=True` (padrão do arquivo nacional), o CSV mistura 1º e
  2º turno com votos diferentes — a pegadinha real que a ingestão filtra.
- Campos não consumidos pelo pipeline recebem constantes plausíveis copiadas dos
  arquivos reais (datas, cargas da urna, juntas apuradoras); só os campos usados
  precisam permanecer coerentes quando o gerador for editado.

## O modelo do mini-pleito

Hierarquia parametrizável: **zonas → locais de votação → seções**, cada seção com
um pleito **por cargo** (`PleitoCargo`: `aptos`, `comparecimento`, `votos` por
votável, e `votos_totalizacao` opcional). A pegadinha real dos aptos que variam
por cargo fica exercitada quando os cargos da mesma seção recebem números
distintos (o `pleito_padrao()` já faz isso).

Construção manual (controle total sobre locais/zonas/seções):

```python
from tests.fixtures import gerador

pleito = gerador.PleitoSintetico()
pleito.adicionar_secao(
    zona=1,
    secao=12,
    local_id=1015,
    local_nome="ESCOLA CLASSE CONCEIÇÃO DE MACEDO",
    local_endereco="SQS 103 BLOCO A - ASA SUL",
    cargos={
        gerador.CARGO_PRESIDENTE: gerador.pleito_do_cargo(
            gerador.CARGO_PRESIDENTE,
            aptos=267,
            votos={22: 120, 13: 80, 15: 30, 95: 8, 96: 12},
        ),
    },
)
```

Regras do modelo:

- `pleito_do_cargo(cargo_id, aptos, votos, comparecimento=None)`: no pleito
  íntegro o comparecimento é a **soma dos votos** (R1 sem violação); passe
  `comparecimento` só em cenários especiais — as anomalias R1/R2 têm injetores
  próprios.
- Seção duplicada (mesma zona+seção), seção/cargo ausente ao injetar: `ValueError`
  orientado.
- Cargos definidos: `CARGO_PRESIDENTE` (1), `CARGO_GOVERNADOR` (3),
  `CARGO_SENADOR` (5), `CARGO_DEPUTADO_FEDERAL` (6), `CARGO_DEPUTADO_DISTRITAL`
  (8); branco/nulo: `VOTAVEL_BRANCO` (95) / `VOTAVEL_NULO` (96).

## Pleitos prontos

- `pleito_padrao()` — mini-pleito do **contrato de validação**
  (VAL-INT-001/002/003): 2 zonas, 3 locais, 4 seções; Presidente e Governador com
  pleitos diferentes por cargo na mesma seção. Números de referência: a seção
  (zona 1, seção 2) Presidente soma 250 votos; o Governador da seção (1, 30) tem
  aptos 300; o Presidente da seção (2, 40) tem 120 votos no votável 22.
- `pleito_grade(zonas=(1, 2), locais_por_zona=2, secoes_por_local=8)` — grade
  determinística para os **módulos 2 e 3**: `locais_por_zona` locais por zona com
  `secoes_por_local` seções cada (padrão ≥ 6 por local = referência local do
  módulo 2; zona total = locais × seções). Votos por fórmulas determinísticas com
  variação leve entre seções, para exercitar mediana/MAD. Parâmetros de pleito
  (aptos/votos base) podem ser ajustados por seção após a criação, com
  `pleito.buscar_pleito_do_cargo(zona, secao, cargo_id)`.

## Injeção de anomalias (módulo 1)

Funções sobre o modelo, ANTES da escrita dos CSVs; cada uma garante que a ÚNICA
regra violada do cenário é a injetada:

- `injetar_r1(pleito, zona=…, secao=…, cargo_id=…, comparecimento=…)`: muda o
  comparecimento sem mexer na soma dos votos (R1: soma ≠ comparecimento).
- `injetar_r2(pleito, zona=…, secao=…, cargo_id=…, comparecimento=…)`: exige
  comparecimento > aptos; a soma dos votos acompanha o novo comparecimento
  (absorvida no menor votável nominal), para que só R2 seja violada.
- `injetar_r3(pleito, zona=…, secao=…, cargo_id=…, votavel=…, votos_totalizacao=…)`:
  um votável da totalização passa a divergir do BU (o resto do lado da
  totalização é copiado do BU).

Exceções legítimas (VAL-INT-004) para testes de falso-positivo: senador com
multiplicador no `config.yaml` (R1 compara contra multiplicador × comparecimento —
monte a seção com a soma = multiplicador × comparecimento e não injete anomalia);
seções agregadas (`DS_AGREGADAS`) ainda não são emitidas pelo gerador (nenhuma na
base sintética); `SG_UF='VT'`/`'ZZ'` podem ser exercitadas ajustando o campo `uf`
do `PleitoSintetico`.

## Escrita e empacotamento

Direto em CSV (testes unitários de esquema):

```python
gerador.escrever_boletim_de_urna(caminho_csv, pleito)
gerador.escrever_totalizacao(caminho, pleito, cargos=[gerador.CARGO_GOVERNADOR], nacional=False)
gerador.csv_boletim_de_urna(pleito)  # bytes, quando preferir
```

- `cargos=` filtra os cargos do CSV de totalização; `nacional=True` escreve o
  formato do arquivo BR (`SG_UE='BR'`, `TP_ABRANGENCIA='F'`) — só o Presidente
  vem do arquivo nacional, como nos dados reais.
- `incluir_turno_2=True` adiciona linhas do 2º turno (padrão do arquivo BR).

Pacote completo no formato do download (zips + manifesto por papel em
`data/raw/manifest.json`) — os testes de integração ingerem pelo caminho REAL do
pipeline:

```python
gerador.empacotar(tmp_path, pleito)  # zips + manifest.json em tmp_path/raw/
pipeline.ingest.ingerir(uf="DF", turno=1, data_dir=tmp_path)
achados = pipeline.integridade.conferir(
    uf="DF", turno=1, ano=2022, data_dir=tmp_path, config=config
)
```

Papéis produzidos: `boletim_de_urna`, `totalizacao_uf` (cargos estaduais
presentes), `totalizacao_presidente` (nacional, com 2º turno misturado) — o mesmo
contrato que `pipeline/download.py` consome.

## Exemplos vivos

Os usos canônicos estão nos testes (copie de lá):

- `tests/unit/test_gerador_fixtures.py` — fidelidade de esquema campo a campo e
  comportamento de cada injetor.
- `tests/integration/test_integridade_fixtures.py` — ciclo completo
  (empacotar → ingerir → conferir) com cada anomalia detectada exatamente e a
  cópia íntegra sem achado.
