from contextlib import closing, contextmanager
import json
import sqlite3
import unittest
from unittest.mock import patch
import warnings

from codex_history_interface import HistoryReadWarning
from test_export_tex import CHAT_ID, START, ExportFixture


OTHER_ID = "22222222-2222-4222-8222-222222222222"
NEW_ID = "33333333-3333-4333-8333-333333333333"


class DifferentialIndexTests(ExportFixture, unittest.TestCase):
    def add_projection(self, identity, text):
        database = self.home / "thread_history_1.sqlite"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS thread_items (thread_id TEXT, item_json TEXT, item_id TEXT, turn_id TEXT, created_at_ms INTEGER, rollout_ordinal INTEGER)")
            connection.execute("CREATE TABLE IF NOT EXISTS thread_turns (thread_id TEXT, turn_id TEXT, final_agent_item_id TEXT)")
            item_id = f"item-{connection.execute('SELECT COUNT(*) FROM thread_items').fetchone()[0]}"
            item = {"type": "agentMessage", "text": text, "phase": "final_answer"}
            connection.execute("INSERT INTO thread_items VALUES (?, ?, ?, ?, ?, ?)", (identity, json.dumps(item), item_id, "turn-1", 1700000000000, 1))
        return database

    def build_projection_index(self):
        self.path.unlink()
        database = self.add_projection(CHAT_ID, "nebula")
        self.add_projection(OTHER_ID, "volcano")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", HistoryReadWarning)
            self.reader.roughMatch("nebula", 5)
        return database

    @contextmanager
    def assert_only_updated(self, identities, *, removed=()):
        repository = self.reader.repository
        index = repository._search_index
        previous_ids = set(index._documents)
        retained = {identity: document for identity, document in index._documents.items() if identity not in identities and identity not in removed}
        with patch.object(repository, "_readChatMessages", wraps=repository._readChatMessages) as read, patch.object(index, "updateDocument", wraps=index.updateDocument) as update, patch.object(index, "removeDocument", wraps=index.removeDocument) as remove:
            yield
        self.assertEqual(sorted(call.args[0].identity for call in read.call_args_list), sorted(identities))
        self.assertEqual(sorted(call.args[0] for call in update.call_args_list), sorted(identities))
        self.assertEqual(sorted(call.args[0] for call in remove.call_args_list), sorted([*identities, *removed]))
        self.assertEqual(set(index._documents), (previous_ids - set(removed)) | set(identities))
        self.assertIs(repository._search_index, index)
        for identity, document in retained.items():
            self.assertIs(index._documents[identity], document)

    def test_appending_to_one_projection_does_not_reindex_other_conversations(self):
        self.build_projection_index()
        self.add_projection(CHAT_ID, "quasar")
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_new_projection_chat_only_indexes_the_new_chat(self):
        self.build_projection_index()
        self.add_projection(NEW_ID, "quasar")
        with warnings.catch_warnings(), self.assert_only_updated([NEW_ID]):
            warnings.simplefilter("ignore", HistoryReadWarning)
            self.assertEqual(self.reader.roughMatch("quasar", 5), [NEW_ID])

    def test_in_place_projection_edits_are_detected_without_count_or_timestamp_changes(self):
        database = self.build_projection_index()
        with closing(sqlite3.connect(database)) as connection, connection:
            item = {"type": "agentMessage", "text": "quasar", "phase": "final_answer"}
            connection.execute("UPDATE thread_items SET item_json = ? WHERE thread_id = ?", (json.dumps(item), CHAT_ID))
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])
            self.assertEqual(self.reader.roughMatch("nebula", 5), [])

    def test_final_answer_selection_changes_only_reindex_the_affected_chat(self):
        database = self.build_projection_index()
        self.add_projection(CHAT_ID, "quasar")
        self.reader.roughMatch("quasar", 5)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("INSERT INTO thread_turns VALUES (?, ?, ?)", (CHAT_ID, "turn-1", "item-0"))
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [])
            self.assertEqual(self.reader.roughMatch("nebula", 5), [CHAT_ID])

    def test_unrelated_sqlite_updates_do_not_rebuild_any_index_document(self):
        database = self.build_projection_index()
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.execute("INSERT INTO unrelated VALUES ('changed')")
        with self.assert_only_updated([]):
            self.assertEqual(self.reader.roughMatch("nebula", 5), [CHAT_ID])

    def test_added_jsonl_chat_only_indexes_the_new_chat(self):
        self.reader.roughMatch("表格", 5)
        path = self.home / "sessions" / f"rollout-{NEW_ID}.jsonl"
        records = [{"type": "session_meta", "payload": {"id": NEW_ID}}, self.message("user", "quasar", START)]
        path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        with self.assert_only_updated([NEW_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [NEW_ID])

    def test_appending_jsonl_keeps_unchanged_projection_documents(self):
        self.add_projection(OTHER_ID, "volcano")
        with self.assertWarns(HistoryReadWarning):
            self.reader.roughMatch("表格", 5)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write("\n" + json.dumps(self.message("user", "quasar", START + 1)) + "\n")
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_visible_jsonl_does_not_fingerprint_unused_projection_content(self):
        self.add_projection(CHAT_ID, "nebula")
        with patch.object(self.reader._store, "projection_signature", side_effect=AssertionError("unused projection scan")):
            self.assertEqual(self.reader.roughMatch("表格", 5), [CHAT_ID])

    def test_warm_queries_do_not_read_projection_rows_even_for_fingerprints(self):
        self.build_projection_index()
        with patch.object(self.reader._store, "projection_signature", side_effect=AssertionError("warm fingerprint read")), self.assert_only_updated([]):
            self.assertEqual(self.reader.roughMatch("nebula", 5), [CHAT_ID])
            self.assertEqual(self.reader.roughMatch("volcano", 5), [OTHER_ID])

    def test_metadata_database_change_reuses_unchanged_projection_fingerprints(self):
        self.build_projection_index()
        self.connection.execute("UPDATE threads SET updated_at = ?", (START + 10,))
        self.connection.commit()
        with patch.object(self.reader._store, "projection_signature", side_effect=AssertionError("unchanged database scan")), self.assert_only_updated([]):
            self.assertEqual(self.reader.roughMatch("nebula", 5), [CHAT_ID])

    def test_projection_row_deletion_removes_only_the_affected_chats_old_terms(self):
        database = self.build_projection_index()
        self.add_projection(CHAT_ID, "quasar")
        self.reader.roughMatch("quasar", 5)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM thread_items WHERE thread_id = ? AND item_json LIKE '%nebula%'", (CHAT_ID,))
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("nebula", 5), [])
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_deleted_projection_only_chat_is_removed_without_rebuilding_other_chats(self):
        database = self.build_projection_index()
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("DELETE FROM thread_items WHERE thread_id = ?", (OTHER_ID,))
        with self.assert_only_updated([], removed=[OTHER_ID]):
            self.assertEqual(self.reader.roughMatch("volcano", 5), [])
            self.assertEqual(self.reader.roughMatch("nebula", 5), [CHAT_ID])
        self.assertTrue(all(key[0] != OTHER_ID for key in self.reader.repository._search_projection_signatures))

    def test_wal_projection_changes_only_reindex_the_affected_chat(self):
        database = self.build_projection_index()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            self.reader.roughMatch("nebula", 5)
            item = {"type": "agentMessage", "text": "quasar", "phase": "final_answer"}
            connection.execute("UPDATE thread_items SET item_json = ? WHERE thread_id = ?", (json.dumps(item), CHAT_ID))
            connection.commit()
            self.assertTrue(database.with_name(database.name + "-wal").is_file())
            with self.assert_only_updated([CHAT_ID]):
                self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_concurrent_write_when_discovering_fallback_is_retried_only_for_that_chat(self):
        self.records = self.records[:1]
        self.write()
        self.add_projection(CHAT_ID, "nebula")
        self.add_projection(OTHER_ID, "volcano")
        repository = self.reader.repository
        original = repository._readChatMessages

        def read_then_append(conversation, *, with_attachments):
            result = original(conversation, with_attachments=with_attachments)
            if conversation.identity == CHAT_ID:
                self.add_projection(CHAT_ID, "quasar")
            return result

        with patch.object(repository, "_readChatMessages", side_effect=read_then_append), warnings.catch_warnings():
            warnings.simplefilter("ignore", HistoryReadWarning)
            self.assertEqual(self.reader.roughMatch("quasar", 5), [])
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_concurrent_write_during_incremental_projection_read_is_not_lost(self):
        self.build_projection_index()
        self.add_projection(CHAT_ID, "quasar")
        repository = self.reader.repository
        original = repository._readChatMessages

        def read_then_append(conversation, *, with_attachments):
            result = original(conversation, with_attachments=with_attachments)
            if conversation.identity == CHAT_ID:
                self.add_projection(CHAT_ID, "astronomy")
            return result

        with patch.object(repository, "_readChatMessages", side_effect=read_then_append):
            self.assertEqual(self.reader.roughMatch("astronomy", 5), [])
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("astronomy", 5), [CHAT_ID])

    def test_concurrent_write_during_fingerprinting_is_retried_without_a_full_rebuild(self):
        database = self.build_projection_index()
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        store = self.reader._store
        original = store.projection_signature

        def fingerprint_then_append(identity, path, table):
            signature = original(identity, path, table)
            if identity == CHAT_ID:
                self.add_projection(CHAT_ID, "quasar")
            return signature

        with patch.object(store, "projection_signature", side_effect=fingerprint_then_append):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [])
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("quasar", 5), [CHAT_ID])

    def test_failed_projection_fingerprint_is_retried_only_for_the_affected_chat(self):
        database = self.build_projection_index()
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
        store = self.reader._store
        original = store.projection_signature

        def fail_one(identity, path, table):
            return None if identity == CHAT_ID else original(identity, path, table)

        with patch.object(store, "projection_signature", side_effect=fail_one):
            self.reader.roughMatch("nebula", 5)
        with self.assert_only_updated([CHAT_ID]):
            self.assertEqual(self.reader.roughMatch("nebula", 5), [CHAT_ID])

    def test_fingerprint_reads_do_not_change_history_bytes_or_file_timestamps(self):
        database = self.build_projection_index()
        before = database.read_bytes(), database.stat().st_mtime_ns
        for identity in (CHAT_ID, OTHER_ID):
            first = self.reader._store.projection_signature(identity, database, "thread_items")
            second = self.reader._store.projection_signature(identity, database, "thread_items")
            self.assertIsInstance(first, bytes)
            self.assertEqual(first, second)
        self.assertEqual((database.read_bytes(), database.stat().st_mtime_ns), before)


if __name__ == "__main__":
    unittest.main()
