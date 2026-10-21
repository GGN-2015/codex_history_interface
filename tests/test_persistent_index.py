from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import warnings

from codex_history_interface import C_CodexInterface, C_PersistentRoughMatchIndex, C_RoughMatchIndex, C_SearchConfig, HistoryReadWarning, HistoryUnavailableError
from test_export_tex import CHAT_ID, START, ExportFixture


class PersistentIndexTests(ExportFixture, unittest.TestCase):
    def new_reader(self):
        return C_CodexInterface(self.home, search_config=C_SearchConfig(self.root / "search-index"))

    def append_message(self, text):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.message("user", text, START + 30), ensure_ascii=False) + "\n")

    def add_archived_chat(self, text):
        identity = "22222222-2222-4222-8222-222222222222"
        path = self.home / "archived_sessions" / ("custom-" + identity + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [{"type": "session_meta", "payload": {"id": identity}}, self.message("user", text, START)]
        path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
        return identity, path

    def child_script(self):
        return """
import json
import sys
from unittest.mock import patch
from codex_history_interface import C_CodexInterface, C_SearchConfig
data = json.load(sys.stdin)
reader = C_CodexInterface(data['home'], search_config=C_SearchConfig(data['index']))
repository = reader.repository
index = repository._search_index
with patch.object(repository, '_readChatMessages', wraps=repository._readChatMessages) as reads, patch.object(index, 'updateDocument', wraps=index.updateDocument) as updates:
    ids = reader.roughMatch(data['query'], 10)
print(json.dumps({'ids': ids, 'reads': [call.args[0].identity for call in reads.call_args_list], 'updates': [call.args[0] for call in updates.call_args_list]}, ensure_ascii=False))
"""

    def run_child(self, query):
        data = {"home": str(self.home), "index": str(self.root / "search-index"), "query": query}
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", self.child_script()], input=json.dumps(data), capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_first_query_creates_a_separate_sqlite_inverted_index(self):
        path = self.reader.getSearchIndexPath()
        self.assertFalse(path.exists())
        self.assertEqual(self.reader.roughMatch("表格", 5), [CHAT_ID])
        self.assertTrue(path.is_file())
        self.assertFalse(path.is_relative_to(self.home))
        with closing(sqlite3.connect(path)) as connection:
            self.assertEqual(connection.execute("SELECT chat_id FROM documents").fetchall(), [(CHAT_ID,)])
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM postings").fetchone()[0], 0)
            plan = connection.execute("EXPLAIN QUERY PLAN SELECT * FROM postings WHERE term = ?", ("u:表",)).fetchone()[3]
            self.assertIn("PRIMARY KEY", plan)

    def test_new_instance_reuses_postings_and_fingerprints_without_history_reads(self):
        expected = self.reader.roughMatch("最终答案", 5)
        reader = self.new_reader()
        with patch.object(reader._store, "refresh", side_effect=AssertionError("history refresh")), patch.object(reader.repository, "_readChatMessages", side_effect=AssertionError("body read")), patch.object(reader.repository._search_index, "updateDocument", side_effect=AssertionError("reindex")):
            self.assertEqual(reader.roughMatch("最终答案", 5), expected)

    def test_new_process_reuses_the_persisted_index(self):
        self.reader.roughMatch("表格", 5)
        result = self.run_child("表格")
        self.assertEqual(result, {"ids": [CHAT_ID], "reads": [], "updates": []})

    def test_new_process_only_reindexes_the_appended_conversation(self):
        self.add_archived_chat("volcano")
        self.reader.roughMatch("表格", 5)
        self.append_message("quasar")
        result = self.run_child("quasar")
        self.assertEqual(result, {"ids": [CHAT_ID], "reads": [CHAT_ID], "updates": [CHAT_ID]})

    def test_new_process_only_indexes_a_new_archived_conversation(self):
        self.reader.roughMatch("表格", 5)
        identity, _ = self.add_archived_chat("volcano")
        result = self.run_child("volcano")
        self.assertEqual(result, {"ids": [identity], "reads": [identity], "updates": [identity]})

    def test_title_changes_do_not_reparse_the_persisted_body(self):
        self.reader.roughMatch("表格", 5)
        self.connection.execute("UPDATE threads SET name='quasar' WHERE id=?", (CHAT_ID,))
        self.connection.commit()
        reader = self.new_reader()
        with patch.object(reader.repository, "_readChatMessages", side_effect=AssertionError("body read")), patch.object(reader.repository._search_index, "updateDocument", wraps=reader.repository._search_index.updateDocument) as update:
            self.assertEqual(reader.roughMatch("quasar", 5), [CHAT_ID])
        self.assertEqual(update.call_count, 1)
        self.assertEqual(update.call_args.args, (CHAT_ID, "quasar"))
        self.assertEqual(reader.roughMatch("表格", 5), [CHAT_ID])

    def test_deleted_conversations_only_remove_their_own_postings(self):
        identity, path = self.add_archived_chat("volcano")
        self.reader.roughMatch("表格", 5)
        path.unlink()
        reader = self.new_reader()
        with patch.object(reader.repository, "_readChatMessages", side_effect=AssertionError("body read")), patch.object(reader.repository._search_index, "removeDocument", wraps=reader.repository._search_index.removeDocument) as remove:
            self.assertEqual(reader.roughMatch("volcano", 5), [])
        remove.assert_called_once_with(identity)
        self.assertEqual(reader.roughMatch("表格", 5), [CHAT_ID])

    def test_warm_queries_do_not_rewrite_the_database(self):
        self.reader.roughMatch("表格", 5)
        path = self.reader.getSearchIndexPath()
        before = path.read_bytes(), path.stat().st_mtime_ns
        self.new_reader().roughMatch("最终答案", 5)
        self.reader.roughMatch("表格", 5)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_external_journal_paths_survive_new_instances(self):
        external = self.root / "external journal.jsonl"
        self.path.rename(external)
        self.connection.execute("ALTER TABLE threads ADD COLUMN rollout_path TEXT")
        self.connection.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(external), CHAT_ID))
        self.connection.commit()
        self.reader.roughMatch("表格", 5)
        reader = self.new_reader()
        with patch.object(reader._store, "refresh", side_effect=AssertionError("unchanged external journal")):
            self.assertEqual(reader.roughMatch("表格", 5), [CHAT_ID])
        with external.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.message("user", "quasar", START + 30)) + "\n")
        self.assertEqual(reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_projection_changes_after_restart_only_reindex_the_changed_chat(self):
        self.path.unlink()
        database = self.home / "thread_history_1.sqlite"
        identity = "22222222-2222-4222-8222-222222222222"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE thread_items (thread_id TEXT, item_json TEXT, item_id TEXT, created_at_ms INTEGER)")
            for chat_id, text in ((CHAT_ID, "nebula"), (identity, "volcano")):
                connection.execute("INSERT INTO thread_items VALUES (?, ?, ?, ?)", (chat_id, json.dumps({"type": "agentMessage", "text": text, "phase": "final_answer"}), chat_id, int(START * 1000)))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", HistoryReadWarning)
            self.reader.roughMatch("nebula", 5)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE thread_items SET item_json=? WHERE thread_id=?", (json.dumps({"type": "agentMessage", "text": "quasar", "phase": "final_answer"}), CHAT_ID))
        reader = self.new_reader()
        with warnings.catch_warnings(), patch.object(reader.repository, "_readChatMessages", wraps=reader.repository._readChatMessages) as read:
            warnings.simplefilter("ignore", HistoryReadWarning)
            self.assertEqual(reader.roughMatch("quasar", 5), [CHAT_ID])
        self.assertEqual([call.args[0].identity for call in read.call_args_list], [CHAT_ID])

    def test_corrupt_cache_is_preserved_and_rebuilt_not_history(self):
        self.reader.roughMatch("表格", 5)
        path = self.reader.getSearchIndexPath()
        path.write_bytes(b"not a sqlite database")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.new_reader().roughMatch("表格", 5), [CHAT_ID])
        backups = list(path.parent.glob(path.name + ".invalid-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"not a sqlite database")

    def test_incompatible_metadata_version_rebuilds_the_owned_cache(self):
        self.reader.roughMatch("表格", 5)
        path = self.reader.getSearchIndexPath()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("UPDATE metadata SET value='999' WHERE key='version'")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.new_reader().roughMatch("表格", 5), [CHAT_ID])
        self.assertEqual(len(list(path.parent.glob(path.name + ".invalid-*"))), 1)

    def test_metadata_checksum_failure_rebuilds_the_owned_cache(self):
        self.reader.roughMatch("表格", 5)
        with closing(sqlite3.connect(self.reader.getSearchIndexPath())) as connection, connection:
            connection.execute("UPDATE metadata SET value='null' WHERE key='state'")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.new_reader().roughMatch("表格", 5), [CHAT_ID])

    def test_missing_postings_table_is_recovered_before_querying(self):
        self.reader.roughMatch("表格", 5)
        with closing(sqlite3.connect(self.reader.getSearchIndexPath())) as connection, connection:
            connection.execute("DROP TABLE postings")
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.new_reader().roughMatch("表格", 5), [CHAT_ID])

    def test_locked_cache_reports_an_error_without_renaming_it(self):
        self.reader.roughMatch("表格", 5)
        reader = self.new_reader()
        reader.repository.getSearchConfig()._sqlite_timeout = 0.01
        path = reader.getSearchIndexPath()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(HistoryUnavailableError):
                reader.roughMatch("表格", 5)
            connection.rollback()
        self.assertEqual(list(path.parent.glob(path.name + ".invalid-*")), [])

    def test_failed_query_rolls_back_index_and_fingerprints_together(self):
        self.reader.roughMatch("表格", 5)
        path = self.reader.getSearchIndexPath()
        before = path.read_bytes()
        self.append_message("quasar")
        with patch.object(self.reader.repository._search_index, "roughMatch", side_effect=RuntimeError("interrupted")), self.assertRaises(RuntimeError):
            self.reader.roughMatch("quasar", 5)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_two_processes_serialize_the_first_build_and_share_results(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.run_child, ("表格", "最终答案")))
        self.assertEqual([result["ids"] for result in results], [[CHAT_ID], [CHAT_ID]])
        self.assertEqual(sorted(len(result["reads"]) for result in results), [0, 1])

    def test_live_readers_see_updates_committed_by_another_reader(self):
        self.reader.roughMatch("表格", 5)
        other = self.new_reader()
        other.roughMatch("表格", 5)
        self.append_message("quasar")
        self.assertEqual(other.roughMatch("quasar", 5), [CHAT_ID])
        with patch.object(self.reader.repository, "_readChatMessages", side_effect=AssertionError("already indexed by other reader")):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_search_does_not_write_to_any_original_history_file(self):
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.reader.roughMatch("表格", 5)
        self.new_reader().roughMatch("最终答案", 5)
        after = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.home.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_cache_excludes_reasoning_system_and_raw_message_records(self):
        self.reader.roughMatch("表格", 5)
        path = self.reader.getSearchIndexPath()
        for secret in (b"REASONING_SECRET", b"PROGRESS_SECRET", b"SYSTEM_SECRET", b"response_item", b"final_answer"):
            self.assertNotIn(secret, path.read_bytes())
        self.assertEqual(self.reader.roughMatch("REASONING", 5), [])

    def test_namespaces_are_stable_and_separate_different_sources(self):
        first, second = self.root / "one.sqlite", self.root / "two.sqlite"
        first.touch()
        second.touch()
        config = C_SearchConfig(self.root / "indexes")
        reader1 = C_CodexInterface(self.home, databases=[first, second], search_config=config)
        reader2 = C_CodexInterface(self.home, databases=[second, first, first], search_config=config)
        reader3 = C_CodexInterface(self.home, databases=[first], search_config=config)
        self.assertEqual(reader1.getSearchIndexPath(), reader2.getSearchIndexPath())
        self.assertNotEqual(reader1.getSearchIndexPath(), reader3.getSearchIndexPath())
        another_home = self.root / "another codex"
        another_home.mkdir()
        self.assertNotEqual(C_CodexInterface(another_home, search_config=config).getSearchIndexPath(), C_CodexInterface(self.home, search_config=config).getSearchIndexPath())

    def test_standalone_persistent_index_matches_memory_scoring_and_reopens(self):
        config = C_SearchConfig(self.root / "standalone")
        disk = C_PersistentRoughMatchIndex("sample", config=config)
        memory = C_RoughMatchIndex(config)
        for identity, title, body in (("chat-1", "截骨规划 Qt", "Markdown table"), ("chat-2", "Qt GUI", "截骨手术规划"), ("chat-3", "公式导出", "Markdown 数学表格")):
            disk.updateDocument(identity, title, body)
            memory.updateDocument(identity, title, body)
        reopened = C_PersistentRoughMatchIndex("sample", config=config)
        for query in ("截骨", "Qt", "markdwon", "公式", "no-match"):
            self.assertEqual(reopened.roughMatch(query, 10), memory.roughMatch(query, 10))
        reopened.updateDocument("chat-1", "新的标题")
        memory.updateDocument("chat-1", "新的标题")
        self.assertEqual(reopened.roughMatch("Markdown", 10), memory.roughMatch("Markdown", 10))
        self.assertTrue(reopened.removeDocument("chat-2"))
        self.assertEqual(set(reopened.getChatList()), {"chat-1", "chat-3"})

    def test_postings_are_written_in_primary_key_order(self):
        batches = []
        original_connect = sqlite3.connect

        class RecordingConnection(sqlite3.Connection):
            def executemany(self, sql, parameters):
                if sql.startswith("INSERT INTO postings"):
                    parameters = list(parameters)
                    batches.append(parameters)
                return super().executemany(sql, parameters)

        def connect(*arguments, **keywords):
            return original_connect(*arguments, **keywords, factory=RecordingConnection)

        index = C_PersistentRoughMatchIndex("sorted", config=C_SearchConfig(self.root / "standalone"))
        with patch.object(sqlite3, "connect", side_effect=connect), index.session():
            index.updateDocument("chat-1", "Unicode 全角Ａ", ["Markdown 表格 nebula", "重复词 nebula"])
            index.updateDocument("chat-2", "其他标题", "Python rendering")
            index.updateDocument("chat-1", "更新标题")
        self.assertEqual(len(batches), 3)
        for batch in batches:
            terms = [row[0] for row in batch]
            self.assertEqual(terms, sorted(set(terms)))
        self.assertEqual(index.roughMatch("nebula", 1), ["chat-1"])

    def test_write_cache_is_lazy_and_configured_once_per_session(self):
        config = C_SearchConfig(self.root / "standalone")
        index = C_PersistentRoughMatchIndex("write-cache", config=config)
        with index.session():
            connection = index._connection
            initial_cache = connection.execute("PRAGMA cache_size").fetchone()[0]
            settings = [connection.execute("PRAGMA " + name).fetchone()[0] for name in ("journal_mode", "synchronous")]
            statements = []
            connection.set_trace_callback(statements.append)
            self.assertFalse(index._write_cache_configured)
            index.updateDocument("chat-1", "标题", "nebula")
            index.updateDocument("chat-2", "其他标题", "quasar")
            self.assertEqual(connection.execute("PRAGMA cache_size").fetchone()[0], -65536)
            self.assertEqual(sum(statement.startswith("PRAGMA cache_size = ") for statement in statements), 1)
            self.assertEqual([connection.execute("PRAGMA " + name).fetchone()[0] for name in ("journal_mode", "synchronous")], settings)
        self.assertFalse(index._write_cache_configured)
        with index.session():
            self.assertEqual(index._connection.execute("PRAGMA cache_size").fetchone()[0], initial_cache)
            self.assertEqual(index.roughMatch("nebula", 1), ["chat-1"])
            self.assertFalse(index._write_cache_configured)
            index.updateDocument("chat-1", "更新标题", "nebula")
            self.assertEqual(index._connection.execute("PRAGMA cache_size").fetchone()[0], -65536)

    def test_custom_write_cache_does_not_change_history_connections(self):
        config = C_SearchConfig(self.root / "standalone")
        config._sqlite_write_cache_kib = 1024
        index = C_PersistentRoughMatchIndex("custom-cache", config=config)
        history_cache = self.connection.execute("PRAGMA cache_size").fetchone()[0]
        with index.session():
            index.updateDocument("chat", "标题", "nebula")
            self.assertEqual(index._connection.execute("PRAGMA cache_size").fetchone()[0], -1024)
        self.assertEqual(self.connection.execute("PRAGMA cache_size").fetchone()[0], history_cache)

    def test_invalid_write_cache_rolls_back_without_quarantining_healthy_index(self):
        config = C_SearchConfig(self.root / "standalone")
        index = C_PersistentRoughMatchIndex("invalid-cache", config=config)
        index.updateDocument("chat", "标题", "nebula")
        before = index.getIndexPath().read_bytes()
        for invalid in (0, -1, True, 1.5, "1024; DROP TABLE documents", None, 2147483648):
            with self.subTest(cache_kib=invalid):
                config._sqlite_write_cache_kib = invalid
                with self.assertRaisesRegex(ValueError, "write cache size"):
                    index.updateDocument("chat", "替换标题", "quasar")
                self.assertEqual(index.getIndexPath().read_bytes(), before)
                self.assertFalse(list(index.getIndexPath().parent.glob("*.invalid-*")))
                self.assertFalse(index._write_cache_configured)
                self.assertEqual(index.roughMatch("nebula", 1), ["chat"])
                self.assertEqual(index.roughMatch("quasar", 1), [])

    def test_standalone_index_is_safe_for_concurrent_threads(self):
        index = C_PersistentRoughMatchIndex("threads", config=C_SearchConfig(self.root / "standalone"))
        index.updateDocument("chat", "并发", "nebula")
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda unused: index.roughMatch("nebula", 1), range(12)))
        self.assertEqual(results, [["chat"]] * 12)

    def test_invalid_queries_do_not_create_any_cache(self):
        self.assertEqual(self.reader.roughMatch(" ", 5), [])
        self.assertEqual(self.reader.roughMatch("表格", 0), [])
        with self.assertRaises(ValueError):
            self.reader.roughMatch("表格", -1)
        self.assertFalse(self.reader.getSearchIndexPath().parent.exists())

    def test_unusable_cache_directory_reports_the_io_error(self):
        directory = self.root / "not-a-directory"
        directory.write_bytes(b"untouched")
        reader = C_CodexInterface(self.home, search_config=C_SearchConfig(directory))
        with self.assertRaises(OSError):
            reader.roughMatch("表格", 5)
        self.assertEqual(directory.read_bytes(), b"untouched")

    def test_cached_source_warnings_are_reported_by_new_readers(self):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("not valid JSON\n")
        with self.assertWarns(HistoryReadWarning):
            self.reader.roughMatch("表格", 5)
        reader = self.new_reader()
        with self.assertWarns(HistoryReadWarning), patch.object(reader._store, "refresh", side_effect=AssertionError("cached warning should not require a rescan")):
            self.assertEqual(reader.roughMatch("表格", 5), [CHAT_ID])
        self.assertTrue(reader.diagnostics)

    def test_append_while_discovering_an_external_journal_is_not_lost(self):
        external = self.root / "external.jsonl"
        self.path.rename(external)
        self.connection.execute("ALTER TABLE threads ADD COLUMN rollout_path TEXT")
        self.connection.execute("UPDATE threads SET rollout_path=? WHERE id=?", (str(external), CHAT_ID))
        self.connection.commit()
        original = self.reader._store.refresh

        def refresh_then_append():
            original()
            with external.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(self.message("user", "quasar", START + 30)) + "\n")

        with patch.object(self.reader._store, "refresh", side_effect=refresh_then_append), warnings.catch_warnings():
            warnings.simplefilter("ignore", HistoryReadWarning)
            self.assertEqual(self.reader.roughMatch("quasar", 5), [])
        with self.assertWarns(HistoryReadWarning):
            self.assertEqual(self.new_reader().roughMatch("quasar", 5), [CHAT_ID])
