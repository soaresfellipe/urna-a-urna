"""Erros controlados do pipeline: sempre com mensagem orientada em pt-BR."""


class PipelineError(Exception):
    """Falha controlada do pipeline; o CLI reporta e encerra com código != 0."""


class ConfigError(PipelineError):
    """Falha de carregamento ou validação do config.yaml."""


class DownloadError(PipelineError):
    """Falha de descoberta ou download das fontes do TSE."""
