"""Erros controlados do pipeline: sempre com mensagem orientada em pt-BR."""


class PipelineError(Exception):
    """Falha controlada do pipeline; o CLI reporta e encerra com código != 0."""


class ConfigError(PipelineError):
    """Falha de carregamento ou validação do config.yaml."""


class DownloadError(PipelineError):
    """Falha de descoberta ou download das fontes do TSE."""


class IngestaoError(PipelineError):
    """Falha de ingestão: contrato de esquema violado ou fonte ilegível."""


class TotaisError(PipelineError):
    """Falha na conferência de totais: divergência entre BU e totalização."""


class IntegridadeError(PipelineError):
    """Falha na etapa de integridade: banco ausente ou ilegível."""


class DivergenciaError(PipelineError):
    """Falha na etapa de divergência: banco ausente ou ilegível."""


class CruzadoError(PipelineError):
    """Falha na etapa de voto cruzado: config sem pares ou banco ausente."""


class RankingError(PipelineError):
    """Falha na etapa de ranking: banco ausente ou achado malformado."""


class EmitirError(PipelineError):
    """Falha na etapa de emissão: banco/manifesto ausente ou saída ilegível."""


class GuardaError(PipelineError):
    """Falha de guarda de publicação: limite de tamanho, total ou git violado."""
