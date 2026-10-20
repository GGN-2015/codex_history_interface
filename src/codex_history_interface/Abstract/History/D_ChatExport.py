from dataclasses import dataclass
from pathlib import Path

from .D_ChatMessage import D_ChatMessage


@dataclass(frozen=True)
class D_ChatExport:
    """Visible conversation snapshot and local-file boundaries for an exporter."""

    title: str
    messages: list[D_ChatMessage]
    created_at: float | None
    modified_at: float | None
    bases: tuple[Path, ...] = ()
    protected: tuple[Path, ...] = ()
