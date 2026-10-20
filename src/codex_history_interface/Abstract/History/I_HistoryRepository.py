from abc import ABC, abstractmethod
from pathlib import Path

from ...errors import HistoryReadWarning
from .D_ChatExport import D_ChatExport
from .D_ChatMessage import D_ChatMessage


class I_HistoryRepository(ABC):
    """Read-only history source, independent of document rendering."""

    @property
    @abstractmethod
    def codex_home(self) -> Path:
        raise NotImplementedError

    @property
    @abstractmethod
    def diagnostics(self) -> tuple[str, ...]:
        raise NotImplementedError

    @abstractmethod
    def getChatList(self) -> list[str]:
        """Return full IDs for active and archived conversations."""
        raise NotImplementedError

    @abstractmethod
    def getChatTitle(self, chat_id: str) -> str:
        """Return the display title with the existing fallback and length rules."""
        raise NotImplementedError

    @abstractmethod
    def getChatMessage(self, chat_id: str) -> list[D_ChatMessage]:
        """Return only visible user messages and final assistant replies."""
        raise NotImplementedError

    @abstractmethod
    def getLastMessageTimeStamp(self, chat_id: str) -> float | None:
        raise NotImplementedError

    @abstractmethod
    def getCreateTimeStamp(self, chat_id: str) -> float | None:
        raise NotImplementedError

    @abstractmethod
    def getChatExport(self, chat_id: str) -> D_ChatExport:
        """Return visible export content, attachment bases and protected paths."""
        raise NotImplementedError

    @abstractmethod
    def reportWarning(self, text: str, *, category: type[Warning] = HistoryReadWarning) -> None:
        """Record a deduplicated read or rendering warning for the caller."""
        raise NotImplementedError
