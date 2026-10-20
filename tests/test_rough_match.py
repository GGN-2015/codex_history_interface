from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from codex_history_interface import C_CodexInterface, C_HistoryRepository, HistoryReadWarning
from test_export_tex import CHAT_ID, START, ExportFixture


ARCHIVED_ID = "22222222-2222-4222-8222-222222222222"


class RoughMatchTests(ExportFixture, unittest.TestCase):
    def add_chat(self, identity=ARCHIVED_ID, *, title="晚餐食谱", content="今天做饭", archived=True):
        self.connection.execute("INSERT INTO threads VALUES (?, ?, NULL, ?, NULL, ?, NULL, ?)", (identity, title, START, START + 1, int(archived)))
        self.connection.commit()
        folder = self.home / ("archived_sessions" if archived else "sessions")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("rollout-" + identity + ".jsonl")
        records = [{"type": "session_meta", "payload": {"id": identity}}, self.message("user", content, START)]
        path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
        return path

    def projection(self, text, *, identity=CHAT_ID, phase="final_answer"):
        database = self.home / "thread_history_1.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS thread_items (thread_id TEXT, item_json TEXT)")
            connection.execute("INSERT INTO thread_items VALUES (?, ?)", (identity, json.dumps({"type": "agentMessage", "text": text, "phase": phase})))
        return database

    def test_user_and_final_assistant_messages_are_searchable(self):
        self.assertEqual(self.reader.roughMatch("表格和公式", 5), [CHAT_ID])
        self.assertEqual(self.reader.roughMatch("最终答案", 5), [CHAT_ID])

    def test_system_reasoning_progress_and_raw_json_keys_are_not_indexed(self):
        for query in ("SYSTEM_SECRET", "REASONING_SECRET", "PROGRESS_SECRET", "response_item", "timestamp"):
            with self.subTest(query=query):
                self.assertEqual(self.reader.roughMatch(query, 5), [])

    def test_persisted_display_title_is_used_instead_of_legacy_title(self):
        self.connection.execute("UPDATE threads SET name = 'volcano', title = 'nebula'")
        self.connection.commit()
        self.assertEqual(self.reader.roughMatch("volcano", 5), [CHAT_ID])
        self.assertEqual(self.reader.roughMatch("nebula", 5), [])

    def test_full_title_is_indexed_even_when_display_is_truncated(self):
        title = "甲" * 45 + "截骨规划"
        self.connection.execute("UPDATE threads SET name = ?", (title,))
        self.connection.commit()
        self.assertEqual(len(self.reader.getChatTitle(CHAT_ID)), 43)
        self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])

    def test_missing_title_marker_is_not_searchable_content(self):
        self.connection.execute("UPDATE threads SET name = NULL")
        self.connection.commit()
        self.assertEqual(self.reader.roughMatch("旧标题", 5), [CHAT_ID])
        self.assertEqual(self.reader.roughMatch("TitleMissing", 5), [])

    def test_archived_conversations_are_included_by_default(self):
        self.add_chat()
        self.assertEqual(self.reader.roughMatch("晚餐食谱", 1), [ARCHIVED_ID])

    def test_missing_marker_text_is_kept_when_it_is_part_of_a_real_display_name(self):
        self.connection.execute("UPDATE threads SET name = '<TitleMissing>真实展示名'")
        self.connection.commit()
        self.assertEqual(self.reader.roughMatch("TitleMissing", 5), [CHAT_ID])

    def test_metadata_only_conversations_can_match_their_title(self):
        self.path.unlink()
        self.assertEqual(self.reader.roughMatch("导出测试", 1), [CHAT_ID])

    def test_index_only_conversations_can_match_their_display_title(self):
        index = self.home / "session_index.jsonl"
        index.write_text(json.dumps({"id": ARCHIVED_ID, "thread_name": "截骨规划"}), encoding="utf-8")
        self.assertEqual(self.reader.roughMatch("截骨规划", 1), [ARCHIVED_ID])

    def test_results_are_ids_ranked_by_relevance_and_limited(self):
        self.add_chat(title="表格和公式", content="其他内容")
        result = self.reader.roughMatch("表格和公式", 1)
        self.assertEqual(result, [ARCHIVED_ID])
        self.assertEqual(self.reader.getChatTitle(result[0]), "表格和公式")
        self.assertEqual(self.reader.getChatMessage(result[0]), [{"role": "user", "content": "其他内容"}])

    def test_warm_queries_do_not_refresh_or_read_any_history_body(self):
        self.reader.roughMatch("表格", 5)
        with patch.object(self.reader._store, "refresh", side_effect=AssertionError("warm refresh")), patch.object(self.reader._store, "records", side_effect=AssertionError("warm JSONL read")), patch.object(self.reader._store, "projection_records", side_effect=AssertionError("warm projection read")):
            self.assertEqual(self.reader.roughMatch("最终答案", 5), [CHAT_ID])
            self.assertEqual(self.reader.roughMatch("astronomy", 5), [])

    def test_first_build_refreshes_once_and_does_not_use_per_chat_public_getters(self):
        self.add_chat()
        repository = self.reader.repository
        with patch.object(repository._store, "refresh", wraps=repository._store.refresh) as refresh:
            with patch.object(repository, "getChatMessage", side_effect=AssertionError("per-chat refresh")), patch.object(repository, "getChatTitle", side_effect=AssertionError("per-chat metadata refresh")):
                self.assertEqual(self.reader.roughMatch("食谱", 5), [ARCHIVED_ID])
        refresh.assert_called_once_with()

    def test_warm_source_check_does_not_iterate_all_conversation_metadata(self):
        class NonIterableValuesDict(dict):
            def values(self):
                raise AssertionError("All conversation metadata scanned")

        self.reader.roughMatch("表格", 5)
        self.reader._store.conversations = NonIterableValuesDict(self.reader._store.conversations)
        self.assertEqual(self.reader.roughMatch("最终答案", 5), [CHAT_ID])

    def test_changed_journal_reindexes_only_its_conversation(self):
        self.add_chat()
        self.reader.roughMatch("表格", 5)
        self.records.append(self.message("user", "截骨规划", START + 1000))
        self.write()
        repository = self.reader.repository
        with patch.object(repository, "_readChatMessages", wraps=repository._readChatMessages) as read:
            self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])
        self.assertEqual([call.args[0].identity for call in read.call_args_list], [CHAT_ID])

    def test_title_rename_does_not_reread_unchanged_message_bodies(self):
        self.reader.roughMatch("表格", 5)
        self.connection.execute("UPDATE threads SET name = '截骨规划'")
        self.connection.commit()
        with patch.object(self.reader.repository, "_readChatMessages", side_effect=AssertionError("title-only body read")):
            self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])
            self.assertEqual(self.reader.roughMatch("最终答案", 5), [CHAT_ID])

    def test_changes_during_indexing_are_detected_by_the_next_query(self):
        repository = self.reader.repository
        original = repository._readChatMessages

        def read_then_append(conversation, *, with_attachments):
            messages = original(conversation, with_attachments=with_attachments)
            self.records.append(self.message("user", "截骨规划", START + 1000))
            self.write()
            return messages

        with patch.object(repository, "_readChatMessages", side_effect=read_then_append):
            self.assertEqual(self.reader.roughMatch("截骨规划", 5), [])
        self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])

    def test_updated_timestamp_alone_does_not_retokenize_messages(self):
        self.reader.roughMatch("表格", 5)
        self.connection.execute("UPDATE threads SET updated_at = ?", (START + 10000,))
        self.connection.commit()
        with patch.object(self.reader.repository, "_readChatMessages", side_effect=AssertionError("metadata-only body read")), patch.object(self.reader.repository._search_index, "updateDocument", side_effect=AssertionError("unnecessary reindex")):
            self.assertEqual(self.reader.roughMatch("最终答案", 5), [CHAT_ID])

    def test_added_and_deleted_conversations_are_reflected(self):
        self.reader.roughMatch("表格", 5)
        path = self.add_chat()
        self.assertEqual(self.reader.roughMatch("食谱", 5), [ARCHIVED_ID])
        self.connection.execute("DELETE FROM threads WHERE id = ?", (ARCHIVED_ID,))
        self.connection.commit()
        path.unlink()
        self.assertEqual(self.reader.roughMatch("食谱", 5), [])

    def test_journal_only_conversation_disappears_after_its_last_source_is_deleted(self):
        path = self.add_chat()
        self.connection.execute("DELETE FROM threads WHERE id = ?", (ARCHIVED_ID,))
        self.connection.commit()
        self.assertEqual(self.reader.roughMatch("做饭", 5), [ARCHIVED_ID])
        path.unlink()
        self.assertEqual(self.reader.roughMatch("做饭", 5), [])

    def test_archive_move_preserves_searchability(self):
        self.reader.roughMatch("表格", 5)
        target = self.home / "archived_sessions" / self.path.name
        target.parent.mkdir()
        self.path.rename(target)
        self.connection.execute("UPDATE threads SET archived = 1")
        self.connection.commit()
        self.assertEqual(self.reader.roughMatch("表格", 5), [CHAT_ID])

    def test_same_length_edit_is_detected_by_file_metadata(self):
        self.records.append(self.message("user", "截骨", START + 1000))
        self.write()
        self.assertEqual(self.reader.roughMatch("截骨", 5), [CHAT_ID])
        size = self.path.stat().st_size
        self.records[-1] = self.message("user", "食谱", START + 1000)
        self.write()
        self.assertEqual(self.path.stat().st_size, size)
        os.utime(self.path, ns=(self.path.stat().st_atime_ns, self.path.stat().st_mtime_ns + 1_000_000_000))
        self.assertEqual(self.reader.roughMatch("截骨", 5), [])
        self.assertEqual(self.reader.roughMatch("食谱", 5), [CHAT_ID])

    def test_index_title_update_is_detected(self):
        self.connection.execute("UPDATE threads SET name = NULL, title = NULL")
        self.connection.commit()
        index = self.home / "session_index.jsonl"
        index.write_text(json.dumps({"id": CHAT_ID, "thread_name": "截骨规划"}), encoding="utf-8")
        self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])
        index.write_text(json.dumps({"id": CHAT_ID, "thread_name": "晚餐食谱"}), encoding="utf-8")
        self.assertEqual(self.reader.roughMatch("晚餐食谱", 5), [CHAT_ID])

    def test_projection_fallback_is_indexed_and_refreshes(self):
        self.path.unlink()
        self.projection("截骨规划")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])
        self.projection("晚餐食谱")
        self.assertEqual(self.reader.roughMatch("晚餐食谱", 5), [CHAT_ID])

    def test_projection_changes_do_not_reindex_existing_visible_journals(self):
        self.projection("截骨规划", identity=ARCHIVED_ID)
        with self.assertWarns(HistoryReadWarning):
            self.reader.roughMatch("表格", 5)
        self.projection("晚餐食谱", identity=ARCHIVED_ID)
        repository = self.reader.repository
        with patch.object(repository, "_readChatMessages", wraps=repository._readChatMessages) as read:
            self.assertEqual(self.reader.roughMatch("晚餐食谱", 5), [ARCHIVED_ID])
        self.assertEqual([call.args[0].identity for call in read.call_args_list], [ARCHIVED_ID])

    def test_nonfinal_projected_analysis_is_not_searchable(self):
        self.path.unlink()
        self.projection("REASONING_SECRET", phase="analysis")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.roughMatch("REASONING_SECRET", 5), [])

    def test_wal_commit_invalidates_cached_titles_without_checkpoint(self):
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.reader.roughMatch("表格", 5)
        self.connection.execute("UPDATE threads SET name = '截骨规划'")
        self.connection.commit()
        self.assertTrue(Path(str(self.database) + "-wal").is_file())
        self.assertEqual(self.reader.roughMatch("截骨规划", 5), [CHAT_ID])

    def test_explicit_external_database_and_journal_are_supported(self):
        database = self.root / "external" / "state_3.sqlite"
        database.parent.mkdir()
        journal = database.parent / "external.jsonl"
        records = [{"type": "session_meta", "payload": {"id": ARCHIVED_ID}}, self.message("user", "截骨规划", START)]
        journal.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
            connection.execute("INSERT INTO threads VALUES (?, ?)", (ARCHIVED_ID, str(journal)))
        reader = C_CodexInterface(self.home, databases=[database])
        self.assertEqual(reader.roughMatch("截骨规划", 5), [ARCHIVED_ID])
        self.assertEqual(reader.roughMatch("截骨规划", 5), [ARCHIVED_ID])
        with journal.open("a", encoding="utf-8") as stream:
            stream.write("\n" + json.dumps(self.message("user", "晚餐食谱", START + 1)) + "\n")
        self.assertEqual(reader.roughMatch("晚餐食谱", 5), [ARCHIVED_ID])

    def test_empty_or_zero_queries_do_not_read_history(self):
        with patch.object(self.reader._store, "source_signature", side_effect=AssertionError("empty query I/O")):
            for content, maximum in (("", 5), (" \n", 5), ("!?", 5), ("表格", 0)):
                with self.subTest(content=content, maximum=maximum):
                    self.assertEqual(self.reader.roughMatch(content, maximum), [])

    def test_invalid_parameters_are_rejected_before_reading_history(self):
        with patch.object(self.reader._store, "source_signature", side_effect=AssertionError("invalid query I/O")):
            for content, maximum, error in ((None, 1, TypeError), ("query", True, TypeError), ("query", 1.5, TypeError), ("query", -1, ValueError)):
                with self.subTest(content=content, maximum=maximum), self.assertRaises(error):
                    self.reader.roughMatch(content, maximum)

    def test_history_bytes_modification_times_and_file_set_remain_unchanged(self):
        self.add_chat()
        before = {path.relative_to(self.home): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(self.reader.roughMatch("表格", 5), [CHAT_ID])
        self.assertEqual(self.reader.roughMatch("食谱", 5), [ARCHIVED_ID])
        after = {path.relative_to(self.home): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_concurrent_searches_share_one_initial_build(self):
        with patch.object(self.reader._store, "refresh", wraps=self.reader._store.refresh) as refresh:
            with ThreadPoolExecutor(max_workers=4) as workers:
                results = list(workers.map(lambda number: self.reader.roughMatch("表格", 5), range(12)))
        self.assertEqual(results, [[CHAT_ID]] * 12)
        refresh.assert_called_once_with()

    def test_standalone_repository_provides_the_same_search(self):
        self.add_chat()
        repository = C_HistoryRepository(self.home)
        self.assertEqual(repository.roughMatch("食谱", 5), self.reader.roughMatch("食谱", 5))

    def test_corrupt_journal_line_reports_warning_without_hiding_valid_messages(self):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("{invalid\n")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.reader.roughMatch("表格", 5), [CHAT_ID])
        self.assertTrue(self.reader.diagnostics)

    def test_query_does_not_compile_or_interpret_regex_or_sql(self):
        self.records.append(self.message("user", "getChatMessage", START + 1000))
        self.write()
        self.assertEqual(self.reader.roughMatch("getChatMessage.*(' OR 1=1)", 1), [CHAT_ID])


if __name__ == "__main__":
    unittest.main()
