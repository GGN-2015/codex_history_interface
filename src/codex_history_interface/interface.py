from abc import ABC, abstractmethod
from typing import Literal, TypedDict


TITLE_MISSING = "<TitleMissing>"


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


class I_CodexInterface(ABC):
    @abstractmethod
    def getChatList(self) -> list[str]:
        """Return exact conversation IDs, including archived conversations."""
        raise NotImplementedError

    @abstractmethod
    def getChatTitle(self, chat_id: str) -> str:
        """Return the display name, or '<TitleMissing>' plus any available legacy title."""
        raise NotImplementedError

    @abstractmethod
    def getChatMessage(self, chat_id: str) -> list[ChatMessage]:
        """Return user messages and final assistant replies in stored order."""
        raise NotImplementedError
