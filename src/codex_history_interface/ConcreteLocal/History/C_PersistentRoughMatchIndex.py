from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Callable, Iterable, Iterator
from uuid import uuid4

from ...Abstract.History.D_ChatId import D_ChatId
from ...Abstract.History.I_SearchConfig import I_SearchConfig
from ...errors import HistoryUnavailableError
from .C_IndexFileLock import C_IndexFileLock
from .C_RoughMatchIndex import C_RoughMatchIndex, _SearchDocument


class C_PersistentRoughMatchIndex(C_RoughMatchIndex):
    """SQLite-backed postings with transactional, cross-process source fingerprints."""

    def __init__(self, namespace: str, *, config: I_SearchConfig | None = None, warn: Callable[[str], None] | None = None) -> None:
        super().__init__(config)
        if not isinstance(namespace, str) or not namespace or any(not character.isascii() or not (character.isalnum() or character in "-_") for character in namespace):
            raise ValueError("namespace must contain only ASCII letters, digits, '-' or '_'")
        directory = Path(self._config.getIndexDirectory()).expanduser().resolve()
        self._path = directory / f"search-v{self._config.getSchemaVersion()}-{namespace}.sqlite3"
        self._file_lock = C_IndexFileLock(self.getLockPath(), self._config)
        self._process_id = os.getpid()
        self._warn = warn
        self._connection: sqlite3.Connection | None = None
        self._lock = RLock()
        self._generation: tuple | None = None
        self._state = None
        self._state_text = "null"
        self._dirty = False
        self._write_cache_configured = False

    def getIndexPath(self) -> Path:
        return self._path

    def getLockPath(self) -> Path:
        return self._path.with_name(self._path.name + ".lock")

    def checkProcess(self) -> None:
        if self._process_id != os.getpid():
            raise HistoryUnavailableError("The search index was inherited from another process; construct a new reader or index in this process")

    @staticmethod
    def _encodeState(value):
        if isinstance(value, Path):
            return {"path": str(value)}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, dict):
            return {"items": [[C_PersistentRoughMatchIndex._encodeState(key), C_PersistentRoughMatchIndex._encodeState(item)] for key, item in value.items()]}
        if isinstance(value, tuple):
            return {"tuple": [C_PersistentRoughMatchIndex._encodeState(item) for item in value]}
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(f"Unsupported index metadata type: {type(value).__name__}")

    @staticmethod
    def _decodeState(value):
        if not isinstance(value, dict):
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            raise ValueError("Invalid index metadata value")
        if set(value) == {"path"}:
            return Path(value["path"])
        if set(value) == {"bytes"}:
            return bytes.fromhex(value["bytes"])
        if set(value) == {"tuple"}:
            return tuple(C_PersistentRoughMatchIndex._decodeState(item) for item in value["tuple"])
        if set(value) == {"items"}:
            return {C_PersistentRoughMatchIndex._decodeState(key): C_PersistentRoughMatchIndex._decodeState(item) for key, item in value["items"]}
        raise ValueError("Invalid index metadata tag")

    def getState(self):
        self.checkProcess()
        with self._lock:
            return self._state

    def saveState(self, state) -> None:
        self.checkProcess()
        with self._lock:
            self._saveState(state)

    def _saveState(self, state) -> None:
        if self._connection is None:
            raise RuntimeError("saveState requires an active index session")
        encoded = json.dumps(self._encodeState(state), ensure_ascii=True, separators=(",", ":"))
        if encoded != self._state_text:
            self._connection.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (("state", encoded), ("state_hash", sha256(encoded.encode()).hexdigest())))
            self._state_text = encoded
            self._state = state
            self._dirty = True

    def _prepare(self, connection: sqlite3.Connection) -> bool:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE documents (chat_id TEXT PRIMARY KEY, title TEXT NOT NULL, title_length INTEGER NOT NULL CHECK(title_length >= 0), body_length INTEGER NOT NULL CHECK(body_length >= 0))")
            connection.execute("CREATE TABLE postings (term TEXT NOT NULL, chat_id TEXT NOT NULL, title_frequency INTEGER NOT NULL CHECK(title_frequency >= 0), body_frequency INTEGER NOT NULL CHECK(body_frequency >= 0), PRIMARY KEY(term, chat_id)) WITHOUT ROWID")
            connection.execute("CREATE INDEX postings_chat ON postings(chat_id)")
            connection.executemany("INSERT INTO metadata VALUES (?, ?)", (("version", str(self._config.getSchemaVersion())), ("identity", uuid4().hex), ("generation", "0"), ("state", "null"), ("state_hash", sha256(b"null").hexdigest())))
        elif not {"metadata", "documents", "postings"}.issubset(tables):
            raise ValueError("Incomplete search index schema")
        connection.execute("SELECT term, chat_id, title_frequency, body_frequency FROM postings LIMIT 0")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if int(metadata["version"]) != self._config.getSchemaVersion():
            raise ValueError("Incompatible search index schema")
        if "identity" not in metadata:
            metadata["identity"] = uuid4().hex
            connection.execute("INSERT INTO metadata VALUES ('identity', ?)", (metadata["identity"],))
        status = self._path.stat()
        generation = metadata["identity"], status.st_ino, int(metadata["generation"])
        if self._generation == generation:
            return False
        encoded = metadata["state"]
        if sha256(encoded.encode()).hexdigest() != metadata["state_hash"]:
            raise ValueError("Invalid search index metadata checksum")
        state = self._decodeState(json.loads(encoded))
        if state is not None and (not isinstance(state, dict) or set(state) != {"sources", "documents", "projections", "journals", "notices"}):
            raise ValueError("Invalid search index metadata fields")
        documents = {}
        for chat_id, title, title_length, body_length in connection.execute("SELECT chat_id, title, title_length, body_length FROM documents"):
            document = _SearchDocument(frozenset(), int(title_length), int(body_length), title)
            previous = self._documents.get(chat_id)
            documents[chat_id] = previous if previous == document else document
        self._documents = documents
        self._title_length = sum(document.title_length for document in documents.values())
        self._body_length = sum(document.body_length for document in documents.values())
        self._state = state
        self._state_text = encoded
        self._generation = generation
        return True

    def _begin(self) -> tuple[sqlite3.Connection, bool]:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self._path.exists() and any(Path(str(self._path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
            self._quarantine()
        for attempt in range(2):
            connection = None
            try:
                try:
                    descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
                connection = sqlite3.connect(self._path, timeout=self._config.getSqliteTimeout(), isolation_level=None)
                connection.execute("BEGIN IMMEDIATE")
                return connection, self._prepare(connection)
            except (sqlite3.DatabaseError, ValueError, KeyError, TypeError) as error:
                if connection is not None:
                    connection.close()
                corrupted = not isinstance(error, sqlite3.DatabaseError) or any(marker in str(error).lower() for marker in ("not a database", "malformed", "no such table", "no such column"))
                if not corrupted or attempt:
                    raise HistoryUnavailableError(f"Cannot open search index {self._path}: {error}") from error
                self._quarantine()
                self._generation = None
            except OSError:
                if connection is not None:
                    connection.close()
                raise
        raise HistoryUnavailableError(f"Cannot open search index {self._path}")

    def _quarantine(self) -> None:
        backup = self._path.with_name(self._path.name + ".invalid-" + uuid4().hex)
        for suffix in ("-journal", "-wal", "-shm", ""):
            source = Path(str(self._path) + suffix)
            if source.exists():
                if not source.is_file():
                    raise HistoryUnavailableError(f"Search index component is not a regular file: {source}")
                source.rename(Path(str(backup) + suffix))
        if self._warn is not None:
            self._warn(f"Search index was invalid; preserved at {backup} and rebuilding from read-only history")

    @contextmanager
    def session(self) -> Iterator[bool]:
        self.checkProcess()
        with self._lock:
            if self._connection is not None:
                yield False
                return
            with self._file_lock.acquire():
                connection, restored = self._begin()
                self._connection = connection
                self._dirty = False
                self._write_cache_configured = False
                try:
                    yield restored
                    if self._dirty:
                        connection.execute("UPDATE metadata SET value = CAST(value AS INTEGER) + 1 WHERE key = 'generation'")
                        self._generation = (*self._generation[:2], self._generation[2] + 1)
                    connection.commit()
                except BaseException as error:
                    connection.rollback()
                    self._generation = None
                    if isinstance(error, sqlite3.Error):
                        raise HistoryUnavailableError(f"Cannot update or query search index {self._path}: {error}") from error
                    raise
                finally:
                    self._connection = None
                    self._write_cache_configured = False
                    connection.close()

    def getChatList(self) -> list[str]:
        with self.session():
            return super().getChatList()

    def removeDocument(self, chat_id: str) -> bool:
        with self.session():
            return self._removeDocument(chat_id)

    def _removeDocument(self, chat_id: str) -> bool:
        document = self._documents.pop(chat_id, None)
        if document is None:
            return False
        self._connection.execute("DELETE FROM postings WHERE chat_id = ?", (chat_id,))
        self._connection.execute("DELETE FROM documents WHERE chat_id = ?", (chat_id,))
        self._title_length -= document.title_length
        self._body_length -= document.body_length
        self._dirty = True
        return True

    def updateDocument(self, chat_id: str, title: str, contents: Iterable[str] | str | None = None) -> None:
        with self.session():
            self._updateDocument(chat_id, title, contents)

    def _configureWriteCache(self) -> None:
        if self._write_cache_configured:
            return
        cache_kib = self._config.getSqliteWriteCacheKiB()
        if isinstance(cache_kib, bool) or not isinstance(cache_kib, int) or not 1 <= cache_kib <= 2147483647:
            raise ValueError("SQLite write cache size must be an integer between 1 and 2147483647 KiB")
        self._connection.execute(f"PRAGMA cache_size = -{cache_kib}")
        self._write_cache_configured = True

    def _updateDocument(self, chat_id: str, title: str, contents: Iterable[str] | str | None) -> None:
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise ValueError("chat_id must be a non-empty string")
        if not isinstance(title, str):
            raise TypeError("title must be a string")
        title_counts = Counter(self._features(title))
        body_counts: Counter[str] = Counter()
        if contents is None and chat_id in self._documents:
            body_counts.update(dict(self._connection.execute("SELECT term, body_frequency FROM postings WHERE chat_id = ? AND body_frequency > 0", (chat_id,))))
        elif contents is not None:
            for text in (contents,) if isinstance(contents, str) else contents:
                if not isinstance(text, str):
                    raise TypeError("contents must contain strings")
                body_counts.update(self._features(text))
        document = _SearchDocument(frozenset(), sum(title_counts.values()), sum(body_counts.values()), " ".join(self._normalize(title).split()))
        self._configureWriteCache()
        self.removeDocument(chat_id)
        self._connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?)", (chat_id, document.title, document.title_length, document.body_length))
        self._connection.executemany("INSERT INTO postings VALUES (?, ?, ?, ?)", ((term, chat_id, title_counts[term], body_counts[term]) for term in sorted(title_counts.keys() | body_counts.keys())))
        self._documents[chat_id] = document
        self._title_length += document.title_length
        self._body_length += document.body_length
        self._dirty = True

    def _getPostings(self, term: str) -> dict[str, tuple[int, int]]:
        return {chat_id: (title_frequency, body_frequency) for chat_id, title_frequency, body_frequency in self._connection.execute("SELECT chat_id, title_frequency, body_frequency FROM postings WHERE term = ?", (term,))}

    def roughMatch(self, content: str, maxCnt: int) -> list[D_ChatId]:
        if not self.validateQuery(content, maxCnt):
            return []
        with self.session():
            return super().roughMatch(content, maxCnt)
