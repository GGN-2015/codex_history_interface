from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from codex_history_interface import C_CodexInterface, HistoryReadWarning
from codex_history_interface._timestamps import timestamp_value


CHAT_ID = "11111111-1111-4111-8111-111111111111"
START = 1_700_000_000.0


class TimestampTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "Codex 数据"
        self.home.mkdir()
        self.database = self.home / "state_9.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, created_at REAL, created_at_ms REAL, updated_at REAL, archived INTEGER)")
        self.connection.execute("INSERT INTO threads VALUES (?, ?, NULL, ?, 0)", (CHAT_ID, START, START + 10_000))
        self.connection.commit()
        self.path = self.home / "sessions" / ("rollout-" + CHAT_ID + ".jsonl")
        self.records = [
            {"type": "session_meta", "timestamp": START - 1, "payload": {"id": CHAT_ID, "timestamp": START}},
            self.message("user", START + 10),
            self.message("assistant", START + 20.125),
        ]
        self.write()
        self.reader = C_CodexInterface(self.home)

    def message(self, role, timestamp, phase="final_answer"):
        return {"type": "response_item", "timestamp": timestamp, "payload": {"type": "message", "role": role, "phase": phase, "content": [{"type": "input_text" if role == "user" else "output_text", "text": role + " message"}]}}

    def write(self, path=None, records=None):
        target = self.path if path is None else path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(json.dumps(record) for record in (self.records if records is None else records)) + "\n", encoding="utf-8")

    def update(self, assignments, parameters=()):
        self.connection.execute("UPDATE threads SET " + assignments + " WHERE id = ?", (*parameters, CHAT_ID))
        self.connection.commit()

    def projection(self, item, created_at_ms=None):
        with closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE thread_items (thread_id TEXT, item_json TEXT, created_at_ms REAL)")
            connection.execute("INSERT INTO thread_items VALUES (?, ?, ?)", (CHAT_ID, json.dumps(item), created_at_ms))

    def test_creation_reads_database_seconds_as_float(self):
        actual = self.reader.getCreateTimeStamp(CHAT_ID)
        self.assertIsInstance(actual, float)
        self.assertEqual(actual, START)

    def test_creation_milliseconds_take_precedence_and_preserve_fraction(self):
        self.update("created_at_ms = ?", ((START + 1.125) * 1000,))
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START + 1.125)

    def test_explicit_millisecond_field_is_converted_even_near_epoch(self):
        self.update("created_at_ms = 1000")
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), 1.0)

    def test_invalid_millisecond_field_falls_back_to_seconds(self):
        self.update("created_at_ms = 'invalid'")
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START)

    def test_creation_database_has_priority_over_session_metadata(self):
        self.update("created_at = ?", (START + 5,))
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START + 5)

    def test_creation_falls_back_to_session_payload_timestamp(self):
        self.update("created_at = NULL")
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START)

    def test_creation_falls_back_to_outer_session_timestamp(self):
        self.update("created_at = NULL")
        del self.records[0]["payload"]["timestamp"]
        self.write()
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START - 1)

    def test_creation_uses_earliest_session_metadata_across_copies(self):
        self.update("created_at = NULL")
        other = self.home / "archived_sessions" / ("copy-" + CHAT_ID + ".jsonl")
        self.write(other, [{"type": "session_meta", "payload": {"id": CHAT_ID, "timestamp": START - 5}}])
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START - 5)

    def test_creation_can_use_index_creation_field_when_no_body_exists(self):
        self.update("created_at = NULL")
        self.path.unlink()
        (self.home / "session_index.jsonl").write_text(json.dumps({"id": CHAT_ID, "created_at_ms": START * 1000}), encoding="utf-8")
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START)

    def test_creation_missing_does_not_use_message_update_or_filesystem_time(self):
        self.update("created_at = NULL")
        self.records[0] = {"type": "session_meta", "payload": {"id": CHAT_ID}}
        self.write()
        self.assertIsNone(self.reader.getCreateTimeStamp(CHAT_ID))

    def test_creation_is_available_without_a_message_body(self):
        self.path.unlink()
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START)

    def test_last_message_uses_final_reply_not_database_update(self):
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.125)

    def test_last_message_can_be_a_user_message_without_a_reply(self):
        self.records.append(self.message("user", START + 30))
        self.write()
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 30)

    def test_last_message_ignores_internal_progress_tools_and_context(self):
        self.records.extend([
            self.message("assistant", START + 100, "commentary"),
            self.message("assistant", START + 110, "analysis"),
            self.message("system", START + 120),
            self.message("developer", START + 130),
            self.message("tool", START + 140),
            {"type": "event_msg", "timestamp": START + 150, "payload": {"type": "user_message", "message": "<environment_context>context</environment_context>"}},
        ])
        self.write()
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.125)

    def test_last_message_uses_maximum_timestamp_across_log_copies(self):
        other = self.home / "archived_sessions" / ("copy-" + CHAT_ID + ".jsonl")
        self.write(other, [self.records[0], self.message("assistant", START + 40), self.message("user", START + 5)])
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 40)

    def test_last_message_falls_back_to_payload_timestamp(self):
        self.records[-1]["timestamp"] = "invalid"
        self.records[-1]["payload"]["timestamp"] = START + 25
        self.write()
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 25)

    def test_last_message_accepts_milliseconds_without_losing_fraction(self):
        self.records[-1]["timestamp"] = (START + 20.125) * 1000
        self.write()
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.125)

    def test_last_message_does_not_invent_time_when_visible_messages_are_untimed(self):
        for record in self.records:
            record.pop("timestamp", None)
        self.write()
        self.assertIsNone(self.reader.getLastMessageTimeStamp(CHAT_ID))

    def test_last_message_is_none_when_only_internal_records_exist(self):
        self.write(records=[self.records[0], self.message("assistant", START + 100, "analysis")])
        self.assertIsNone(self.reader.getLastMessageTimeStamp(CHAT_ID))

    def test_last_message_is_none_when_only_metadata_exists(self):
        self.path.unlink()
        self.assertIsNone(self.reader.getLastMessageTimeStamp(CHAT_ID))

    def test_last_message_uses_projection_item_timestamp_as_fallback(self):
        self.path.unlink()
        self.projection({"type": "agentMessage", "text": "final", "phase": "final_answer", "timestamp": START + 50})
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 50)

    def test_last_message_uses_projection_row_creation_timestamp(self):
        self.path.unlink()
        self.projection({"type": "agentMessage", "text": "final", "phase": "final_answer"}, (START + 60.125) * 1000)
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 60.125)

    def test_last_message_excludes_nonfinal_projected_items(self):
        self.path.unlink()
        self.projection({"type": "agentMessage", "text": "thinking", "phase": "analysis"}, (START + 60) * 1000)
        with self.assertWarns(HistoryReadWarning):
            self.assertIsNone(self.reader.getLastMessageTimeStamp(CHAT_ID))

    def test_last_message_prefers_visible_jsonl_over_newer_projection(self):
        self.projection({"type": "agentMessage", "text": "projected", "phase": "final_answer", "timestamp": START + 500})
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.125)

    def test_untimed_visible_jsonl_does_not_use_projection_time(self):
        for record in self.records:
            record.pop("timestamp", None)
        self.write()
        self.projection({"type": "agentMessage", "text": "projected", "phase": "final_answer", "timestamp": START + 500})
        self.assertIsNone(self.reader.getLastMessageTimeStamp(CHAT_ID))

    def test_both_methods_support_archived_conversations(self):
        archived = self.home / "archived_sessions" / self.path.name
        self.write(archived)
        self.path.unlink()
        self.update("archived = 1")
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START)
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.125)

    def test_both_methods_refresh_after_new_data_is_persisted(self):
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START)
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.125)
        self.update("created_at = ?", (START + 1,))
        self.records.append(self.message("assistant", START + 30))
        self.write()
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START + 1)
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 30)

    def test_timestamp_parser_handles_seconds_milliseconds_and_iso_8601(self):
        for value, expected in (
            (START, START),
            (str(START), START),
            (START * 1000, START),
            ("2023-11-14T22:13:20Z", START),
            ("2023-11-15T06:13:20+08:00", START),
            ("2023-11-14T22:13:20", START),
            ("2023-11-14T22:13:20.125Z", START + 0.125),
            (0, 0.0),
        ):
            with self.subTest(value=value):
                self.assertEqual(timestamp_value(value), expected)

    def test_timestamp_parser_rejects_invalid_and_nonfinite_values(self):
        for value in (None, "", "invalid", True, False, [], {}, float("nan"), float("inf"), float("-inf"), "NaN", "Infinity"):
            with self.subTest(value=value):
                self.assertIsNone(timestamp_value(value))


if __name__ == "__main__":
    unittest.main()
