from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_history_interface import (
    C_CodexInterface,
    ChatNotFoundError,
    HistoryReadWarning,
    HistoryUnavailableError,
    I_CodexInterface,
    TITLE_MISSING,
)
from codex_history_interface import C_CommandLine
from codex_history_interface._storage import read_database


ACTIVE = "11111111-1111-4111-8111-111111111111"
ARCHIVED = "22222222-2222-4222-8222-222222222222"
EXTRA = "33333333-3333-4333-8333-333333333333"


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "Codex 数据 & O'Reilly"
        self.home.mkdir()
        self.database = self.home / "state_9.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, archived INTEGER, rollout_path TEXT, updated_at INTEGER)")
        self.active = self.add_chat(ACTIVE, False, "Active display name", "Original prompt")
        self.archived = self.add_chat(ARCHIVED, True, "归档展示名", "归档原始问题")

    def response(self, role, text, **fields):
        return {"type": "response_item", "payload": {"type": "message", "role": role, "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}], **fields}}

    def events(self, identity, user="用户的问题", answer="最终答案 $x_i^2$\n第二行"):
        return [
            {"type": "session_meta", "payload": {"id": identity}},
            self.response("user", user),
            {"type": "event_msg", "payload": {"type": "user_message", "message": user}},
            self.response("assistant", "PROGRESS_SECRET", phase="commentary"),
            self.response("assistant", answer, phase="final_answer"),
            {"type": "event_msg", "payload": {"type": "agent_message", "message": answer, "phase": "final_answer"}},
            {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": answer}},
        ]

    def write_log(self, identity, archived=False, events=None):
        folder = self.home / ("archived_sessions/nested" if archived else "sessions/2026/10/20")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("rollout-" + identity + ".jsonl")
        path.write_bytes(b"".join(json.dumps(record, ensure_ascii=True).encode() + b"\r\n" for record in (self.events(identity) if events is None else events)))
        return path

    def add_chat(self, identity, archived=False, name=None, title="old", events=None):
        path = self.write_log(identity, archived, events)
        self.connection.execute("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", (identity, name, title, int(archived), str(path), 100 if not archived else 200))
        self.connection.commit()
        return path

    def index(self, entries):
        (self.home / "session_index.jsonl").write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")

    def reader(self):
        return C_CodexInterface(self.home)

    def projection(self, identity=EXTRA, items=None, final_id=None):
        path = self.home / "thread_history_1.sqlite"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE thread_items (thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, item_json TEXT)")
            selected = items if items is not None else [{"type": "agentMessage", "text": "PROJECTED_ANSWER", "phase": "final_answer"}]
            for ordinal, item in enumerate(selected):
                connection.execute("INSERT INTO thread_items VALUES (?, 'turn1', ?, ?, ?)", (identity, str(ordinal), ordinal, json.dumps(item)))
            if final_id is not None:
                connection.execute("CREATE TABLE thread_turns (thread_id TEXT, turn_id TEXT, final_agent_item_id TEXT)")
                connection.execute("INSERT INTO thread_turns VALUES (?, 'turn1', ?)", (identity, final_id))
        return path

    def test_abstract_interface_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            I_CodexInterface()
        self.assertEqual(I_CodexInterface.__abstractmethods__, {"getChatList", "getChatListInTimeRange", "roughMatch", "getChatTitle", "getChatMessage", "getLastMessageTimeStamp", "getCreateTimeStamp", "exportToTex"})

    def test_concrete_reader_implements_requested_interface(self):
        self.assertIsInstance(self.reader(), I_CodexInterface)

    def test_list_returns_only_exact_ids_and_includes_archived(self):
        self.assertEqual(self.reader().getChatList(), [ARCHIVED, ACTIVE])

    def test_title_uses_display_name_not_original_prompt_or_index(self):
        self.index([{"id": ACTIVE, "thread_name": "STALE_INDEX", "title": "Legacy index title"}])
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "Active display name")

    def test_archived_title_uses_same_rules(self):
        self.assertEqual(self.reader().getChatTitle(ARCHIVED), "归档展示名")

    def test_index_name_beats_legacy_database_title(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.index([{"id": ACTIVE, "thread_name": "Actual UI index name", "title": "Legacy index title"}])
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "Actual UI index name")

    def test_missing_display_name_prefixes_available_database_title(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.assertEqual(TITLE_MISSING, "<TitleMissing>")
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "<TitleMissing>Original prompt")

    def test_empty_or_whitespace_display_name_prefixes_available_title(self):
        for value in (None, "", " \n\t"):
            with self.subTest(value=value):
                self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", (value, ACTIVE))
                self.connection.commit()
                self.index([{"id": ACTIVE, "thread_name": "  "}])
                self.assertEqual(self.reader().getChatTitle(ACTIVE), "<TitleMissing>Original prompt")

    def test_missing_or_blank_legacy_titles_return_marker_alone(self):
        for value in (None, "", " \n\t"):
            with self.subTest(value=value):
                self.connection.execute("UPDATE threads SET name = NULL, title = ? WHERE id = ?", (value, ACTIVE))
                self.connection.commit()
                self.index([{"id": ACTIVE, "title": value}])
                self.assertEqual(self.reader().getChatTitle(ACTIVE), "<TitleMissing>")

    def test_non_string_legacy_titles_are_not_coerced(self):
        self.connection.execute("UPDATE threads SET name = NULL, title = ? WHERE id = ?", (b"not a title string", ACTIVE))
        self.connection.commit()
        for value in (42, False, [], {}):
            with self.subTest(value=value):
                self.index([{"id": ACTIVE, "title": value}])
                self.assertEqual(self.reader().getChatTitle(ACTIVE), "<TitleMissing>")

    def test_invalid_index_title_falls_back_to_database_title(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        for value in (None, "", " \n\t", 42, [], {}):
            with self.subTest(value=value):
                self.index([{"id": ACTIVE, "title": value}])
                self.assertEqual(self.reader().getChatTitle(ACTIVE), "<TitleMissing>Original prompt")

    def test_legacy_title_truncation_preserves_prefix_and_whitespace(self):
        title = "  旧标题 <原文>" * 100 + "\n末尾 "
        self.connection.execute("UPDATE threads SET name = NULL, title = ? WHERE id = ?", (title, ACTIVE))
        self.connection.commit()
        self.assertEqual(self.reader().getChatTitle(ACTIVE), ("<TitleMissing>" + title)[:40] + "...")
        self.index([{"id": ACTIVE, "title": title}])
        self.assertEqual(self.reader().getChatTitle(ACTIVE), ("<TitleMissing>" + title)[:40] + "...")

    def test_archived_missing_display_name_prefixes_available_title(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ARCHIVED,))
        self.connection.commit()
        self.assertEqual(self.reader().getChatTitle(ARCHIVED), "<TitleMissing>归档原始问题")

    def test_fallback_title_refreshes_and_later_display_name_has_no_marker(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        reader = self.reader()
        self.assertEqual(reader.getChatTitle(ACTIVE), "<TitleMissing>Original prompt")
        self.connection.execute("UPDATE threads SET title = 'Updated legacy title' WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.assertEqual(reader.getChatTitle(ACTIVE), "<TitleMissing>Updated legacy title")
        self.connection.execute("UPDATE threads SET name = 'Recovered display name' WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.assertEqual(reader.getChatTitle(ACTIVE), "Recovered display name")

    def test_display_name_truncation_preserves_leading_whitespace(self):
        name = "  很长的标题" * 100 + "\n末尾 "
        self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", (name, ACTIVE))
        self.connection.commit()
        self.assertEqual(self.reader().getChatTitle(ACTIVE), name[:40] + "...")

    def test_database_display_name_40_character_boundary(self):
        for length in (39, 40, 41):
            with self.subTest(length=length):
                title = "x" * length
                self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", (title, ACTIVE))
                self.connection.commit()
                expected = "x" * 40 + "..." if length > 40 else title
                self.assertEqual(self.reader().getChatTitle(ACTIVE), expected)

    def test_index_display_name_40_character_boundary(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        for length in (39, 40, 41):
            with self.subTest(length=length):
                title = "x" * length
                self.index([{"id": ACTIVE, "thread_name": title}])
                expected = "x" * 40 + "..." if length > 40 else title
                self.assertEqual(self.reader().getChatTitle(ACTIVE), expected)

    def test_unicode_title_counts_characters_not_bytes(self):
        for character in ("中", "😀"):
            for length in (40, 41):
                with self.subTest(character=character, length=length):
                    title = character * length
                    self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", (title, ACTIVE))
                    self.connection.commit()
                    expected = character * 40 + "..." if length > 40 else title
                    self.assertEqual(self.reader().getChatTitle(ACTIVE), expected)

    def test_legacy_title_boundary_counts_missing_prefix(self):
        for source in ("database", "index"):
            for length in (39, 40, 41):
                with self.subTest(source=source, length=length):
                    title = "x" * (length - len(TITLE_MISSING))
                    self.connection.execute("UPDATE threads SET name = NULL, title = ? WHERE id = ?", (title, ACTIVE))
                    self.connection.commit()
                    self.index([{"id": ACTIVE, "title": title}] if source == "index" else [])
                    expected = TITLE_MISSING + "x" * (min(length, 40) - len(TITLE_MISSING))
                    if length > 40:
                        expected += "..."
                    self.assertEqual(self.reader().getChatTitle(ACTIVE), expected)

    def test_archived_long_title_is_truncated(self):
        self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", ("归" * 41, ARCHIVED))
        self.connection.commit()
        self.assertEqual(self.reader().getChatTitle(ARCHIVED), "归" * 40 + "...")

    def test_long_messages_are_not_truncated(self):
        user = "长问题" * 100
        answer = "长答案" * 100
        self.write_log(ACTIVE, events=self.events(ACTIVE, user, answer))
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [
            {"role": "user", "content": user},
            {"role": "assistant", "content": answer},
        ])

    def test_duplicate_index_uses_newest_timestamp_not_last_physical_line(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.index([
            {"id": ACTIVE, "thread_name": "new", "updated_at": "2026-10-20T00:00:00Z"},
            {"id": ACTIVE, "thread_name": "old", "updated_at": "2026-10-19T00:00:00Z"},
        ])
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "new")

    def test_equal_or_unparseable_index_timestamp_uses_last_record(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        for value in (None, "invalid", "2026-10-20T00:00:00Z"):
            with self.subTest(value=value):
                self.index([{"id": ACTIVE, "thread_name": "first", "updated_at": value}, {"id": ACTIVE, "thread_name": "last", "updated_at": value}])
                self.assertEqual(self.reader().getChatTitle(ACTIVE), "last")

    def test_title_refreshes_after_database_rename(self):
        reader = self.reader()
        self.assertEqual(reader.getChatTitle(ACTIVE), "Active display name")
        self.connection.execute("UPDATE threads SET name = 'Renamed display' WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.assertEqual(reader.getChatTitle(ACTIVE), "Renamed display")

    def test_title_refreshes_after_index_rename(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.index([{"id": ACTIVE, "thread_name": "first"}])
        reader = self.reader()
        self.assertEqual(reader.getChatTitle(ACTIVE), "first")
        self.index([{"id": ACTIVE, "thread_name": "second"}])
        self.assertEqual(reader.getChatTitle(ACTIVE), "second")

    def test_exact_id_required_instead_of_prefix_or_title(self):
        reader = self.reader()
        for identity in (ACTIVE[:8], "Active display name", "unknown"):
            for method in (reader.getChatTitle, reader.getChatMessage, reader.getLastMessageTimeStamp, reader.getCreateTimeStamp):
                with self.subTest(identity=identity, method=method.__name__), self.assertRaises(ChatNotFoundError):
                    method(identity)

    def test_blank_or_non_string_id_is_rejected(self):
        for identity in ("", " ", None, 10):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.reader().getChatTitle(identity)

    def test_messages_return_only_role_and_content_dictionaries(self):
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [
            {"role": "user", "content": "用户的问题"},
            {"role": "assistant", "content": "最终答案 $x_i^2$\n第二行"},
        ])

    def test_archived_messages_are_read_without_an_extra_flag(self):
        self.assertEqual(len(self.reader().getChatMessage(ARCHIVED)), 2)

    def test_internal_roles_reasoning_and_tools_are_not_returned(self):
        events = self.events(ACTIVE) + [
            self.response("system", "SYSTEM_SECRET"),
            self.response("developer", "DEVELOPER_SECRET"),
            self.response("assistant", "ANALYSIS_SECRET", channel="analysis"),
            self.response("assistant", "REASONING_SECRET", phase="analysis"),
            self.response("assistant", "COMMAND_SECRET", recipient="functions.shell_command"),
            {"type": "response_item", "payload": {"type": "reasoning", "text": "REASON_SECRET"}},
            {"type": "response_item", "payload": {"type": "function_call", "arguments": "CALL_SECRET"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "TOOL_SECRET"}},
            {"type": "compacted", "payload": {"message": "COMPACTION_SECRET"}},
        ]
        self.write_log(ACTIVE, events=events)
        result = self.reader().getChatMessage(ACTIVE)
        self.assertEqual(len(result), 2)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_context_prefix_is_removed_but_actual_prompt_is_preserved(self):
        context = "# AGENTS.md instructions for demo\n<INSTRUCTIONS>SECRET</INSTRUCTIONS>\n<environment_context>SECRET</environment_context>\nActual request"
        self.write_log(ACTIVE, events=[self.response("user", context), self.response("assistant", "Answer", phase="final_answer")])
        self.assertEqual(self.reader().getChatMessage(ACTIVE)[0]["content"], "Actual request")

    def test_context_only_messages_are_omitted(self):
        events = [self.response("user", "<permissions instructions>SECRET</permissions instructions>")] + self.events(ACTIVE)
        self.write_log(ACTIVE, events=events)
        self.assertEqual(len(self.reader().getChatMessage(ACTIVE)), 2)

    def test_tags_inside_actual_user_text_are_not_deleted(self):
        user = 'Explain <environment_context> and JSON {"payload": "value"}.'
        self.write_log(ACTIVE, events=self.events(ACTIVE, user))
        self.assertEqual(self.reader().getChatMessage(ACTIVE)[0]["content"], user)

    def test_real_json_code_formulas_and_newlines_are_preserved(self):
        user = '\n{"type":"reasoning","payload":"real user data"}\n'
        answer = '```python\nprint("hello")\n```\n' + r"\[\frac{x_i^2}{\sqrt{a+b}}\]" + "\n"
        self.write_log(ACTIVE, events=self.events(ACTIVE, user, answer))
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [{"role": "user", "content": user}, {"role": "assistant", "content": answer}])

    def test_repeated_real_turns_are_not_removed_with_mirrors(self):
        events = self.events(ACTIVE)
        self.write_log(ACTIVE, events=events + events[1:])
        self.assertEqual(len(self.reader().getChatMessage(ACTIVE)), 4)

    def test_duplicate_journal_copy_is_not_returned_twice(self):
        duplicate = self.home / "archived_sessions" / ("copy-" + ACTIVE + ".jsonl")
        duplicate.write_bytes(self.active.read_bytes())
        self.assertEqual(len(self.reader().getChatMessage(ACTIVE)), 2)

    def test_event_only_legacy_messages_are_supported(self):
        self.write_log(ACTIVE, events=[
            {"type": "session_meta", "payload": {"id": ACTIVE}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "legacy user"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "legacy answer"}},
        ])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [{"role": "user", "content": "legacy user"}, {"role": "assistant", "content": "legacy answer"}])

    def test_task_complete_only_final_answer_is_supported(self):
        self.write_log(ACTIVE, events=[
            {"type": "session_meta", "payload": {"id": ACTIVE}},
            self.response("user", "question"),
            {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": "complete answer"}},
        ])
        self.assertEqual(self.reader().getChatMessage(ACTIVE)[-1], {"role": "assistant", "content": "complete answer"})

    def test_response_attachments_are_placeholders_without_binary(self):
        user = self.response("user", "question")
        user["payload"]["content"].extend([{"type": "input_image", "image_url": "BINARY_SECRET"}, {"type": "input_audio", "data": "AUDIO_SECRET"}, {"type": "input_file", "file_data": "FILE_SECRET"}])
        self.write_log(ACTIVE, events=[user])
        result = self.reader().getChatMessage(ACTIVE)
        self.assertEqual(result[0]["content"], "question\n[图片]\n[音频]\n[附件]")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_event_attachments_preserve_user_newlines(self):
        self.write_log(ACTIVE, events=[{"type": "event_msg", "payload": {"type": "user_message", "message": "\nquestion\n", "local_images": ["PRIVATE"], "local_audio": "PRIVATE"}}])
        self.assertEqual(self.reader().getChatMessage(ACTIVE)[0]["content"], "\nquestion\n\n[图片]\n[音频]")

    def test_refusal_text_is_visible_without_raw_payload(self):
        self.write_log(ACTIVE, events=[{"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "refusal reply"}]}}])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [{"role": "assistant", "content": "refusal reply"}])

    def test_old_assistant_without_phase_is_retained(self):
        self.write_log(ACTIVE, events=[self.response("assistant", "old final answer")])
        self.assertEqual(self.reader().getChatMessage(ACTIVE)[0]["content"], "old final answer")

    def test_index_only_conversation_is_listed_but_has_no_fabricated_body(self):
        self.index([{"id": EXTRA, "thread_name": "index only"}])
        reader = self.reader()
        self.assertIn(EXTRA, reader.getChatList())
        self.assertEqual(reader.getChatTitle(EXTRA), "index only")
        with self.assertRaises(HistoryUnavailableError):
            reader.getChatMessage(EXTRA)

    def test_orphan_journal_is_listed_and_uses_missing_title_marker(self):
        self.write_log(EXTRA, True)
        reader = self.reader()
        self.assertIn(EXTRA, reader.getChatList())
        self.assertEqual(reader.getChatTitle(EXTRA), "<TitleMissing>")
        self.assertEqual(len(reader.getChatMessage(EXTRA)), 2)

    def test_unidentified_journal_is_not_given_an_invented_conversation_id(self):
        path = self.home / "sessions" / "unidentified.jsonl"
        path.write_text(json.dumps(self.response("user", "unknown conversation")), encoding="utf-8")
        with self.assertWarns(HistoryReadWarning):
            identities = self.reader().getChatList()
        self.assertEqual(set(identities), {ACTIVE, ARCHIVED})

    def test_no_database_still_reads_index_and_archived_journals(self):
        self.connection.close()
        self.database.unlink()
        self.index([{"id": ARCHIVED, "thread_name": "index archived"}])
        reader = self.reader()
        self.assertEqual(set(reader.getChatList()), {ACTIVE, ARCHIVED})
        self.assertEqual(reader.getChatTitle(ARCHIVED), "index archived")
        self.assertEqual(len(reader.getChatMessage(ARCHIVED)), 2)

    def test_stale_foreign_rollout_path_is_recovered_from_archive_scan(self):
        self.connection.execute("UPDATE threads SET rollout_path = '/nonexistent/old/system/rollout.jsonl' WHERE id = ?", (ARCHIVED,))
        self.connection.commit()
        self.assertEqual(len(self.reader().getChatMessage(ARCHIVED)), 2)

    def test_projection_only_message_stream_is_available_with_warning(self):
        self.projection(items=[{"type": "userMessage", "content": [{"type": "text", "text": "projected user"}]}, {"type": "agentMessage", "text": "projected answer", "phase": "final_answer"}, {"type": "agentMessage", "text": "SECRET", "phase": "commentary"}])
        reader = self.reader()
        self.assertIn(EXTRA, reader.getChatList())
        self.assertEqual(reader.getChatTitle(EXTRA), "<TitleMissing>")
        with self.assertWarns(HistoryReadWarning):
            result = reader.getChatMessage(EXTRA)
        self.assertEqual(result, [{"role": "user", "content": "projected user"}, {"role": "assistant", "content": "projected answer"}])

    def test_projection_final_item_id_excludes_unmarked_nonfinal_items(self):
        self.projection(items=[{"type": "agentMessage", "text": "SECRET"}, {"type": "agentMessage", "text": "final answer"}], final_id="1")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader().getChatMessage(EXTRA), [{"role": "assistant", "content": "final answer"}])

    def test_stale_projection_does_not_duplicate_existing_log_answers(self):
        self.projection(ACTIVE)
        self.assertEqual(len(self.reader().getChatMessage(ACTIVE)), 2)

    def test_projection_fallback_works_for_context_only_journal(self):
        self.write_log(ACTIVE, events=[self.response("system", "SECRET")])
        self.projection(ACTIVE)
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader().getChatMessage(ACTIVE), [{"role": "assistant", "content": "PROJECTED_ANSWER"}])

    def test_malformed_lines_are_warned_and_not_returned_as_json(self):
        with self.active.open("ab") as stream:
            stream.write(b'\n{"unfinished": "SECRET\xff')
        reader = self.reader()
        with self.assertWarns(HistoryReadWarning):
            result = reader.getChatMessage(ACTIVE)
        self.assertEqual(len(result), 2)
        self.assertTrue(reader.diagnostics)
        self.assertNotIn("SECRET", json.dumps(result))

    def test_scalar_json_and_unknown_payload_are_not_messages(self):
        self.write_log(ACTIVE, events=[{"type": "response_item", "payload": "unexpected"}, {"type": "future_unknown", "payload": {"role": "assistant", "text": "SECRET"}}])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [])

    def test_read_only_database_connection_rejects_writes(self):
        with read_database(self.database) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("UPDATE threads SET name = 'changed'")

    def test_real_source_files_remain_byte_identical_after_all_operations(self):
        self.index([{"id": ACTIVE, "thread_name": "index title"}])
        paths = [self.database, self.active, self.archived, self.home / "session_index.jsonl"]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        reader = self.reader()
        for identity in reader.getChatList():
            reader.getChatTitle(identity)
            reader.getChatMessage(identity)
            reader.getLastMessageTimeStamp(identity)
            reader.getCreateTimeStamp(identity)
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def test_read_only_connection_observes_uncheckpointed_wal(self):
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA wal_autocheckpoint=0")
        self.connection.execute("UPDATE threads SET name = 'WAL name' WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.assertTrue(Path(str(self.database) + "-wal").exists())
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "WAL name")

    def test_highest_numeric_database_version_wins(self):
        newer = self.home / "state_10.sqlite"
        with closing(sqlite3.connect(newer)) as connection, connection:
            connection.execute("CREATE TABLE threads (id TEXT, name TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, 'new version name')", (ACTIVE,))
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "new version name")

    def test_database_in_sqlite_subdirectory_is_discovered(self):
        self.connection.close()
        folder = self.home / "sqlite"
        folder.mkdir()
        self.database.rename(folder / self.database.name)
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "Active display name")

    def test_explicit_external_nonstandard_database_is_supported(self):
        external = self.root / "external.sqlite"
        with closing(sqlite3.connect(external)) as connection, connection:
            connection.execute("CREATE TABLE threads (id TEXT, name TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, 'external title')", (EXTRA,))
        self.assertEqual(C_CodexInterface(self.home, databases=[external]).getChatTitle(EXTRA), "external title")

    def test_missing_explicit_database_is_not_created(self):
        missing = self.root / "missing.sqlite"
        with self.assertRaises(HistoryUnavailableError):
            C_CodexInterface(self.home, databases=[missing])
        self.assertFalse(missing.exists())

    def test_corrupt_database_warns_without_losing_journals(self):
        (self.home / "state_99.sqlite").write_bytes(b"not a database")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(set(self.reader().getChatList()), {ACTIVE, ARCHIVED})

    def test_explicit_home_beats_environment_and_default_home(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "does-not-exist")}):
            self.assertEqual(self.reader().codex_home, self.home.resolve())

    def test_codex_home_environment_is_used(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.home)}):
            self.assertEqual(C_CodexInterface().codex_home, self.home.resolve())

    def test_current_user_dot_codex_is_default(self):
        user = self.root / "user"
        (user / ".codex").mkdir(parents=True)
        with patch.dict(os.environ, {"CODEX_HOME": ""}), patch("pathlib.Path.home", return_value=user):
            self.assertEqual(C_CodexInterface().codex_home, (user / ".codex").resolve())

    def test_missing_home_fails_without_creating_directory(self):
        missing = self.root / "missing-home"
        with self.assertRaises(HistoryUnavailableError):
            C_CodexInterface(missing)
        self.assertFalse(missing.exists())

    def test_unrecognized_empty_home_is_not_reported_as_verified_empty_history(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(HistoryUnavailableError):
            C_CodexInterface(empty).getChatList()

    def test_valid_empty_index_returns_empty_list(self):
        empty = self.root / "empty-index"
        empty.mkdir()
        (empty / "session_index.jsonl").touch()
        self.assertEqual(C_CodexInterface(empty).getChatList(), [])

    def test_list_refreshes_when_new_conversation_is_added(self):
        reader = self.reader()
        self.assertEqual(len(reader.getChatList()), 2)
        self.add_chat(EXTRA, name="new conversation")
        self.assertEqual(len(reader.getChatList()), 3)

    def test_message_call_reads_newly_appended_final_reply(self):
        reader = self.reader()
        self.assertEqual(len(reader.getChatMessage(ACTIVE)), 2)
        with self.active.open("ab") as stream:
            stream.write(json.dumps(self.response("assistant", "new answer", phase="final_answer")).encode() + b"\n")
        self.assertEqual(reader.getChatMessage(ACTIVE)[-1]["content"], "new answer")

    def test_cli_list_title_and_messages_match_library(self):
        reader = self.reader()
        for command, arguments, expected in (("list", [], reader.getChatList()), ("title", [ACTIVE], reader.getChatTitle(ACTIVE)), ("messages", [ARCHIVED], reader.getChatMessage(ARCHIVED))):
            with self.subTest(command=command):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = C_CommandLine.main(["--home", str(self.home), command, *arguments])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output.getvalue()), expected)

    def test_cli_missing_title_returns_prefixed_legacy_title_and_success(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            code = C_CommandLine.main(["--home", str(self.home), "title", ACTIVE])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), "<TitleMissing>Original prompt")

    def test_cli_title_truncates_long_title(self):
        self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", ("中" * 41, ACTIVE))
        self.connection.commit()
        output = io.StringIO()
        with redirect_stdout(output):
            code = C_CommandLine.main(["--home", str(self.home), "title", ACTIVE])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), "中" * 40 + "...")

    def test_cli_unknown_id_returns_structured_error_and_exit_two(self):
        output = io.StringIO()
        with redirect_stderr(output):
            code = C_CommandLine.main(["--home", str(self.home), "title", "unknown"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["error"], "ChatNotFoundError")

    def test_context_only_journal_does_not_leak_system_data(self):
        self.write_log(ACTIVE, events=[self.response("system", "SECRET"), self.response("user", "<environment_context>SECRET</environment_context>")])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [])

    def test_reasoning_content_parts_inside_final_message_are_excluded(self):
        answer = self.response("assistant", "VISIBLE_ANSWER", phase="final_answer")
        answer["payload"]["content"].insert(0, {"type": "reasoning_text", "text": "SECRET"})
        self.write_log(ACTIVE, events=[answer])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [{"role": "assistant", "content": "VISIBLE_ANSWER"}])

    def test_malformed_content_type_is_ignored_without_crashing(self):
        answer = self.response("assistant", "VISIBLE_ANSWER")
        answer["payload"]["content"].insert(0, {"type": [], "text": "SECRET"})
        self.write_log(ACTIVE, events=[answer])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [{"role": "assistant", "content": "VISIBLE_ANSWER"}])

    def test_empty_message_and_non_text_payload_are_not_coerced_to_strings(self):
        self.write_log(ACTIVE, events=[self.response("user", "  "), {"type": "event_msg", "payload": {"type": "agent_message", "message": {"payload": "SECRET"}}}])
        self.assertEqual(self.reader().getChatMessage(ACTIVE), [])

    def test_missing_display_name_prefixes_index_title_before_database_title(self):
        self.connection.execute("UPDATE threads SET name = NULL WHERE id = ?", (ACTIVE,))
        self.connection.commit()
        self.index([{"id": ACTIVE, "title": "unverified old name"}])
        self.assertEqual(self.reader().getChatTitle(ACTIVE), "<TitleMissing>unverified old name")

    def test_list_has_no_implicit_result_limit(self):
        self.index([{"id": f"index-only-{ordinal}", "thread_name": f"title-{ordinal}"} for ordinal in range(150)])
        self.assertEqual(len(self.reader().getChatList()), 152)

    def test_index_accepts_thread_id_key(self):
        self.index([{"thread_id": EXTRA, "thread_name": "alternate id key"}])
        self.assertEqual(self.reader().getChatTitle(EXTRA), "alternate id key")

    def test_records_are_opened_only_in_binary_read_mode(self):
        original_open = Path.open
        calls = []

        def record_open(path, *arguments, **keywords):
            if path.suffix == ".jsonl":
                calls.append(arguments[0] if arguments else keywords.get("mode", "r"))
            return original_open(path, *arguments, **keywords)

        reader = self.reader()
        with patch.object(Path, "open", record_open):
            reader.getChatList()
            reader.getChatTitle(ACTIVE)
            reader.getChatMessage(ACTIVE)
        self.assertTrue(calls)
        self.assertEqual(set(calls), {"rb"})

    def test_authentication_and_unrelated_secrets_are_never_opened(self):
        (self.home / "auth.json").write_text('{"token":"SECRET"}', encoding="utf-8")
        original_open = Path.open

        def guarded_open(path, *arguments, **keywords):
            self.assertNotEqual(path.name, "auth.json")
            return original_open(path, *arguments, **keywords)

        reader = self.reader()
        with patch.object(Path, "open", guarded_open):
            reader.getChatList()
            reader.getChatTitle(ACTIVE)
            reader.getChatMessage(ACTIVE)

    def test_commands_in_historical_messages_are_not_executed(self):
        self.write_log(ACTIVE, events=self.events(ACTIVE, "os.system('HISTORY_COMMAND')", "subprocess.run('HISTORY_COMMAND')"))
        with patch("os.system", side_effect=AssertionError("must not execute history")), patch("subprocess.run", side_effect=AssertionError("must not execute history")):
            messages = self.reader().getChatMessage(ACTIVE)
        self.assertIn("HISTORY_COMMAND", messages[0]["content"])

    def test_initial_snapshot_boundary_excludes_concurrent_append(self):
        reader = self.reader()
        original_records = reader._store.records
        appended = False

        def append_during_read(path, limit=None):
            nonlocal appended
            if path == self.active and limit is not None and not appended:
                with self.active.open("ab") as stream:
                    stream.write(json.dumps(self.response("assistant", "LATE_ANSWER", phase="final_answer")).encode() + b"\n")
                appended = True
            yield from original_records(path, limit)

        with patch.object(reader._store, "records", side_effect=append_during_read), self.assertWarns(HistoryReadWarning):
            messages = reader.getChatMessage(ACTIVE)
        self.assertTrue(appended)
        self.assertEqual(len(messages), 2)
        self.assertNotIn("LATE_ANSWER", json.dumps(messages))

    def test_message_results_are_fresh_and_mutable_without_affecting_history(self):
        reader = self.reader()
        result = reader.getChatMessage(ACTIVE)
        result[0]["content"] = "changed by caller"
        self.assertEqual(reader.getChatMessage(ACTIVE)[0]["content"], "用户的问题")

    def test_corrupt_index_warns_without_hiding_database_display_name(self):
        (self.home / "session_index.jsonl").write_bytes(b'{"partial": "invalid')
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader().getChatTitle(ACTIVE), "Active display name")

    def test_python_module_cli_is_installed_and_emits_utf8(self):
        execution = subprocess.run([sys.executable, "-B", "-m", "codex_history_interface", "--home", str(self.home), "title", ARCHIVED], capture_output=True, check=False)
        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(json.loads(execution.stdout.decode("utf-8")), "归档展示名")

    def test_utf8_title_includes_non_ascii_symbols_and_emoji(self):
        title = "标题 🚀 数学 ∑"
        self.connection.execute("UPDATE threads SET name = ? WHERE id = ?", (title, ACTIVE))
        self.connection.commit()
        execution = subprocess.run([sys.executable, "-B", "-m", "codex_history_interface", "--home", str(self.home), "title", ACTIVE], capture_output=True, check=False)
        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(json.loads(execution.stdout.decode("utf-8")), title)


if __name__ == "__main__":
    unittest.main()
