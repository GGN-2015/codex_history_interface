from abc import ABC, abstractmethod
from os import PathLike
from pathlib import Path

from .History.D_ChatMessage import D_ChatMessage


TITLE_MISSING = "<TitleMissing>"


class I_CodexInterface(ABC):
    @abstractmethod
    def getChatList(self) -> list[str]:
        """Return exact conversation IDs, including archived conversations."""
        raise NotImplementedError

    @abstractmethod
    def getChatTitle(self, chat_id: str) -> str:
        """Return the display name or marked legacy title, capped at 40 characters plus '...'."""
        raise NotImplementedError

    @abstractmethod
    def getChatMessage(self, chat_id: str) -> list[D_ChatMessage]:
        """Return user messages and final assistant replies in stored order."""
        raise NotImplementedError

    @abstractmethod
    def getLastMessageTimeStamp(self, chat_id: str) -> float | None:
        """Return the latest visible message's Unix timestamp in seconds, or None."""
        raise NotImplementedError

    @abstractmethod
    def getCreateTimeStamp(self, chat_id: str) -> float | None:
        """Return the conversation's creation Unix timestamp in seconds, or None."""
        raise NotImplementedError

    @abstractmethod
    def exportToTex(
        self,
        chat_id: str,
        output_path: str | PathLike,
        *,
        overwrite: bool = False,
    ) -> Path:
        """Generate UTF-8 TeX and sibling assets from Markdown, preserving formula bodies."""
        raise NotImplementedError
