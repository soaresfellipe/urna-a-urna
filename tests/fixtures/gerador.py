"""Gerador de fixtures sintéticas fiéis ao esquema real dos CSVs do TSE.

Escreve CSVs no formato VERIFICADO dos arquivos oficiais
(library/tse-schema.md): encoding Latin-1, delimitador ';', TODOS os campos
entre aspas, colunas exatas do Boletim de Urna (45) e da totalização (26) na
ordem original, '#NULO#' marcando valor ausente e — no arquivo nacional de
totalização — o 1º e o 2º turno misturados (a ingestão filtra NR_TURNO).

O modelo é um mini-pleito parametrizável: zonas → locais de votação →
seções, cada seção com um pleito POR CARGO (aptos/comparecimento/votos por
votável) — a pegadinha real dos aptos que variam por cargo fica exercitada
quando os cargos da mesma seção recebem números distintos.

Campos NÃO consumidos pelo pipeline recebem constantes plausíveis copiadas
dos arquivos reais (datas de geração, cargas da urna, juntas apuradoras);
quem editar o gerador só precisa manter coerentes os campos usados
(NR_ZONA, NR_SECAO, cargos, votáveis, votos, aptos/comparecimento/abstenções).

Anomalias (módulo 1) são injetadas sobre cópia do modelo por funções
explícitas: `injetar_r1` (soma ≠ comparecimento), `injetar_r2` (comparecimento
> aptos) e `injetar_r3` (BU ≠ totalização em um votável).

Uso (módulos 2 e 3): monte o pleito com `pleito_padrao()` (números exatos do
contrato de validação) ou `pleito_grade()` (grupos de seções por local, para
comparações estatísticas), injete o desvio desejado e leia os CSVs gerados
com o mesmo leitor do pipeline (`pipeline.ingest.ingerir`). Ver LEIA-ME.md.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Códigos fixos do TSE (pipeline/ingest.py e dicionário de dados).
CARGO_PRESIDENTE = 1
CARGO_GOVERNADOR = 3
CARGO_SENADOR = 5
CARGO_DEPUTADO_FEDERAL = 6
CARGO_DEPUTADO_DISTRITAL = 8

VOTAVEL_BRANCO = 95
VOTAVEL_NULO = 96

NOMES_CARGO = {
    CARGO_PRESIDENTE: "Presidente",
    CARGO_GOVERNADOR: "Governador",
    CARGO_SENADOR: "Senador",
    CARGO_DEPUTADO_FEDERAL: "Deputado Federal",
    CARGO_DEPUTADO_DISTRITAL: "Deputado Distrital",
}

# BU: CD_ELEICAO 544 federal, 546 estadual (nomes como nos arquivos reais).
ELEICOES_POR_CARGO = {
    CARGO_PRESIDENTE: (544, "Federal"),
    CARGO_GOVERNADOR: (546, "Estadual"),
    CARGO_SENADOR: (546, "Estadual"),
    CARGO_DEPUTADO_FEDERAL: (546, "Estadual"),
    CARGO_DEPUTADO_DISTRITAL: (546, "Estadual"),
}

# BU usa nome de urna; a totalização usa nome civil completo — o cruzamento
# é por NR_VOTAVEL, nunca por nome (pegadinha real, library/tse-schema.md).
NOMES_BU = {22: "JAIR BOLSONARO", 13: "LULA", 15: "IBANEIS ROCHA", 43: "LEANDRO GRASS"}
NOMES_TOT = {
    22: "JAIR MESSIAS BOLSONARO",
    13: "LUIZ INÁCIO LULA DA SILVA",
    15: "IBANEIS ROCHA",
    43: "LEANDRO GRASS",
}
PARTIDOS = {
    22: ("22", "PL", "Partido Liberal"),
    13: ("13", "PT", "Partido dos Trabalhadores"),
    15: ("15", "MDB", "Movimento Democrático Brasileiro"),
    43: ("43", "PV", "Partido Verde"),
}
TIPO_VOTAVEL = {VOTAVEL_BRANCO: (2, "Branco"), VOTAVEL_NULO: (3, "Nulo")}

# Colunas EXATAS dos arquivos reais, na ordem original
# (data/reports/schema-2022-df.md do repositório; espelhadas em
# library/tse-schema.md).
COLUNAS_BU = (
    "DT_GERACAO",
    "HH_GERACAO",
    "ANO_ELEICAO",
    "CD_TIPO_ELEICAO",
    "NM_TIPO_ELEICAO",
    "CD_PLEITO",
    "DT_PLEITO",
    "NR_TURNO",
    "CD_ELEICAO",
    "DS_ELEICAO",
    "SG_UF",
    "CD_MUNICIPIO",
    "NM_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "NR_LOCAL_VOTACAO",
    "CD_CARGO_PERGUNTA",
    "DS_CARGO_PERGUNTA",
    "NR_PARTIDO",
    "SG_PARTIDO",
    "NM_PARTIDO",
    "DT_BU_RECEBIDO",
    "QT_APTOS",
    "QT_COMPARECIMENTO",
    "QT_ABSTENCOES",
    "CD_TIPO_URNA",
    "DS_TIPO_URNA",
    "CD_TIPO_VOTAVEL",
    "DS_TIPO_VOTAVEL",
    "NR_VOTAVEL",
    "NM_VOTAVEL",
    "QT_VOTOS",
    "NR_URNA_EFETIVADA",
    "CD_CARGA_1_URNA_EFETIVADA",
    "CD_CARGA_2_URNA_EFETIVADA",
    "CD_FLASHCARD_URNA_EFETIVADA",
    "DT_CARGA_URNA_EFETIVADA",
    "DS_CARGO_PERGUNTA_SECAO",
    "DS_AGREGADAS",
    "DT_ABERTURA",
    "DT_ENCERRAMENTO",
    "QT_ELEITORES_BIOMETRIA_NH",
    "DT_EMISSAO_BU",
    "NR_JUNTA_APURADORA",
    "NR_TURMA_APURADORA",
)
COLUNAS_TOT = (
    "DT_GERACAO",
    "HH_GERACAO",
    "ANO_ELEICAO",
    "CD_TIPO_ELEICAO",
    "NM_TIPO_ELEICAO",
    "NR_TURNO",
    "CD_ELEICAO",
    "DS_ELEICAO",
    "DT_ELEICAO",
    "TP_ABRANGENCIA",
    "SG_UF",
    "SG_UE",
    "NM_UE",
    "CD_MUNICIPIO",
    "NM_MUNICIPIO",
    "NR_ZONA",
    "NR_SECAO",
    "CD_CARGO",
    "DS_CARGO",
    "NR_VOTAVEL",
    "NM_VOTAVEL",
    "QT_VOTOS",
    "NR_LOCAL_VOTACAO",
    "SQ_CANDIDATO",
    "NM_LOCAL_VOTACAO",
    "DS_LOCAL_VOTACAO_ENDERECO",
)

# Constantes copiadas dos arquivos reais (mesma levantada em
# data/reports/schema-2022-df.md).
_DT_GERACAO = "05/10/2022"
_HH_GERACAO = "13:49:49"
_DT_PLEITO = "02/10/2022"
_DT_BU_RECEBIDO = "02/10/2022 17:18:48"
_DT_ABERTURA = "02/10/2022 08:00:01"
_DT_ENCERRAMENTO = "02/10/2022 17:04:56"
_DT_EMISSAO_BU = "02/10/2022 17:08:39"
_DT_CARGA_URNA = "25/09/2022 08:28:00"
_DT_ELEICAO = _DT_PLEITO
_CARGA_1 = "231.434.345.134.096.965."
_CARGA_2 = "274.620"
_FLASHCARD = "A6920B27"
_CARGO_PERGUNTA_SECAO = "1 - 1"
_PLEITO = "406"
_JUNTA = "-1"


# ---------------------------------------------------------------------------
# Modelo do mini-pleito
# ---------------------------------------------------------------------------


@dataclass
class PleitoCargo:
    """Pleito de UM cargo numa seção: aptos, comparecimento e votos por votável."""

    cargo_id: int
    aptos: int
    comparecimento: int
    votos: dict[int, int]
    votos_totalizacao: dict[int, int] | None = None
    cargo: str = ""
    abstencoes: int = 0

    def __post_init__(self) -> None:
        self.cargo = NOMES_CARGO[self.cargo_id]
        self.abstencoes = self.aptos - self.comparecimento

    def soma_votos(self) -> int:
        return sum(self.votos.values())

    def votos_para_totalizacao(self) -> dict[int, int]:
        """Votos do lado da totalização (= BU, salvo divergência injetada em R3)."""
        return self.votos if self.votos_totalizacao is None else self.votos_totalizacao


@dataclass
class Secao:
    zona: int
    numero: int
    local_id: int
    local_nome: str
    local_endereco: str
    cargos: dict[int, PleitoCargo]


@dataclass
class PleitoSintetico:
    """Mini-pleito parametrizável: zonas, locais e seções com pleito por cargo."""

    uf: str = "DF"
    ano: int = 2022
    turno: int = 1
    municipio_id: str = "97012"
    municipio: str = "BRASÍLIA"
    secoes: list[Secao] = field(default_factory=list)

    def adicionar_secao(
        self,
        *,
        zona: int,
        secao: int,
        local_id: int,
        local_nome: str,
        local_endereco: str,
        cargos: dict[int, PleitoCargo],
    ) -> Secao:
        chave = (zona, secao)
        if any((existente.zona, existente.numero) == chave for existente in self.secoes):
            raise ValueError(f"Seção duplicada no pleito sintético: zona {zona}, seção {secao}")
        nova = Secao(
            zona=zona,
            numero=secao,
            local_id=local_id,
            local_nome=local_nome,
            local_endereco=local_endereco,
            cargos=dict(cargos),
        )
        self.secoes.append(nova)
        return nova

    def buscar_secao(self, zona: int, secao: int) -> Secao:
        for existente in self.secoes:
            if existente.zona == zona and existente.numero == secao:
                return existente
        raise ValueError(
            f"Seção ausente no pleito sintético: zona {zona}, seção {secao} — "
            "monte a seção antes de injetar a anomalia."
        )

    def buscar_pleito_do_cargo(self, zona: int, secao: int, cargo_id: int) -> PleitoCargo:
        secao_alvo = self.buscar_secao(zona, secao)
        pleito = secao_alvo.cargos.get(cargo_id)
        if pleito is None:
            raise ValueError(
                f"Cargo {cargo_id} ausente na seção {secao} da zona {zona} — "
                "monte o pleito do cargo antes de injetar a anomalia."
            )
        return pleito

    def urna(self, zona: int, secao: int) -> int:
        """NR_URNA_EFETIVADA determinístico (uma urna por seção, como no DF real)."""
        return 2_200_000 + zona * 10_000 + secao


def pleito_do_cargo(
    cargo_id: int,
    aptos: int,
    votos: dict[int, int],
    *,
    comparecimento: int | None = None,
) -> PleitoCargo:
    """Fábrica de PleitoCargo: comparecimento = soma dos votos, salvo indicado.

    No pleito íntegro a soma dos votos é o comparecimento (R1 sem violação);
    passe `comparecimento` explicitamente apenas em cenários especiais — as
    anomalias R1/R2 são injetadas com `injetar_r1`/`injetar_r2`.
    """
    comp = sum(votos.values()) if comparecimento is None else comparecimento
    return PleitoCargo(cargo_id=cargo_id, aptos=aptos, comparecimento=comp, votos=dict(votos))


# ---------------------------------------------------------------------------
# Injeção de anomalias do módulo 1 (mexem no modelo ANTES da escrita dos CSVs)
# ---------------------------------------------------------------------------


def _ajustar_comparecimento(p: PleitoCargo, comparecimento: int) -> None:
    p.comparecimento = comparecimento
    p.abstencoes = max(p.aptos - comparecimento, 0)


def injetar_r1(
    pleito: PleitoSintetico, *, zona: int, secao: int, cargo_id: int, comparecimento: int
) -> None:
    """R1 violada: a soma dos votos do cargo passa a diferir do comparecimento."""
    _ajustar_comparecimento(pleito.buscar_pleito_do_cargo(zona, secao, cargo_id), comparecimento)


def injetar_r2(
    pleito: PleitoSintetico, *, zona: int, secao: int, cargo_id: int, comparecimento: int
) -> None:
    """R2 violada: o comparecimento passa a exceder os aptos do próprio cargo.

    A soma dos votos acompanha o novo comparecimento (absorvida no menor
    votável nominal), para que a ÚNICA regra violada do cenário seja R2.
    """
    p = pleito.buscar_pleito_do_cargo(zona, secao, cargo_id)
    if comparecimento <= p.aptos:
        raise ValueError(
            f"R2 pede comparecimento ({comparecimento}) acima dos aptos ({p.aptos})"
            " — nada para injetar."
        )
    _ajustar_comparecimento(p, comparecimento)
    delta = comparecimento - p.soma_votos()
    if delta != 0:
        nominais = sorted(v for v in p.votos if v not in (VOTAVEL_BRANCO, VOTAVEL_NULO))
        if not nominais:
            raise ValueError(
                f"R2 sem votável nominal para absorver a diferença de {delta} votos "
                f"(zona {zona}, seção {secao}, cargo {cargo_id})."
            )
        p.votos[nominais[0]] += delta
        if p.votos_totalizacao is not None:
            p.votos_totalizacao[nominais[0]] += delta


def injetar_r3(
    pleito: PleitoSintetico,
    *,
    zona: int,
    secao: int,
    cargo_id: int,
    votavel: int,
    votos_totalizacao: int,
) -> None:
    """R3 divergente: um votável da totalização passa a divergir do BU."""
    p = pleito.buscar_pleito_do_cargo(zona, secao, cargo_id)
    if p.votos_totalizacao is None:
        p.votos_totalizacao = dict(p.votos)
    p.votos_totalizacao[votavel] = votos_totalizacao


# ---------------------------------------------------------------------------
# Pleitos prontos (documentados no LEIA-ME.md)
# ---------------------------------------------------------------------------


def pleito_padrao() -> PleitoSintetico:
    """Mini-pleito do contrato de validação (VAL-INT-001/002/003).

    2 zonas, 3 locais, 4 seções; Presidente e Governador com pleitos DIFERENTES
    por cargo na mesma seção (pegadinha real dos 378 urnas). Números de
    referência: seção (1, 2) Presidente soma 250; Governador da seção (1, 30)
    tem aptos 300; Presidente da seção (2, 40) tem 120 votos no votável 22.
    """
    pleito = PleitoSintetico()
    pres_z12 = dict(votos={22: 120, 13: 80, 15: 30, 95: 8, 96: 12}, aptos=267)
    gov_z12 = dict(votos={15: 130, 43: 60, 95: 10, 96: 9}, aptos=259)
    pleito.adicionar_secao(
        zona=1,
        secao=12,
        local_id=1015,
        local_nome="ESCOLA CLASSE CONCEIÇÃO DE MACEDO",
        local_endereco="SQS 103 BLOCO A - ASA SUL",
        cargos={
            CARGO_PRESIDENTE: pleito_do_cargo(CARGO_PRESIDENTE, **pres_z12),
            CARGO_GOVERNADOR: pleito_do_cargo(CARGO_GOVERNADOR, **gov_z12),
        },
    )
    pleito.adicionar_secao(
        zona=1,
        secao=2,
        local_id=1015,
        local_nome="ESCOLA CLASSE CONCEIÇÃO DE MACEDO",
        local_endereco="SQS 103 BLOCO A - ASA SUL",
        cargos={
            CARGO_PRESIDENTE: pleito_do_cargo(CARGO_PRESIDENTE, **pres_z12),
            CARGO_GOVERNADOR: pleito_do_cargo(CARGO_GOVERNADOR, **gov_z12),
        },
    )
    pleito.adicionar_secao(
        zona=1,
        secao=30,
        local_id=1016,
        local_nome="ESCOLA PARQUE DO CENTRO EDUCACIONAL",
        local_endereco="EQS 312/313 - ASA SUL",
        cargos={
            CARGO_PRESIDENTE: pleito_do_cargo(
                CARGO_PRESIDENTE, aptos=280, votos={22: 110, 13: 80, 95: 20, 96: 30}
            ),
            CARGO_GOVERNADOR: pleito_do_cargo(
                CARGO_GOVERNADOR, aptos=300, votos={15: 150, 43: 100, 95: 25, 96: 15}
            ),
        },
    )
    pleito.adicionar_secao(
        zona=2,
        secao=40,
        local_id=2015,
        local_nome="COLÉGIO CANDIDO MENDES",
        local_endereco="EQN 208/209 - ASA NORTE",
        cargos={
            CARGO_PRESIDENTE: pleito_do_cargo(
                CARGO_PRESIDENTE, aptos=260, votos={22: 120, 13: 70, 95: 20, 96: 30}
            ),
            CARGO_GOVERNADOR: pleito_do_cargo(
                CARGO_GOVERNADOR, aptos=255, votos={15: 120, 43: 80, 95: 15, 96: 15}
            ),
        },
    )
    return pleito


def pleito_grade(
    *,
    zonas: Iterable[int] = (1, 2),
    locais_por_zona: int = 2,
    secoes_por_local: int = 8,
) -> PleitoSintetico:
    """Grade determinística de locais e seções — para os módulos 2 e 3.

    Gera `locais_por_zona` locais por zona com `secoes_por_local` seções cada
    (mín. 6 por local = referência local do módulo 2). Votos por seção seguem
    fórmulas determinísticas (mesmo pleito → mesmos CSVs), com variação leve
    entre seções para exercitar medianas/MAD. Cargos: Presidente e Governador.
    """
    pleito = PleitoSintetico()
    indice = 0
    for zona in zonas:
        for indice_local in range(locais_por_zona):
            local_id = zona * 1000 + 10 + indice_local
            local_nome = f"ESCOLA SINTÉTICA {zona}-{indice_local} ANÁLISE"
            for indice_secao in range(1, secoes_por_local + 1):
                numero = indice_local * 100 + indice_secao
                indice += 1
                pres22 = 90 + (indice * 13) % 25
                pres = pleito_do_cargo(
                    CARGO_PRESIDENTE,
                    aptos=300,
                    votos={22: pres22, 13: 180 - pres22, 15: 30, 95: 20, 96: 10},
                )
                gov15 = 100 + (indice * 7) % 20
                gov = pleito_do_cargo(
                    CARGO_GOVERNADOR,
                    aptos=310,
                    votos={15: gov15, 43: 200 - gov15, 95: 15, 96: 15},
                )
                pleito.adicionar_secao(
                    zona=zona,
                    secao=numero,
                    local_id=local_id,
                    local_nome=local_nome,
                    local_endereco=f"SQS {zona}{indice_local}{indice_secao:02d}",
                    cargos={CARGO_PRESIDENTE: pres, CARGO_GOVERNADOR: gov},
                )
    return pleito


# ---------------------------------------------------------------------------
# Escrita dos CSVs (Latin-1, ';', tudo entre aspas)
# ---------------------------------------------------------------------------


def _csv_em_bytes(cabecalho: tuple[str, ...], linhas: Iterable[Iterable[str]]) -> bytes:
    buffer = io.StringIO()
    escritor = csv.writer(
        buffer, delimiter=";", quotechar='"', quoting=csv.QUOTE_ALL, lineterminator="\r\n"
    )
    escritor.writerow(cabecalho)
    for linha in linhas:
        escritor.writerow(linha)
    return buffer.getvalue().encode("latin-1")


def _gravar_csv(
    caminho_csv: Path, cabecalho: tuple[str, ...], linhas: Iterable[Iterable[str]]
) -> None:
    caminho_csv.parent.mkdir(parents=True, exist_ok=True)
    caminho_csv.write_bytes(_csv_em_bytes(cabecalho, linhas))


def _nome_votavel_bu(votavel: int) -> str:
    if votavel == VOTAVEL_BRANCO:
        return "Branco"
    if votavel == VOTAVEL_NULO:
        return "Nulo"
    return NOMES_BU.get(votavel, f"CANDIDATO {votavel}")


def _nome_votavel_tot(votavel: int) -> str:
    if votavel == VOTAVEL_BRANCO:
        return "VOTO BRANCO"
    if votavel == VOTAVEL_NULO:
        return "VOTO NULO"
    return NOMES_TOT.get(votavel, f"CANDIDATO {votavel}")


def _partido(votavel: int) -> tuple[str, str, str]:
    """(NR_PARTIDO, SG_PARTIDO, NM_PARTIDO); branco/nulo/sem partido = #NULO#."""
    return PARTIDOS.get(votavel, ("-1", "#NULO#", "#NULO#"))


def _linha_bu(
    pleito: PleitoSintetico, secao: Secao, urna: int, p: PleitoCargo, votavel: int
) -> list[str]:
    tipo, tipo_nome = TIPO_VOTAVEL.get(votavel, (1, "Nominal"))
    eleicao, abrangencia_nome = ELEICOES_POR_CARGO[p.cargo_id]
    numero_partido, sigla, nome_partido = _partido(votavel)
    return [
        _DT_GERACAO,
        _HH_GERACAO,
        str(pleito.ano),
        "0",
        "Eleição Ordinária",
        _PLEITO,
        _DT_PLEITO,
        str(pleito.turno),
        str(eleicao),
        f"Eleição Geral {abrangencia_nome} {pleito.ano}",
        pleito.uf,
        pleito.municipio_id,
        pleito.municipio,
        str(secao.zona),
        str(secao.numero),
        str(secao.local_id),
        str(p.cargo_id),
        p.cargo,
        numero_partido,
        sigla,
        nome_partido,
        _DT_BU_RECEBIDO,
        str(p.aptos),
        str(p.comparecimento),
        str(p.abstencoes),
        "1",
        "APURADA",
        str(tipo),
        tipo_nome,
        str(votavel),
        _nome_votavel_bu(votavel),
        str(p.votos[votavel]),
        str(urna),
        _CARGA_1,
        _CARGA_2,
        _FLASHCARD,
        _DT_CARGA_URNA,
        _CARGO_PERGUNTA_SECAO,
        "#NULO#",  # nenhuma urna agregada na base (exceção fica para quem injetar)
        _DT_ABERTURA,
        _DT_ENCERRAMENTO,
        str(max(p.comparecimento - 10, 0)),  # biometria não consumida pelo pipeline
        _DT_EMISSAO_BU,
        _JUNTA,
        _JUNTA,
    ]


def _linhas_bu(pleito: PleitoSintetico) -> list[list[str]]:
    linhas: list[list[str]] = []
    for secao in pleito.secoes:
        urna = pleito.urna(secao.zona, secao.numero)
        for cargo_id in sorted(secao.cargos):
            p = secao.cargos[cargo_id]
            for votavel in sorted(p.votos):
                linhas.append(_linha_bu(pleito, secao, urna, p, votavel))
    return linhas


def csv_boletim_de_urna(pleito: PleitoSintetico) -> bytes:
    """CSV do BU (Latin-1): 1 linha por urna × cargo × votável."""
    return _csv_em_bytes(COLUNAS_BU, _linhas_bu(pleito))


def escrever_boletim_de_urna(caminho_csv: Path, pleito: PleitoSintetico) -> None:
    _gravar_csv(caminho_csv, COLUNAS_BU, _linhas_bu(pleito))


def _linha_tot(
    pleito: PleitoSintetico,
    secao: Secao,
    p: PleitoCargo,
    votavel: int,
    votos: int,
    *,
    turno: int,
    nacional: bool,
) -> list[str]:
    eleicao, abrangencia_nome = ELEICOES_POR_CARGO[p.cargo_id]
    if nacional:
        sg_ue, nm_ue, tp_abrangencia = "BR", "BRASIL", "F"
    else:
        sg_ue, nm_ue, tp_abrangencia = pleito.uf, "DISTRITO FEDERAL", "E"
    if votavel in (VOTAVEL_BRANCO, VOTAVEL_NULO):
        candidato_sq = "#NULO#"
    else:
        candidato_sq = str(100_000 + p.cargo_id * 1_000 + votavel)
    return [
        _DT_GERACAO,
        _HH_GERACAO,
        str(pleito.ano),
        "2",
        "ELEIÇÃO ORDINÁRIA",
        str(turno),
        str(eleicao),
        f"ELEIÇÃO GERAL {abrangencia_nome.upper()} {pleito.ano}",
        _DT_ELEICAO,
        tp_abrangencia,
        pleito.uf,
        sg_ue,
        nm_ue,
        pleito.municipio_id,
        pleito.municipio,
        str(secao.zona),
        str(secao.numero),
        str(p.cargo_id),
        p.cargo.upper(),
        str(votavel),
        _nome_votavel_tot(votavel),
        str(votos),
        str(secao.local_id),
        candidato_sq,
        secao.local_nome,
        secao.local_endereco,
    ]


def _linhas_totalizacao(
    pleito: PleitoSintetico,
    cargos: Iterable[int],
    *,
    nacional: bool,
    incluir_turno_2: bool,
) -> list[list[str]]:
    linhas: list[list[str]] = []
    alvo = tuple(sorted(cargos))
    for secao in pleito.secoes:
        for cargo_id in alvo:
            p = secao.cargos.get(cargo_id)
            if p is None:
                continue
            for votavel, votos in sorted(p.votos_para_totalizacao().items()):
                linhas.append(
                    _linha_tot(
                        pleito, secao, p, votavel, votos, turno=pleito.turno, nacional=nacional
                    )
                )
                if incluir_turno_2:
                    # pegadinha real: o arquivo BR mistura turnos — 2º turno com
                    # votos diferentes; a ingestão filtra NR_TURNO
                    linhas.append(
                        _linha_tot(
                            pleito,
                            secao,
                            p,
                            votavel,
                            votos + 1,
                            turno=2,
                            nacional=nacional,
                        )
                    )
    return linhas


def csv_totalizacao(
    pleito: PleitoSintetico,
    *,
    cargos: Iterable[int],
    nacional: bool,
    incluir_turno_2: bool = False,
) -> bytes:
    """CSV de totalização (Latin-1): 1 linha por seção × cargo × votável."""
    linhas = _linhas_totalizacao(pleito, cargos, nacional=nacional, incluir_turno_2=incluir_turno_2)
    return _csv_em_bytes(COLUNAS_TOT, linhas)


def escrever_totalizacao(
    caminho_csv: Path,
    pleito: PleitoSintetico,
    *,
    cargos: Iterable[int],
    nacional: bool,
    incluir_turno_2: bool = False,
) -> None:
    _gravar_csv(
        caminho_csv,
        COLUNAS_TOT,
        _linhas_totalizacao(pleito, cargos, nacional=nacional, incluir_turno_2=incluir_turno_2),
    )


# ---------------------------------------------------------------------------
# Empacotamento no formato do download (zips + manifest.json)
# ---------------------------------------------------------------------------


def _zipar(caminho_zip: Path, nome_membro: str, conteudo: bytes) -> None:
    caminho_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as arquivo:
        arquivo.writestr(nome_membro, conteudo)


def empacotar(data_dir: Path, pleito: PleitoSintetico) -> dict[str, Path]:
    """Empacota o pleito como os zips do download + manifest.json em data/raw.

    Resolve exatamente o contrato consumido por pipeline/ingest.ingerir
    (manifesto por PAPEL, nunca por nome de arquivo presumido). Devolve o
    caminho de cada zip pelo papel.
    """
    bruto = Path(data_dir) / "raw"
    bruto.mkdir(parents=True, exist_ok=True)
    cargos_estaduais = sorted(
        {
            cargo_id
            for secao in pleito.secoes
            for cargo_id in secao.cargos
            if cargo_id != CARGO_PRESIDENTE
        }
    )
    conteudos = {
        "boletim_de_urna": ("bweb_1t_sintetico.csv", csv_boletim_de_urna(pleito)),
        "totalizacao_uf": (
            "votacao_secao_uf_sintetica.csv",
            csv_totalizacao(pleito, cargos=cargos_estaduais, nacional=False),
        ),
        "totalizacao_presidente": (
            "votacao_secao_br_sintetica.csv",
            csv_totalizacao(
                pleito,
                cargos=[CARGO_PRESIDENTE],
                nacional=True,
                incluir_turno_2=True,
            ),
        ),
    }
    caminhos: dict[str, Path] = {}
    arquivos: dict[str, dict[str, object]] = {}
    for papel, (nome_membro, conteudo) in conteudos.items():
        nome_zip = f"{papel}_sintetico.zip"
        caminho_zip = bruto / nome_zip
        _zipar(caminho_zip, nome_membro, conteudo)
        caminhos[papel] = caminho_zip
        arquivos[nome_zip] = {
            "papel": papel,
            "conjunto": "fixtures-sinteticas",
            "tamanho_bytes": caminho_zip.stat().st_size,
            "sha256": hashlib.sha256(caminho_zip.read_bytes()).hexdigest(),
        }
    manifesto = {
        "gerado_em": datetime.now(UTC).isoformat(),
        "arquivos": arquivos,
    }
    (bruto / "manifest.json").write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return caminhos
