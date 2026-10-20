import os
from pathlib import Path
from typing import Iterable

from ._assets import attachment_text
from ._export_tex import export_destination, export_document
from ._messages import MessageMerger, visible_message
from ._storage import HistoryStore
from ._timestamps import creation_timestamp, last_message_timestamp, modification_timestamp
from .errors import HistoryUnavailableError, TexExportWarning
from .interface import D_ChatMessage, I_CodexInterface


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
        title = self._store.title(conversation)
        return title[:40] + "..." if len(title) > 40 else title

    def getChatMessage(self, chat_id: str) -> list[D_ChatMessage]:
        return self._chat_messages(chat_id, with_attachments=False)

    def _chat_messages(self, chat_id: str, *, with_attachments: bool) -> list[D_ChatMessage]:
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

    def exportToTex(
        self,
        chat_id: str,
        output_path: str | os.PathLike,
        *,
        overwrite: bool = False,
    ) -> Path:
        destination = export_destination(output_path, self._store.home, overwrite)
        messages = self._chat_messages(chat_id, with_attachments=True)
        if not messages:
            raise HistoryUnavailableError(f"No visible user messages or final replies exist for {chat_id}")
        protected = set(self._store.extra_databases)
        for stored in self._store.conversations.values():
            protected.update(stored.paths)
        if destination in protected:
            raise ValueError("The TeX destination cannot replace a Codex database or history journal")
        conversation = self._store.lookup(chat_id)
        title = self._store.title(conversation)
        return export_document(
            destination,
            title[:40] + "..." if len(title) > 40 else title,
            messages,
            creation_timestamp(self._store, conversation),
            modification_timestamp(self._store, conversation),
            overwrite=overwrite,
            bases=tuple(Path(value) for value in (conversation.metadata.get("cwd"), *conversation.working_directories) if isinstance(value, str) and Path(value).is_absolute()),
            protected=(self._store.home, *protected),
            warn=self._store.warn,
            render_warn=lambda text: self._store.warn(text, category=TexExportWarning),
        )
