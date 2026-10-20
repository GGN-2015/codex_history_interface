from dataclasses import dataclass
from pathlib import Path

from .D_ChatMessage import D_ChatMessage


@dataclass(frozen=True)
class D_ChatExport:
    """Visible snapshot with per-message Unix times and local-file boundaries.

    message_timestamps aligns with messages; an empty tuple means unavailable.
    """

    title: str
    messages: list[D_ChatMessage]
    created_at: float | None
    modified_at: float | None
    bases: tuple[Path, ...] = ()
    protected: tuple[Path, ...] = ()
    message_timestamps: tuple[float | None, ...] = ()
