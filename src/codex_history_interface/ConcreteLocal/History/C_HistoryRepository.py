import os
from pathlib import Path
from typing import Iterable

from ...Abstract.History import D_ChatExport, D_ChatMessage, I_HistoryRepository
from ..._messages import MessageMerger, visible_message
from ..._timestamps import creation_timestamp, last_message_timestamp, modification_timestamp
from ...errors import HistoryReadWarning, HistoryUnavailableError
from ..Export.C_AssetBundle import attachment_text
from .C_HistoryStore import C_HistoryStore


class C_HistoryRepository(I_HistoryRepository):
    def __init__(
        self,
        codex_home: str | os.PathLike | None = None,
        *,
        databases: Iterable[str | os.PathLike] = (),
    ) -> None:
        self._store = C_HistoryStore(codex_home, tuple(databases))

    @property
    def codex_home(self) -> Path:
        return self._store.home

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return tuple(self._store.notices)

    def reportWarning(self, text: str, *, category: type[Warning] = HistoryReadWarning) -> None:
        self._store.warn(text, category=category)

    def getChatList(self) -> list[str]:
        self._store.refresh()
        return self._store.identities()

    def getChatTitle(self, chat_id: str) -> str:
        if self._store.loaded:
            self._store.refresh_metadata()
        conversation = self._store.lookup(chat_id)
        return self._displayTitle(self._store.title(conversation))

    @staticmethod
    def _displayTitle(title: str) -> str:
        return title[:40] + "..." if len(title) > 40 else title

    def getChatMessage(self, chat_id: str) -> list[D_ChatMessage]:
        return self._chatMessages(chat_id, with_attachments=False)

    def _chatMessages(self, chat_id: str, *, with_attachments: bool) -> list[D_ChatMessage]:
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
                    messages.append(attachment_text(record, message.public()) if with_attachments else message.public())
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
                messages.append(attachment_text(record, message.public()) if with_attachments else message.public())
        return messages

    def getLastMessageTimeStamp(self, chat_id: str) -> float | None:
        self._store.refresh()
        conversation = self._store.lookup(chat_id)
        return last_message_timestamp(self._store, conversation)

    def getCreateTimeStamp(self, chat_id: str) -> float | None:
        self._store.refresh()
        conversation = self._store.lookup(chat_id)
        return creation_timestamp(self._store, conversation)

    def getChatExport(self, chat_id: str) -> D_ChatExport:
        messages = self._chatMessages(chat_id, with_attachments=True)
        if not messages:
            raise HistoryUnavailableError(f"No visible user messages or final replies exist for {chat_id}")
        protected = set(self._store.extra_databases)
        for stored in self._store.conversations.values():
            protected.update(stored.paths)
        conversation = self._store.lookup(chat_id)
        return D_ChatExport(
            title=self._displayTitle(self._store.title(conversation)),
            messages=messages,
            created_at=creation_timestamp(self._store, conversation),
            modified_at=modification_timestamp(self._store, conversation),
            bases=tuple(Path(value) for value in (conversation.metadata.get("cwd"), *conversation.working_directories) if isinstance(value, str) and Path(value).is_absolute()),
            protected=(self._store.home, *protected),
        )
