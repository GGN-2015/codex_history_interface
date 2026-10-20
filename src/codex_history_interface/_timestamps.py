from datetime import datetime, timezone
import math

from ._messages import visible_message
from ._storage import Conversation, HistoryStore


TIME_FIELDS = ("timestamp_ms", "timestamp", "created_at_ms", "created_at")
CREATE_FIELDS = ("created_at_ms", "created_at")


def timestamp_value(value: object, *, milliseconds: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            result = parsed.timestamp()
        except (ValueError, OverflowError, OSError):
            return None
    else:
        if milliseconds or abs(result) > 100_000_000_000:
            result /= 1000
    return result if math.isfinite(result) else None


def fields_timestamp(values: dict, fields: tuple[str, ...]) -> float | None:
    for field in fields:
        result = timestamp_value(values.get(field), milliseconds=field.endswith("_ms"))
        if result is not None:
            return result
    return None


def record_timestamp(record: dict) -> float | None:
    result = fields_timestamp(record, TIME_FIELDS)
    payload = record.get("payload")
    if result is None and isinstance(payload, dict):
        result = fields_timestamp(payload, TIME_FIELDS)
    return result


def creation_timestamp(store: HistoryStore, conversation: Conversation) -> float | None:
    result = fields_timestamp(conversation.metadata, CREATE_FIELDS)
    if result is not None:
        return result
    candidates = []
    for path, size in sorted(conversation.paths.items()):
        for _, record in store.records(path, size):
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            result = fields_timestamp(payload, TIME_FIELDS) if isinstance(payload, dict) else None
            if result is None:
                result = fields_timestamp(record, TIME_FIELDS)
            if result is not None:
                candidates.append(result)
                break
    if candidates:
        return min(candidates)
    return fields_timestamp(conversation.index, CREATE_FIELDS)


def last_message_timestamp(store: HistoryStore, conversation: Conversation) -> float | None:
    latest = None
    has_visible_journal = False
    for path, size in sorted(conversation.paths.items()):
        for _, record in store.records(path, size):
            if visible_message(record) is None:
                continue
            has_visible_journal = True
            result = record_timestamp(record)
            if result is not None:
                latest = result if latest is None else max(latest, result)
    if has_visible_journal:
        return latest
    if conversation.projections:
        store.warn(f"Using SQLite projected messages for {conversation.identity}; the original JSONL message stream is unavailable")
    for _, record in store.projection_records(conversation):
        if visible_message(record) is None:
            continue
        result = record_timestamp(record)
        if result is not None:
            latest = result if latest is None else max(latest, result)
    return latest


def modification_timestamp(store: HistoryStore, conversation: Conversation) -> float | None:
    for values in (conversation.metadata, conversation.index):
        result = fields_timestamp(values, ("updated_at_ms", "updated_at"))
        if result is not None:
            return result
    return last_message_timestamp(store, conversation)
