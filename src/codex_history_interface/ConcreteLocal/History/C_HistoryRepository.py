from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock
from typing import Iterable

from ...Abstract.I_CodexInterface import TITLE_MISSING
from ...Abstract.History import D_ChatExport, D_ChatId, D_ChatMessage, I_HistoryRepository
from ...Abstract.History.I_SearchConfig import I_SearchConfig
from ..._messages import MessageMerger, visible_message
from ..._timestamps import activity_in_time_range, creation_timestamp, last_message_timestamp, modification_timestamp, record_timestamp, timestamp_range
from ...errors import HistoryReadWarning, HistoryUnavailableError
from ..Export.C_AssetBundle import attachment_text
from .C_HistoryStore import C_HistoryStore, Conversation
from .C_PersistentRoughMatchIndex import C_PersistentRoughMatchIndex
from .C_SearchConfig import C_SearchConfig


class C_HistoryRepository(I_HistoryRepository):
    def __init__(
        self,
        codex_home: str | os.PathLike | None = None,
        *,
        databases: Iterable[str | os.PathLike] = (),
        search_config: I_SearchConfig | None = None,
    ) -> None:
        self._store = C_HistoryStore(codex_home, tuple(databases))
        self._search_config = search_config if search_config is not None else C_SearchConfig()
        namespace = sha256(json.dumps([
            os.path.normcase(str(self._store.home)),
            sorted({os.path.normcase(str(path)) for path in self._store.extra_databases}),
            self._search_config.getTokenPattern().pattern, self._search_config.getTokenPattern().flags,
            self._search_config.getCjkPattern().pattern, self._search_config.getCjkPattern().flags,
        ]).encode()).hexdigest()
        self._search_index = C_PersistentRoughMatchIndex(namespace, config=self._search_config, warn=self._store.warn)
        index_path = self._search_index.getIndexPath().resolve()
        if index_path.is_relative_to(self._store.home.resolve()) or index_path in self._store.extra_databases:
            raise ValueError("The search index must be outside the read-only Codex data directory and history databases")
        self._search_sources: dict | None = None
        self._search_documents: dict[str, tuple[str, tuple, bool]] = {}
        self._search_projection_signatures: dict[tuple[str, Path, str], tuple[tuple, bytes | None]] = {}
        self._search_lock = RLock()

    def getSearchIndexPath(self) -> Path:
        return self._search_index.getIndexPath()

    def getSearchConfig(self) -> I_SearchConfig:
        return self._search_config

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

    def getChatListInTimeRange(self, timestamp1: float, timestamp2: float) -> list[str]:
        start, end = timestamp_range(timestamp1, timestamp2)
        self._store.refresh()
        identities = self._store.identities()
        matches = set()
        for identity, record in self._store.activity_records():
            if identity in self._store.conversations and identity not in matches and activity_in_time_range(record, start, end):
                matches.add(identity)
        for identity in identities:
            if identity in matches:
                continue
            conversation = self._store.conversations[identity]
            for path, size in sorted(conversation.paths.items()):
                if any(activity_in_time_range(record, start, end) for _, record in self._store.records(path, size)):
                    matches.add(identity)
                    break
        return [identity for identity in identities if identity in matches]

    def roughMatch(self, content: str, maxCnt: int) -> list[D_ChatId]:
        if not self._search_index.validateQuery(content, maxCnt):
            return []
        with self._search_lock, self._search_index.session() as restored:
            if restored:
                state = self._search_index.getState()
                self._search_sources = state["sources"] if state is not None else None
                self._search_documents = state["documents"] if state is not None else {}
                self._search_projection_signatures = state["projections"] if state is not None else {}
                if state is not None:
                    self._store._journal_paths.update(state["journals"])
                    for notice in state["notices"]:
                        self._store.warn(notice)
            sources = self._store.source_signature()
            if sources != self._search_sources:
                self._store.refresh()
                sources.update((path, signature) for path, signature in self._store._journal_signatures.items() if path not in sources)
                retry = False
                identities = set(self._store.conversations)
                for identity in set(self._search_index.getChatList()) - identities:
                    self._search_index.removeDocument(identity)
                    self._search_documents.pop(identity, None)
                for identity in sorted(identities):
                    conversation = self._store.conversations[identity]
                    title = self._store.title(conversation)
                    if not any(isinstance(value, str) and value.strip() for value in (conversation.metadata.get("name"), conversation.index.get("thread_name"))):
                        title = title.removeprefix(TITLE_MISSING)
                    previous = self._search_documents.get(identity)
                    using_projection = previous[2] if previous is not None else True
                    projection_sources = {path: self._databaseSignature(path) for path, table in conversation.projections} if using_projection else {}
                    body_signature = self._bodySignature(conversation, sources, using_projection) if previous is not None else None
                    if previous is None or body_signature != previous[1]:
                        messages, using_projection = self._readChatMessages(conversation, with_attachments=False) if conversation.paths or conversation.projections else ([], True)
                        self._search_index.updateDocument(identity, title, (message["content"] for message in messages))
                        body_signature = self._bodySignature(conversation, sources, using_projection)
                    elif title != previous[0]:
                        self._search_index.updateDocument(identity, title)
                    self._search_documents[identity] = title, body_signature, using_projection
                    if using_projection and any(
                        signature is None or self._databaseSignature(path) != projection_sources.get(path)
                        for path, table, signature in body_signature[1]
                    ):
                        self._search_documents.pop(identity, None)
                        retry = True
                self._search_projection_signatures = {
                    key: value for key, value in self._search_projection_signatures.items()
                    if key[0] in self._search_documents and self._search_documents[key[0]][2]
                    and (key[1], key[2]) in self._store.conversations[key[0]].projections
                }
                self._search_sources = None if retry else sources
            self._search_index.saveState({"sources": self._search_sources, "documents": self._search_documents, "projections": self._search_projection_signatures, "journals": tuple(sorted(self._store._journal_paths)), "notices": tuple(self._store.notices)})
            return self._search_index.roughMatch(content, maxCnt)

    def _bodySignature(self, conversation: Conversation, sources: dict, with_projection: bool) -> tuple:
        journals = tuple((path, sources.get(path)) for path in sorted(conversation.paths))
        projections = tuple((path, table, self._projectionSignature(conversation.identity, path, table, sources)) for path, table in conversation.projections) if with_projection else ()
        return journals, projections

    def _databaseSignature(self, path: Path, sources: dict | None = None) -> tuple:
        paths = path, Path(str(path) + "-wal"), Path(str(path) + "-journal")
        return tuple(sources.get(source) if sources is not None else self._store.file_signature(source) for source in paths)

    def _projectionSignature(self, identity: str, path: Path, table: str, sources: dict) -> bytes | None:
        key = identity, path, table
        source = self._databaseSignature(path, sources)
        previous = self._search_projection_signatures.get(key)
        if previous is None or previous[0] != source:
            self._search_projection_signatures[key] = source, self._store.projection_signature(identity, path, table)
        return self._search_projection_signatures[key][1]

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

    def _chatMessages(self, chat_id: str, *, with_attachments: bool, timestamps: list[float | None] | None = None) -> list[D_ChatMessage]:
        self._store.refresh()
        conversation = self._store.lookup(chat_id)
        return self._readChatMessages(conversation, with_attachments=with_attachments, timestamps=timestamps)[0]

    def _readChatMessages(self, conversation: Conversation, *, with_attachments: bool, timestamps: list[float | None] | None = None) -> tuple[list[D_ChatMessage], bool]:
        chat_id = conversation.identity
        if not conversation.paths and not conversation.projections:
            raise HistoryUnavailableError(f"No local conversation body exists for {chat_id}")
        merger = MessageMerger()
        messages = []
        for path, size in sorted(conversation.paths.items()):
            for _, record in self._store.records(path, size):
                message = visible_message(record)
                if message and merger.accept(message, str(path)):
                    messages.append(attachment_text(record, message.public()) if with_attachments else message.public())
                    if timestamps is not None:
                        timestamps.append(record_timestamp(record))
            try:
                if path.stat().st_size != size:
                    self._store.warn(f"Journal size changed while reading {chat_id}; only the initial snapshot boundary was read")
            except OSError as error:
                self._store.warn(f"Cannot inspect journal after reading {chat_id}: {error}")
        if messages:
            return messages, False
        if conversation.projections:
            self._store.warn(f"Using SQLite projected messages for {chat_id}; the original JSONL message stream is unavailable")
        for source, record in self._store.projection_records(conversation):
            message = visible_message(record)
            if message and merger.accept(message, source):
                messages.append(attachment_text(record, message.public()) if with_attachments else message.public())
                if timestamps is not None:
                    timestamps.append(record_timestamp(record))
        return messages, True

    def getLastMessageTimeStamp(self, chat_id: str) -> float | None:
        self._store.refresh()
        conversation = self._store.lookup(chat_id)
        return last_message_timestamp(self._store, conversation)

    def getCreateTimeStamp(self, chat_id: str) -> float | None:
        self._store.refresh()
        conversation = self._store.lookup(chat_id)
        return creation_timestamp(self._store, conversation)

    def getChatExport(self, chat_id: str) -> D_ChatExport:
        timestamps: list[float | None] = []
        messages = self._chatMessages(chat_id, with_attachments=True, timestamps=timestamps)
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
            message_timestamps=tuple(timestamps),
        )
