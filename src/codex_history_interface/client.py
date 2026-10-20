import os
from pathlib import Path
from typing import Iterable

from ._messages import MessageMerger, visible_message
from ._storage import HistoryStore
from .errors import HistoryUnavailableError
from .interface import ChatMessage, I_CodexInterface


class C_CodexInterface(I_CodexInterface):
    def __init__(
        self,
        codex_home: str | os.PathLike | None = None,
        *,
        databases: Iterable[str | os.PathLike] = (),
    ) -> None:
        self._store = HistoryStore(codex_home, tuple(databases))

    @property
    def codex_home(self) -> Path:
        return self._store.home

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._store.notices)

    def getChatList(self) -> list[str]:
        self._store.refresh()
        return self._store.identities()

    def getChatTitle(self, chat_id: str) -> str:
        if self._store.loaded:
            self._store.refresh_metadata()
        conversation = self._store.lookup(chat_id)
        return self._store.title(conversation)

    def getChatMessage(self, chat_id: str) -> list[ChatMessage]:
        self._store.refresh()
        conversation = self._store.lookup(chat_id)
        if not conversation.paths and not conversation.projections:
            raise HistoryUnavailableError(f"No local conversation body exists for {chat_id}")
        merger = MessageMerger()
        messages = []
        for path, size in sorted(conversation.paths.items()):
            for _, record in self._store.records(path, size):
                message = visible_message(record)
                if message and merger.accept(message, str(path)):
                    messages.append(message.public())
            try:
                if path.stat().st_size != size:
                    self._store.warn(f"Journal size changed while reading {chat_id}; only the initial snapshot boundary was read")
            except OSError as error:
                self._store.warn(f"Cannot inspect journal after reading {chat_id}: {error}")
        if messages:
            return messages
        if conversation.projections:
            self._store.warn(f"Using SQLite projected messages for {chat_id}; the original JSONL message stream is unavailable")
        for source, record in self._store.projection_records(conversation):
            message = visible_message(record)
            if message and merger.accept(message, source):
                messages.append(message.public())
        return messages
