from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import blake2b
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
from typing import Iterator
import warnings

from ...errors import ChatNotFoundError, HistoryReadWarning, HistoryUnavailableError
from ...Abstract.I_CodexInterface import TITLE_MISSING


UUID_PATTERN = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
THREAD_COLUMNS = ("id", "name", "title", "archived", "rollout_path", "updated_at_ms", "updated_at", "created_at_ms", "created_at", "cwd")
ITEM_TABLES = ("thread_items", "thread_realtime_items")
HISTORY_TABLES = (*ITEM_TABLES, "thread_turns", "thread_history_projection_state")
ACTIVITY_TIME_FIELDS = (
    "timestamp_ms", "timestamp", "created_at_ms", "created_at", "updated_at_ms", "updated_at",
    "started_at_ms", "started_at", "completed_at_ms", "completed_at", "archived_at_ms", "archived_at",
)


@dataclass
class Conversation:
    identity: str
    metadata: dict = field(default_factory=dict)
    index: dict = field(default_factory=dict)
    paths: dict[Path, int] = field(default_factory=dict)
    projections: list[tuple[Path, str]] = field(default_factory=list)
    working_directories: list[str] = field(default_factory=list)


def timestamp(value: object) -> float | None:
    try:
        numeric = float(value)
        result = numeric / 1000 if numeric > 100_000_000_000 else numeric
    except (TypeError, ValueError, OverflowError):
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    return result if math.isfinite(result) else None


def database_version(path: Path) -> tuple[int, str]:
    numbers = re.findall(r"\d+", path.stem)
    return int(numbers[-1]) if numbers else -1, str(path)


def locate_home(explicit: str | os.PathLike | None) -> Path:
    if explicit is not None:
        home = Path(explicit).expanduser()
    elif os.environ.get("CODEX_HOME", "").strip():
        home = Path(os.environ["CODEX_HOME"]).expanduser()
    else:
        candidates = [Path.home() / ".codex"]
        candidates.extend(Path(os.environ[name]) / ".codex" for name in ("USERPROFILE", "HOME") if os.environ.get(name))
        home = next((path for path in candidates if path.is_dir()), candidates[0])
    home = home.resolve()
    if not home.is_dir():
        raise HistoryUnavailableError(f"Codex data directory not found: {home}. Pass codex_home or set CODEX_HOME.")
    return home


@contextmanager
def read_database(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


def columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


class C_HistoryStore:
    def __init__(self, home: str | os.PathLike | None, databases: tuple = ()) -> None:
        self.home = locate_home(home)
        self.extra_databases = tuple(Path(path).expanduser().resolve() for path in databases)
        for path in self.extra_databases:
            if not path.is_file():
                raise HistoryUnavailableError(f"Explicit database not found: {path}")
        self.conversations: dict[str, Conversation] = {}
        self._journal_paths: set[Path] = set()
        self._journal_signatures: dict[Path, tuple[int, int, int, int]] = {}
        self.notices: list[str] = []
        self.loaded = False
        self.recognized = False

    def warn(self, text: str, *, category: type[Warning] = HistoryReadWarning) -> None:
        if text not in self.notices:
            self.notices.append(text)
            warnings.warn(text, category, stacklevel=3)

    def conversation(self, identity: str) -> Conversation:
        if identity not in self.conversations:
            self.conversations[identity] = Conversation(identity)
        return self.conversations[identity]

    def records(self, path: Path, limit: int | None = None) -> Iterator[tuple[int, dict]]:
        try:
            with path.open("rb") as stream:
                remaining = os.fstat(stream.fileno()).st_size if limit is None else limit
                ordinal = 0
                while remaining > 0:
                    raw = stream.readline(remaining)
                    if not raw:
                        self.warn(f"Journal became shorter during reading: {path}")
                        break
                    remaining -= len(raw)
                    ordinal += 1
                    if not raw.strip():
                        continue
                    try:
                        record = json.loads(raw)
                    except (ValueError, UnicodeError):
                        self.warn(f"Invalid or partial JSON omitted at {path}:{ordinal}")
                        continue
                    if isinstance(record, dict):
                        yield ordinal, record
                    else:
                        self.warn(f"Non-object JSON omitted at {path}:{ordinal}")
        except OSError as error:
            self.warn(f"Cannot read journal {path}: {error}")

    def database_paths(self) -> list[Path]:
        roots = dict.fromkeys((self.home, self.home / "sqlite", *(path.parent for path in self.extra_databases)))
        candidates = list(self.extra_databases)
        for root in roots:
            if root.is_dir():
                for pattern in ("state*.sqlite", "state*.db", "thread_history*.sqlite", "thread_history*.db"):
                    candidates.extend(root.glob(pattern))
        if not candidates:
            for root in roots:
                if root.is_dir():
                    candidates.extend(root.glob("*.sqlite"))
                    candidates.extend(root.glob("*.db"))
        return sorted(dict.fromkeys(path.resolve() for path in candidates), key=database_version, reverse=True)

    def source_signature(self) -> dict[Path, tuple[int, int, int, int] | None]:
        paths = {self.home / "session_index.jsonl"}
        for database in self.database_paths():
            paths.update((database, Path(str(database) + "-wal"), Path(str(database) + "-journal")))
        for directory in ("sessions", "archived_sessions"):
            root = self.home / directory
            if root.is_dir():
                try:
                    paths.update(root.rglob("*.jsonl"))
                except OSError as error:
                    self.warn(f"Cannot enumerate journals under {root}: {error}")
        paths.update(self._journal_paths)
        return {path: self.file_signature(path) for path in paths}

    def file_signature(self, path: Path) -> tuple[int, int, int, int] | None:
        try:
            status = path.stat()
            return status.st_size, status.st_mtime_ns, status.st_ctime_ns, status.st_ino
        except FileNotFoundError:
            return None
        except OSError as error:
            self.warn(f"Cannot inspect history source {path}: {error}")
            return None

    def refresh_metadata(self) -> None:
        for conversation in self.conversations.values():
            conversation.metadata.clear()
            conversation.index.clear()
            conversation.projections.clear()
        for path in self.database_paths():
            self.load_database(path)
        self.load_index()

    def load_database(self, path: Path) -> None:
        try:
            with read_database(path) as connection:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not tables.intersection(("threads", *HISTORY_TABLES)):
                    return
                self.recognized = True
                if "threads" in tables:
                    available = columns(connection, "threads")
                    if "id" not in available:
                        self.warn(f"Unsupported threads table without id: {path}")
                    else:
                        selected = ", ".join(f'"{name}"' for name in THREAD_COLUMNS if name in available)
                        for row in connection.execute(f"SELECT {selected} FROM threads"):
                            values = dict(row)
                            identity = values.get("id")
                            if isinstance(identity, str) and identity:
                                conversation = self.conversation(identity)
                                if not conversation.metadata:
                                    conversation.metadata.update(values)
                for table in HISTORY_TABLES:
                    if table not in tables:
                        continue
                    available = columns(connection, table)
                    if "thread_id" not in available:
                        self.warn(f"Unsupported history table without thread_id: {path}#{table}")
                        continue
                    for row in connection.execute(f'SELECT DISTINCT thread_id FROM "{table}"'):
                        if not isinstance(row[0], str) or not row[0]:
                            continue
                        conversation = self.conversation(row[0])
                        if table in ITEM_TABLES and "item_json" in available:
                            conversation.projections.append((path, table))
        except (OSError, sqlite3.Error) as error:
            self.warn(f"Cannot read database {path}: {error}")

    def load_index(self) -> None:
        path = self.home / "session_index.jsonl"
        if not path.is_file():
            return
        self.recognized = True
        for _, entry in self.records(path):
            identity = entry.get("id") or entry.get("thread_id")
            if not isinstance(identity, str) or not identity:
                continue
            conversation = self.conversation(identity)
            previous = timestamp(conversation.index.get("updated_at"))
            current = timestamp(entry.get("updated_at"))
            if previous is not None and current is not None and current < previous:
                continue
            conversation.index = entry

    def add_path(self, conversation: Conversation, path: Path) -> None:
        try:
            path = path.resolve()
            if any(os.path.samefile(path, existing) for existing in conversation.paths if existing.exists()):
                return
            status = path.stat()
            conversation.paths[path] = status.st_size
            self._journal_paths.add(path)
            self._journal_signatures[path] = status.st_size, status.st_mtime_ns, status.st_ctime_ns, status.st_ino
        except OSError as error:
            self.warn(f"Cannot inspect journal {path}: {error}")

    def load_journal(self, path: Path) -> None:
        identity = None
        working_directory = None
        for ordinal, record in self.records(path):
            payload = record.get("payload")
            if record.get("type") == "session_meta" and isinstance(payload, dict):
                identity = payload.get("id") or payload.get("session_id")
                working_directory = payload.get("cwd")
                break
            if ordinal >= 8:
                break
        if not isinstance(identity, str) or not identity:
            match = UUID_PATTERN.search(path.name)
            if match is None:
                self.warn(f"Cannot identify a Codex conversation ID for journal {path}; no synthetic ID was created")
                return
            identity = match.group()
        conversation = self.conversation(identity)
        if isinstance(working_directory, str) and working_directory not in conversation.working_directories:
            conversation.working_directories.append(working_directory)
        self.add_path(conversation, path)
        self.recognized = True

    def refresh(self) -> None:
        self.conversations.clear()
        self._journal_paths.clear()
        self._journal_signatures.clear()
        self.recognized = False
        self.refresh_metadata()
        for directory in ("sessions", "archived_sessions"):
            root = self.home / directory
            if not root.is_dir():
                continue
            try:
                for path in root.rglob("*.jsonl"):
                    self.load_journal(path)
            except OSError as error:
                self.warn(f"Cannot enumerate journals under {root}: {error}")
        for conversation in self.conversations.values():
            stored = conversation.metadata.get("rollout_path")
            if not isinstance(stored, str) or not stored:
                continue
            path = Path(stored).expanduser()
            if not path.is_absolute() and not PureWindowsPath(stored).is_absolute():
                path = self.home / path
            if path.is_file():
                self.add_path(conversation, path)
            elif not conversation.paths:
                self.warn(f"Missing local journal for conversation {conversation.identity}")
        self.loaded = True
        if not self.recognized:
            raise HistoryUnavailableError(f"No recognized Codex history stores found under {self.home}")

    def lookup(self, identity: str) -> Conversation:
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("chat_id must be a non-empty exact conversation ID")
        if not self.loaded:
            self.refresh()
        if identity not in self.conversations:
            raise ChatNotFoundError(f"Conversation ID not found: {identity}. ID prefixes and titles are not accepted.")
        return self.conversations[identity]

    def identities(self) -> list[str]:
        def recency(conversation: Conversation) -> tuple[float, str]:
            for value in (conversation.metadata.get("updated_at_ms"), conversation.metadata.get("updated_at"), conversation.index.get("updated_at"), conversation.metadata.get("created_at_ms"), conversation.metadata.get("created_at")):
                parsed = timestamp(value)
                if parsed is not None:
                    return parsed, conversation.identity
            return 0, conversation.identity

        return [conversation.identity for conversation in sorted(self.conversations.values(), key=recency, reverse=True)]

    def title(self, conversation: Conversation) -> str:
        for value in (conversation.metadata.get("name"), conversation.index.get("thread_name")):
            if isinstance(value, str) and value.strip():
                return value
        for value in (conversation.index.get("title"), conversation.metadata.get("title")):
            if isinstance(value, str) and value.strip():
                return TITLE_MISSING + value
        return TITLE_MISSING

    def activity_records(self) -> Iterator[tuple[str, dict]]:
        index = self.home / "session_index.jsonl"
        if index.is_file():
            for _, entry in self.records(index):
                identity = entry.get("id") or entry.get("thread_id")
                if isinstance(identity, str) and identity:
                    yield identity, entry
        for path in self.database_paths():
            try:
                with read_database(path) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for table in ("threads", *HISTORY_TABLES):
                        if table not in tables:
                            continue
                        available = columns(connection, table)
                        identity_field = "id" if table == "threads" else "thread_id"
                        selected = [name for name in (*ACTIVITY_TIME_FIELDS, "item_json") if name in available]
                        if identity_field not in available or not selected:
                            continue
                        fields = ", ".join(f'"{name}"' for name in (identity_field, *selected))
                        for row in connection.execute(f'SELECT {fields} FROM "{table}"'):
                            record = dict(row)
                            identity = record.pop(identity_field)
                            if not isinstance(identity, str) or not identity:
                                continue
                            if "item_json" in record:
                                try:
                                    record["payload"] = json.loads(record.pop("item_json"))
                                except (ValueError, TypeError, UnicodeError):
                                    self.warn(f"Invalid activity item for {identity} in {path}#{table}")
                            yield identity, record
            except (OSError, sqlite3.Error) as error:
                self.warn(f"Cannot read activity timestamps from {path}: {error}")

    def projection_signature(self, identity: str, path: Path, table: str) -> bytes | None:
        """Fingerprint one chat's projected rows and final-answer selectors without parsing JSON."""
        try:
            with read_database(path) as connection:
                connection.execute("BEGIN")
                available = columns(connection, table)
                selected = [name for name in ("item_json", "item_id", "turn_id", "timestamp_ms", "timestamp", "created_at_ms", "created_at", "rollout_ordinal") if name in available]
                order = [name for name in ("rollout_ordinal", "created_at_ms", "turn_id", "item_id") if name in available]
                query = 'SELECT ' + ", ".join(f'"{name}"' for name in selected) + f' FROM "{table}" WHERE thread_id = ?'
                if order:
                    query += " ORDER BY " + ", ".join(f'"{name}"' for name in order)
                digest = blake2b(digest_size=20)
                digest.update(repr(tuple(selected)).encode("ascii") + b"\0")
                for row in connection.execute(query, (identity,)):
                    digest.update(repr(tuple(row)).encode("utf-8", "surrogatepass") + b"\0")
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "thread_turns" in tables and {"thread_id", "turn_id", "final_agent_item_id"}.issubset(columns(connection, "thread_turns")):
                    digest.update(b"thread_turns\0")
                    for row in connection.execute("SELECT turn_id, final_agent_item_id FROM thread_turns WHERE thread_id = ? ORDER BY turn_id, final_agent_item_id", (identity,)):
                        digest.update(repr(tuple(row)).encode("utf-8", "surrogatepass") + b"\0")
                return digest.digest()
        except (OSError, sqlite3.Error) as error:
            self.warn(f"Cannot fingerprint projected messages for {identity}: {error}")
            return None

    def projection_records(self, conversation: Conversation) -> Iterator[tuple[str, dict]]:
        for path, table in conversation.projections:
            try:
                with read_database(path) as connection:
                    available = columns(connection, table)
                    time_fields = ("timestamp_ms", "timestamp", "created_at_ms", "created_at")
                    selected = [name for name in ("item_json", "item_id", "turn_id", *time_fields) if name in available]
                    order = [name for name in ("rollout_ordinal", "created_at_ms", "turn_id", "item_id") if name in available]
                    query = f'SELECT ' + ", ".join(f'"{name}"' for name in selected) + f' FROM "{table}" WHERE thread_id = ?'
                    if order:
                        query += " ORDER BY " + ", ".join(f'"{name}"' for name in order)
                    final_items = {}
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if "thread_turns" in tables and {"thread_id", "turn_id", "final_agent_item_id"}.issubset(columns(connection, "thread_turns")):
                        final_items = {row[0]: row[1] for row in connection.execute("SELECT turn_id, final_agent_item_id FROM thread_turns WHERE thread_id = ?", (conversation.identity,)) if row[1]}
                    for row in connection.execute(query, (conversation.identity,)):
                        values = dict(row)
                        try:
                            item = json.loads(values["item_json"])
                        except (ValueError, TypeError, UnicodeError):
                            self.warn(f"Invalid projected message for {conversation.identity} in {path}#{table}")
                            continue
                        if not isinstance(item, dict):
                            continue
                        final_id = final_items.get(values.get("turn_id"))
                        if item.get("type") == "agentMessage" and final_id and (values.get("item_id") or item.get("id")) != final_id:
                            continue
                        record = {"type": "projection", "payload": item}
                        record.update({name: values[name] for name in time_fields if name in values})
                        yield f"{path}#{table}", record
            except (OSError, sqlite3.Error) as error:
                self.warn(f"Cannot read projected messages for {conversation.identity}: {error}")
