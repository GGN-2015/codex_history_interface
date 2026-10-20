from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from codex_history_interface import C_CodexInterface, HistoryReadWarning, HistoryUnavailableError


CHAT_ID = "11111111-1111-4111-8111-111111111111"
ARCHIVED_ID = "22222222-2222-4222-8222-222222222222"
OTHER_ID = "33333333-3333-4333-8333-333333333333"
START = 1_700_000_000.0


class TimeRangeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "Codex 数据"
        self.home.mkdir()
        self.database = self.home / "state_9.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, created_at REAL, created_at_ms REAL, updated_at REAL, updated_at_ms REAL, archived INTEGER, archived_at REAL)")
        self.connection.commit()
        self.reader = C_CodexInterface(self.home)

    def add_chat(self, identity=CHAT_ID, *, created=None, updated=None, archived=False):
        self.connection.execute("INSERT INTO threads VALUES (?, ?, ?, NULL, ?, NULL, ?, NULL)", (identity, identity, created, updated, int(archived)))
        self.connection.commit()

    def update(self, assignments, parameters=(), identity=CHAT_ID):
        self.connection.execute("UPDATE threads SET " + assignments + " WHERE id = ?", (*parameters, identity))
        self.connection.commit()

    def journal(self, records, identity=CHAT_ID, *, archived=False, copy=False):
        folder = self.home / ("archived_sessions" if archived else "sessions") / "2023/11/14"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (("copy-" if copy else "rollout-") + identity + ".jsonl")
        entries = [{"type": "session_meta", "payload": {"id": identity}}, *records]
        path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
        return path

    def index(self, entries):
        (self.home / "session_index.jsonl").write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")

    def projection(self, *, identity=CHAT_ID, item=None, created_ms=None, updated_ms=None, table="thread_items"):
        with closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as connection, connection:
            connection.execute(f"CREATE TABLE IF NOT EXISTS {table} (thread_id TEXT, created_at_ms REAL, updated_at_ms REAL, item_json TEXT)")
            connection.execute(f"INSERT INTO {table} VALUES (?, ?, ?, ?)", (identity, created_ms, updated_ms, json.dumps({} if item is None else item)))

    def test_created_in_range_without_a_message_body(self):
        self.add_chat(created=START + 5, updated=START + 100)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])

    def test_latest_database_modification_in_range(self):
        self.add_chat(created=START - 100, updated=START + 5)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])

    def test_intermediate_modification_is_not_hidden_by_later_updates(self):
        self.add_chat(created=START - 100, updated=START + 100)
        self.journal([
            {"type": "event_msg", "timestamp": START + 5, "payload": {"type": "user_message", "message": "问题"}},
            {"type": "event_msg", "timestamp": START + 100, "payload": {"type": "agent_message", "message": "回答"}},
        ])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])

    def test_lifetime_overlap_without_an_event_in_range_does_not_match(self):
        self.add_chat(created=START - 100, updated=START + 100)
        self.journal([{"type": "event_msg", "timestamp": START - 50}, {"type": "event_msg", "timestamp": START + 50}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [])

    def test_both_interval_endpoints_are_included(self):
        self.add_chat(created=START)
        self.add_chat(OTHER_ID, updated=START + 10)
        self.assertEqual(set(self.reader.getChatListInTimeRange(START, START + 10)), {CHAT_ID, OTHER_ID})

    def test_equal_bounds_match_an_exact_fractional_timestamp(self):
        self.journal([{"type": "event_msg", "timestamp": START + 0.125}])
        self.assertEqual(self.reader.getChatListInTimeRange(START + 0.125, START + 0.125), [CHAT_ID])

    def test_fractional_seconds_are_not_rounded_into_the_interval(self):
        self.add_chat(created=START - 0.125, updated=START + 10.125)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [])

    def test_active_archived_and_unknown_status_conversations_are_included(self):
        self.add_chat(created=START)
        self.add_chat(ARCHIVED_ID, updated=START, archived=True)
        self.journal([{"type": "event_msg", "timestamp": START}], OTHER_ID, archived=True)
        self.assertEqual(set(self.reader.getChatListInTimeRange(START, START)), {CHAT_ID, ARCHIVED_ID, OTHER_ID})

    def test_archive_event_time_is_a_modification(self):
        self.add_chat(created=START - 100, updated=START + 100, archived=True)
        self.update("archived_at = ?", (START + 5,))
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])

    def test_duplicate_copies_and_events_return_each_id_only_once(self):
        records = [{"type": "event_msg", "timestamp": START}, {"type": "event_msg", "timestamp": START}]
        self.journal(records)
        self.journal(records, archived=True, copy=True)
        self.index([{"id": CHAT_ID, "updated_at": START}] * 2)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_result_order_matches_get_chat_list(self):
        for identity, updated in ((CHAT_ID, START + 100), (ARCHIVED_ID, START + 300), (OTHER_ID, START + 200)):
            self.add_chat(identity, created=START, updated=updated)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), self.reader.getChatList())

    def test_empty_recognized_history_returns_an_empty_list(self):
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [])

    def test_missing_timestamps_are_not_guessed(self):
        self.add_chat()
        self.journal([{"type": "event_msg", "payload": {"type": "user_message", "message": "没有时间"}}])
        self.assertEqual(self.reader.getChatListInTimeRange(0, START * 10), [])

    def test_filesystem_modification_time_is_not_used(self):
        self.add_chat()
        path = self.journal([])
        os.utime(path, (START, START))
        os.utime(self.database, (START, START))
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [])

    def test_timestamp_fields_inside_message_content_are_not_activity(self):
        self.journal([{"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": json.dumps({"timestamp": START}), "timestamp": START}]}}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [])

    def test_journal_payload_timestamp_is_supported(self):
        self.journal([{"type": "event_msg", "payload": {"timestamp": START, "type": "user_message", "message": "问题"}}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_session_metadata_creation_time_is_supported(self):
        self.journal([{"type": "session_meta", "payload": {"id": CHAT_ID, "timestamp": START}}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_journal_iso_offsets_and_milliseconds_use_the_same_timeline(self):
        for timestamp in ("2023-11-14T22:13:20Z", "2023-11-15T06:13:20+08:00", "2023-11-14T22:13:20", str(START), START * 1000):
            with self.subTest(timestamp=timestamp):
                self.journal([{"type": "event_msg", "timestamp": timestamp}])
                self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_database_millisecond_creation_and_modification_times_are_supported(self):
        self.add_chat()
        for field in ("created_at_ms", "updated_at_ms"):
            with self.subTest(field=field):
                self.update("created_at_ms = NULL, updated_at_ms = NULL")
                self.update(field + " = ?", ((START + 0.125) * 1000,))
                self.assertEqual(self.reader.getChatListInTimeRange(START + 0.125, START + 0.125), [CHAT_ID])

    def test_milliseconds_take_precedence_over_rounded_seconds(self):
        self.add_chat(created=START, updated=START)
        self.update("created_at_ms = ?, updated_at_ms = ?", ((START + 0.125) * 1000, (START + 0.125) * 1000))
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [])

    def test_invalid_milliseconds_fall_back_to_seconds(self):
        self.add_chat(updated=START)
        self.update("updated_at_ms = 'invalid'")
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_older_index_modification_survives_a_later_rename(self):
        self.index([
            {"id": CHAT_ID, "updated_at": START + 100, "thread_name": "最新标题"},
            {"id": CHAT_ID, "updated_at": START + 5, "thread_name": "旧标题"},
        ])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])
        self.assertEqual(self.reader.getChatTitle(CHAT_ID), "最新标题")

    def test_index_only_creation_and_thread_id_alias_are_supported(self):
        self.index([{"thread_id": CHAT_ID, "created_at_ms": START * 1000}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_index_modification_is_not_hidden_by_database_metadata(self):
        self.add_chat(created=START - 100, updated=START + 100)
        self.index([{"id": CHAT_ID, "updated_at_ms": (START + 5) * 1000}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])

    def test_projection_row_creation_and_update_times_are_supported(self):
        for table in ("thread_items", "thread_realtime_items"):
            self.projection(table=table, created_ms=START * 1000, updated_ms=(START + 1) * 1000)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])
        self.assertEqual(self.reader.getChatListInTimeRange(START + 1, START + 1), [CHAT_ID])

    def test_projection_payload_activity_time_is_supported(self):
        self.projection(item={"type": "userMessage", "timestamp": "2023-11-15T06:13:20+08:00"})
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_internal_activity_in_projection_is_checked_even_with_a_visible_journal(self):
        self.journal([{"type": "event_msg", "timestamp": START + 100, "payload": {"type": "user_message", "message": "问题"}}])
        self.projection(item={"type": "agentMessage", "phase": "analysis", "text": "THOUGHT_SECRET"}, created_ms=START * 1000)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])
        self.assertEqual(self.reader.getChatMessage(CHAT_ID), [{"role": "user", "content": "问题"}])

    def test_turn_start_and_completion_times_are_modifications(self):
        with closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE thread_turns (thread_id TEXT, started_at TEXT, completed_at TEXT)")
            connection.execute("INSERT INTO thread_turns VALUES (?, ?, ?)", (CHAT_ID, "2023-11-14T22:13:20Z", "2023-11-14T22:13:21Z"))
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])
        self.assertEqual(self.reader.getChatListInTimeRange(START + 1, START + 1), [CHAT_ID])

    def test_durations_and_rollout_ordinals_are_not_timestamps(self):
        with closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE thread_turns (thread_id TEXT, duration_ms REAL, rollout_ordinal INTEGER)")
            connection.execute("INSERT INTO thread_turns VALUES (?, ?, ?)", (CHAT_ID, START * 1000, START))
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [])

    def test_malformed_projection_json_does_not_hide_a_valid_row_timestamp(self):
        self.projection(created_ms=START * 1000)
        with closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as connection, connection:
            connection.execute("UPDATE thread_items SET item_json = '{invalid'")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_bad_journal_line_does_not_hide_later_activity(self):
        path = self.journal([])
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{invalid\n' + json.dumps({"type": "event_msg", "timestamp": START}) + '\n')
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_all_copies_are_checked_when_only_an_archived_copy_matches(self):
        self.journal([{"type": "event_msg", "timestamp": START + 100}])
        self.journal([{"type": "event_msg", "timestamp": START}], archived=True, copy=True)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_journal_scan_does_not_assume_chronological_record_order(self):
        self.journal([{"type": "event_msg", "timestamp": value} for value in (START + 100, START, START - 100)])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_internal_journal_activity_does_not_become_a_visible_message(self):
        self.journal([{"type": "response_item", "timestamp": START, "payload": {"type": "message", "role": "assistant", "phase": "analysis", "content": [{"type": "output_text", "text": "THOUGHT_SECRET"}]}}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [CHAT_ID])
        self.assertEqual(self.reader.getChatMessage(CHAT_ID), [])

    def test_invalid_activity_timestamps_are_ignored(self):
        self.add_chat(created="invalid", updated="NaN")
        self.journal([{"type": "event_msg", "timestamp": value} for value in (None, True, [], "invalid", "Infinity")])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START), [])

    def test_near_epoch_millisecond_fields_and_negative_unix_seconds(self):
        self.add_chat(created=-1)
        self.update("updated_at_ms = 1000")
        self.assertEqual(self.reader.getChatListInTimeRange(-1, -1), [CHAT_ID])
        self.assertEqual(self.reader.getChatListInTimeRange(1, 1), [CHAT_ID])

    def test_local_timezone_bounds_are_not_shifted_a_second_time(self):
        self.add_chat(created=START)
        local_start = datetime.fromtimestamp(START).astimezone().timestamp()
        offset_start = datetime(2023, 11, 15, 6, 13, 20, tzinfo=timezone(timedelta(hours=8))).timestamp()
        self.assertEqual(self.reader.getChatListInTimeRange(local_start, local_start), [CHAT_ID])
        self.assertEqual(self.reader.getChatListInTimeRange(offset_start, offset_start), [CHAT_ID])

    def test_input_bounds_are_seconds_and_are_not_implicitly_rescaled(self):
        self.add_chat(created=START)
        self.assertEqual(self.reader.getChatListInTimeRange(START * 1000, START * 1000), [])

    def test_query_refreshes_after_new_activity_is_persisted(self):
        self.add_chat(created=START - 100, updated=START + 100)
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [])
        self.journal([{"type": "event_msg", "timestamp": START + 5}])
        self.assertEqual(self.reader.getChatListInTimeRange(START, START + 10), [CHAT_ID])

    def test_query_refreshes_only_once_and_does_not_call_per_chat_getters(self):
        self.add_chat(created=START)
        self.add_chat(ARCHIVED_ID, created=START, archived=True)
        repository = self.reader.repository
        with patch.object(repository._store, "refresh", wraps=repository._store.refresh) as refresh:
            with patch.object(repository, "getCreateTimeStamp", side_effect=AssertionError("per-chat read")), patch.object(repository, "getLastMessageTimeStamp", side_effect=AssertionError("per-chat read")):
                self.assertEqual(set(self.reader.getChatListInTimeRange(START, START)), {CHAT_ID, ARCHIVED_ID})
        refresh.assert_called_once_with()

    def test_non_numeric_or_boolean_bounds_raise_type_error_before_reading(self):
        for value in (None, True, False, "1700000000", [], {}, datetime.now()):
            for bounds in ((value, START), (START, value)):
                with self.subTest(bounds=bounds), patch.object(self.reader._store, "refresh", side_effect=AssertionError("invalid query read history")):
                    with self.assertRaises(TypeError):
                        self.reader.getChatListInTimeRange(*bounds)

    def test_nonfinite_bounds_raise_value_error_before_reading(self):
        for value in (float("nan"), float("inf"), float("-inf"), 10 ** 400):
            for bounds in ((value, START), (START, value)):
                with self.subTest(bounds=bounds), patch.object(self.reader._store, "refresh", side_effect=AssertionError("invalid query read history")):
                    with self.assertRaises(ValueError):
                        self.reader.getChatListInTimeRange(*bounds)

    def test_reversed_bounds_raise_value_error_before_reading(self):
        with patch.object(self.reader._store, "refresh", side_effect=AssertionError("invalid query read history")):
            with self.assertRaisesRegex(ValueError, "timestamp1"):
                self.reader.getChatListInTimeRange(START + 1, START)

    def test_query_does_not_change_history_bytes_timestamps_or_file_set(self):
        self.add_chat(created=START - 100, updated=START + 100)
        self.journal([{"type": "event_msg", "timestamp": START}])
        self.index([{"id": CHAT_ID, "updated_at": START + 100}])
        self.projection(identity=ARCHIVED_ID, created_ms=START * 1000)
        before = {path.relative_to(self.home): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(set(self.reader.getChatListInTimeRange(START, START)), {CHAT_ID, ARCHIVED_ID})
        after = {path.relative_to(self.home): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_explicit_external_database_is_also_queried_read_only(self):
        database = Path(self.temporary.name) / "external" / "state_3.sqlite"
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE threads (id TEXT, created_at REAL)")
            connection.execute("INSERT INTO threads VALUES (?, ?)", (CHAT_ID, START))
        reader = C_CodexInterface(self.home, databases=[database])
        self.assertEqual(reader.getChatListInTimeRange(START, START), [CHAT_ID])

    def test_unrecognized_history_retains_the_existing_error_contract(self):
        empty_home = Path(self.temporary.name) / "empty"
        empty_home.mkdir()
        with self.assertRaises(HistoryUnavailableError):
            C_CodexInterface(empty_home).getChatListInTimeRange(START, START)


if __name__ == "__main__":
    unittest.main()
