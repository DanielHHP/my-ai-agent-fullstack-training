"""Domain errors that the CLI can present without a traceback."""


class RagError(Exception):
    """Base class for expected user-facing failures."""


class ConfigurationError(RagError):
    """Invalid or missing configuration."""


class IndexError(RagError):
    """Missing, incompatible, or corrupt persisted index."""
