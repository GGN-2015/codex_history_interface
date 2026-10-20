from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch

from codex_history_interface import HistoryReadWarning
from codex_history_interface._export_tex import local_time_text, render_document
from codex_history_interface._tex_math import check_document
from test_export_tex import CHAT_ID, START, ExportFixture


def question_heading(timestamp):
    text = local_time_text(timestamp, include_timezone=False)
    return r"\subsection*{用户提问\quad{\normalfont\footnotesize\textcolor{gray}{" + text + "}}}"


class QuestionTimestampRenderingTests(unittest.TestCase):
    def test_each_heading_uses_its_question_time_not_an_assistant_time(self):
        messages = [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "第一答"},
            {"role": "assistant", "content": "补充回答"},
            {"role": "user", "content": "第二问"},
            {"role": "assistant", "content": "第二答"},
        ]
        source = render_document("时间", messages, START, START + 100, message_timestamps=(START + 10.987, START + 20, START + 25, START + 30.999999, START + 40))
        self.assertIn(question_heading(START + 10), source)
        self.assertIn(question_heading(START + 30), source)
        self.assertNotIn(question_heading(START + 20), source)
        self.assertNotIn(question_heading(START + 25), source)
        self.assertEqual(source.count(r"\textcolor{gray}{"), 2)
        self.assertIn(r"\subsection*{AI 回答}", source)
        check_document(source)

    def test_timestamps_change_only_the_question_heading(self):
        messages = [
            {"role": "user", "content": "问题 $x^2$"},
            {"role": "assistant", "content": "| 名称 | 值 |\n| --- | --- |\n| 数学 | $x_i$ |\n\n```python\nprint('中文')\n```\n\n> 引用 **强调**\n\n$$\\boxed{a_b=1}$$"},
        ]
        original = render_document("原有渲染", messages, START, START + 100)
        timed = render_document("原有渲染", messages, START, START + 100, message_timestamps=(START + 10, START + 20))
        self.assertEqual(timed.replace(question_heading(START + 10), question_heading(None)), original)
        check_document(timed)

    def test_missing_message_metadata_does_not_reuse_document_times(self):
        source = render_document("缺失时间", [{"role": "user", "content": "问题"}], START, START + 100)
        self.assertIn(question_heading(None), source)
        self.assertNotIn(question_heading(START), source)
        self.assertNotIn(question_heading(START + 100), source)

    def test_partial_message_metadata_leaves_later_question_unknown(self):
        messages = [{"role": "user", "content": "第一问"}, {"role": "user", "content": "第二问"}]
        source = render_document("部分时间", messages, None, None, message_timestamps=(START + 10,))
        self.assertIn(question_heading(START + 10), source)
        self.assertIn(question_heading(None), source)

    def test_orphan_assistant_does_not_supply_a_question_timestamp(self):
        messages = [{"role": "assistant", "content": "孤立回答"}, {"role": "user", "content": "问题"}]
        source = render_document("孤立回答", messages, None, None, message_timestamps=(START + 5, START + 10))
        self.assertIn(question_heading(None), source)
        self.assertNotIn(question_heading(START + 5), source)
        self.assertIn(question_heading(START + 10), source)
        self.assertIn("本轮未保留用户提问", source)

    def test_unrepresentable_time_is_labeled_without_stopping_export(self):
        notices = []
        source = render_document("异常时间", [{"role": "user", "content": "仍保留问题"}], None, None, message_timestamps=(1e300,), warn=notices.append)
        self.assertIn(r"\textcolor{gray}{未知（原始时间不可表示）}", source)
        self.assertIn("仍保留问题", source)
        self.assertEqual(len(notices), 1)
        self.assertIn("Round 1 user message", notices[0])
        check_document(source)

    def test_local_timezone_and_second_precision_include_day_rollover(self):
        instant = datetime(2026, 10, 20, 23, 59, 59, 999999, timezone.utc)
        for hours in (8, -7, 5.5):
            with self.subTest(offset_hours=hours):
                local = instant.astimezone(timezone(timedelta(hours=hours)))
                with patch("codex_history_interface.ConcreteLocal.Export.C_TexDocumentRenderer.datetime") as clock:
                    clock.fromtimestamp.return_value.astimezone.return_value = local
                    actual = local_time_text(instant.timestamp(), include_timezone=False)
                    clock.fromtimestamp.assert_called_once_with(instant.timestamp(), timezone.utc)
                    clock.fromtimestamp.return_value.astimezone.assert_called_once_with()
                self.assertEqual(actual, local.replace(tzinfo=None, microsecond=0).isoformat(sep=" "))
                self.assertRegex(actual, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class QuestionTimestampHistoryTests(ExportFixture, unittest.TestCase):
    def export(self):
        self.reader.exportToTex(CHAT_ID, self.output)
        return self.output.read_text(encoding="utf-8")

    def projection(self, item, created_at_ms=None):
        with closing(sqlite3.connect(self.home / "thread_history_1.sqlite")) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS thread_items (thread_id TEXT, item_json TEXT, created_at_ms REAL)")
            connection.execute("INSERT INTO thread_items VALUES (?, ?, ?)", (CHAT_ID, json.dumps(item), created_at_ms))

    def test_snapshot_collects_aligned_times_in_the_existing_message_read(self):
        repository = self.reader.repository
        with patch.object(repository, "_readChatMessages", wraps=repository._readChatMessages) as read:
            snapshot = repository.getChatExport(CHAT_ID)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(snapshot.message_timestamps, (START + 10, START + 20))
        self.assertEqual(snapshot.messages, self.reader.getChatMessage(CHAT_ID))
        self.assertTrue(all(set(message) == {"role", "content"} for message in snapshot.messages))

    def test_export_displays_the_user_time_and_preserves_subtitle_times(self):
        source = self.export()
        self.assertIn(question_heading(START + 10), source)
        for timestamp in (START, START + 20, START + 100, START + 500, START + 600):
            self.assertNotIn(question_heading(timestamp), source)
        self.assertIn("创建时间：" + local_time_text(START), source)
        self.assertIn("最后修改时间：" + local_time_text(START + 100), source)
        check_document(source)

    def test_iso8601_offset_is_converted_to_the_machine_timezone(self):
        self.records[2]["timestamp"] = "2026-10-20T08:03:04.987654-07:00"
        self.write()
        expected = datetime.fromisoformat(self.records[2]["timestamp"]).timestamp()
        self.assertIn(question_heading(expected), self.export())

    def test_timestamp_field_precedence_and_payload_fallback(self):
        for outer, milliseconds, payload, expected in (
            (START + 10, (START + 15.875) * 1000, START + 30, START + 15.875),
            ("invalid", "invalid", START + 30.75, START + 30.75),
            ((START + 10.125) * 1000, None, None, START + 10.125),
        ):
            with self.subTest(outer=outer, milliseconds=milliseconds):
                self.records[2]["timestamp"] = outer
                self.records[2]["timestamp_ms"] = milliseconds
                self.records[2]["payload"]["timestamp"] = payload
                self.write()
                snapshot = self.reader.repository.getChatExport(CHAT_ID)
                self.assertEqual(snapshot.message_timestamps[0], expected)
                self.assertIn(question_heading(expected), self.export())
                self.output.unlink()

    def test_mirrored_events_and_repeated_questions_keep_times_aligned(self):
        self.records = [
            self.records[0],
            {"type": "event_msg", "timestamp": START + 10, "payload": {"type": "user_message", "message": "相同提问"}},
            self.message("user", "相同提问", START + 10.125),
            self.message("assistant", "第一答", START + 20),
            {"type": "event_msg", "timestamp": START + 30, "payload": {"type": "user_message", "message": "相同提问"}},
            self.message("user", "相同提问", START + 30.125),
            self.message("assistant", "第二答", START + 40),
        ]
        self.write()
        snapshot = self.reader.repository.getChatExport(CHAT_ID)
        self.assertEqual(snapshot.message_timestamps, (START + 10, START + 20, START + 30, START + 40))
        source = self.export()
        self.assertEqual(source.count(r"\section{"), 2)
        self.assertIn(question_heading(START + 10), source)
        self.assertIn(question_heading(START + 30), source)

    def test_missing_user_time_is_not_filled_from_projection_or_other_events(self):
        self.records[2].pop("timestamp")
        self.write()
        self.projection({"type": "userMessage", "content": [{"type": "input_text", "text": "请解释表格和公式"}]}, (START + 15) * 1000)
        snapshot = self.reader.repository.getChatExport(CHAT_ID)
        self.assertEqual(snapshot.message_timestamps, (None, START + 20))
        self.assertIn(question_heading(None), self.export())

    def test_invalid_user_time_is_explicitly_unknown(self):
        self.records[2]["timestamp"] = "not-a-date"
        self.write()
        self.assertIn(question_heading(None), self.export())

    def test_projection_row_time_is_used_when_journal_is_unavailable(self):
        self.path.unlink()
        self.projection({"type": "userMessage", "content": [{"type": "input_text", "text": "投影提问"}], "timestamp": START + 5}, (START + 10.875) * 1000)
        self.projection({"type": "agentMessage", "text": "投影回答", "phase": "final_answer"}, (START + 20) * 1000)
        with self.assertWarns(HistoryReadWarning):
            snapshot = self.reader.repository.getChatExport(CHAT_ID)
            source = self.export()
        self.assertEqual(snapshot.message_timestamps, (START + 10.875, START + 20))
        self.assertIn(question_heading(START + 10.875), source)
        self.assertIn("投影提问", source)

    def test_projection_payload_time_is_used_when_row_time_is_missing(self):
        self.path.unlink()
        self.projection({"type": "userMessage", "content": [{"type": "input_text", "text": "提问"}], "timestamp": "2026-10-20T03:04:05.987Z"})
        with self.assertWarns(HistoryReadWarning):
            source = self.export()
        expected = datetime.fromisoformat("2026-10-20T03:04:05.987+00:00").timestamp()
        self.assertIn(question_heading(expected), source)

    def test_archived_history_preserves_question_times(self):
        archived = self.home / "archived_sessions" / self.path.name
        archived.parent.mkdir()
        self.path.rename(archived)
        self.connection.execute("UPDATE threads SET archived = 1")
        self.connection.commit()
        self.assertIn(question_heading(START + 10), self.export())

    def test_question_time_export_does_not_modify_history_or_public_messages(self):
        paths = (self.database, self.path)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        messages = self.reader.getChatMessage(CHAT_ID)
        self.export()
        self.assertEqual(self.reader.getChatMessage(CHAT_ID), messages)
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}, before)

    def test_standalone_exporter_accepts_message_timestamps(self):
        from codex_history_interface import C_TexExporter

        C_TexExporter().exportDocument(self.output, "独立文档", [{"role": "user", "content": "问题"}], START, START + 100, message_timestamps=(START + 10,))
        self.assertIn(question_heading(START + 10), self.output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
