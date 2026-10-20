class CodexHistoryError(Exception):
    """Base exception for local history access failures."""


class ChatNotFoundError(CodexHistoryError):
    """The exact conversation ID was not found locally."""


class HistoryUnavailableError(CodexHistoryError):
    """History stores or the requested conversation body are unavailable."""


class HistoryReadWarning(UserWarning):
    """The retained history is partial, unreadable, or in an unknown format."""


class TexExportError(CodexHistoryError):
    """A conversation could not be safely rendered or published as LaTeX."""


class TexExportWarning(UserWarning):
    """An export preserves source content using a less specialized presentation."""
