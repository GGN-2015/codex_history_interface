from contextlib import closing
import errno
from importlib import import_module
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from codex_history_interface import C_CodexInterface, C_PersistentRoughMatchIndex, C_SearchConfig, HistoryUnavailableError
from test_export_tex import CHAT_ID, START, ExportFixture


class C_CacheProcessWorker:
    @staticmethod
    def makeState(marker):
        return {"sources": None, "documents": {marker: marker}, "projections": {}, "journals": (), "notices": ()}

    @staticmethod
    def history(reader, index, connection, request):
        repository = reader.repository
        original = index.roughMatch
        with patch.object(repository, "_readChatMessages", wraps=repository._readChatMessages) as reads, patch.object(index, "updateDocument", wraps=index.updateDocument) as updates:
            def query(content, maxCnt):
                identities = original(content, maxCnt)
                if request.get("hold"):
                    connection.send({"status": "held", "ids": identities, "reads": [call.args[0].identity for call in reads.call_args_list], "updates": [call.args[0] for call in updates.call_args_list]})
                    if connection.recv() != "release":
                        raise RuntimeError("Transaction was interrupted")
                return identities

            with patch.object(index, "roughMatch", side_effect=query):
                identities = reader.roughMatch(request["query"], 10)
            return {"ids": identities, "reads": [call.args[0].identity for call in reads.call_args_list], "updates": [call.args[0] for call in updates.call_args_list]}

    @staticmethod
    def run(connection, directory, namespace, options):
        try:
            config = C_SearchConfig(directory)
            config._lock_timeout = options.get("lock_timeout", 10.0)
            reader = C_CodexInterface(options["home"], search_config=config) if "home" in options else None
            index = reader.repository._search_index if reader is not None else C_PersistentRoughMatchIndex(namespace, config=config)
            pause = options.get("pause_recovery")
            if pause:
                original = index._quarantine

                def recover():
                    if pause == "before":
                        connection.send({"status": "recovering"})
                        if connection.recv() != "release":
                            raise RuntimeError("Recovery was interrupted")
                    original()
                    if pause == "after":
                        connection.send({"status": "recovered"})
                        if connection.recv() != "release":
                            raise RuntimeError("Recovery was interrupted")

                index._quarantine = recover
            connection.send({"status": "ready"})
            while True:
                request = connection.recv()
                if request == "quit":
                    break
                connection.send({"status": "attempting"})
                if request["operation"] == "history":
                    result = C_CacheProcessWorker.history(reader, index, connection, request)
                    connection.send({"status": "done", **result})
                    continue
                with index.session():
                    if request["operation"] == "update":
                        index.updateDocument(request["identity"], request["text"], request["text"])
                        if request.get("save_state"):
                            index.saveState(C_CacheProcessWorker.makeState(request["identity"]))
                        result = {"ids": [request["identity"]]}
                    elif request["operation"] == "remove":
                        index.removeDocument(request["identity"])
                        result = {"ids": []}
                    elif request["operation"] == "query":
                        result = {"ids": index.roughMatch(request["query"], 10)}
                    else:
                        result = {"ids": index.getChatList()}
                    if request.get("hold"):
                        connection.send({"status": "held", **result})
                        if connection.recv() != "release":
                            raise RuntimeError("Transaction was interrupted")
                connection.send({"status": "done", **result})
        except EOFError:
            pass
        except BaseException as error:
            connection.send({"status": "error", "type": type(error).__name__, "message": str(error)})
        finally:
            connection.close()


class ProcessCacheTests(ExportFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.directory = self.root / "shared-process-cache"
        self.config = C_SearchConfig(self.directory)
        self.index = C_PersistentRoughMatchIndex("concurrency", config=self.config)
        self.context = multiprocessing.get_context("spawn")

    def receive(self, worker, expected=None, *, timeout=30):
        process, connection = worker
        self.assertTrue(connection.poll(timeout), f"Worker {process.pid} did not respond")
        response = connection.recv()
        if expected is not None:
            self.assertEqual(response["status"], expected, response)
        return response

    def stop(self, worker):
        process, connection = worker
        if process.is_alive():
            try:
                connection.send("quit")
            except (BrokenPipeError, EOFError, OSError):
                pass
            process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(10)
        connection.close()
        process.close()

    def start(self, *, namespace="concurrency", **options):
        connection, child = self.context.Pipe()
        process = self.context.Process(target=C_CacheProcessWorker.run, args=(child, self.directory, namespace, options))
        process.start()
        child.close()
        worker = process, connection
        self.addCleanup(self.stop, worker)
        self.receive(worker, "ready")
        return worker

    def request(self, worker, operation, **arguments):
        worker[1].send({"operation": operation, **arguments})
        self.receive(worker, "attempting")

    def release(self, worker):
        worker[1].send("release")
        return self.receive(worker, "done")

    def kill(self, worker):
        worker[0].terminate()
        worker[0].join(10)
        self.assertFalse(worker[0].is_alive())

    def test_four_processes_share_the_first_build_without_lost_updates(self):
        workers = [self.start() for unused in range(4)]
        for number, worker in enumerate(workers):
            self.request(worker, "update", identity=f"chat-{number}", text=f"topic{number}")
        for worker in workers:
            self.receive(worker, "done")
        self.assertEqual(set(self.index.getChatList()), {f"chat-{number}" for number in range(4)})
        with closing(sqlite3.connect(self.index.getIndexPath())) as connection:
            self.assertEqual(connection.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0], "4")

    def test_simultaneous_invalid_cache_recovery_cannot_replace_a_repaired_cache(self):
        self.directory.mkdir()
        self.index.getIndexPath().write_bytes(b"corrupt cache")
        first = self.start(pause_recovery="before")
        second = self.start()
        self.request(first, "update", identity="chat-a", text="alpha")
        self.receive(first, "recovering")
        self.request(second, "update", identity="chat-b", text="beta")
        self.assertFalse(second[1].poll(0.2), "Another process entered cache recovery while the first held the lock")
        self.release(first)
        self.receive(second, "done")
        self.assertEqual(set(self.index.getChatList()), {"chat-a", "chat-b"})
        self.assertEqual(len(list(self.directory.glob(self.index.getIndexPath().name + ".invalid-*"))), 1)

    def test_reader_waits_for_the_writer_and_sees_only_the_committed_document(self):
        self.index.updateDocument("baseline", "initial")
        writer = self.start()
        reader = self.start()
        self.request(writer, "update", identity="new-chat", text="nebula", hold=True)
        self.receive(writer, "held")
        self.request(reader, "query", query="nebula")
        self.assertFalse(reader[1].poll(0.2))
        self.release(writer)
        self.assertEqual(self.receive(reader, "done")["ids"], ["new-chat"])

    def test_killed_writer_rolls_back_documents_and_metadata_and_releases_the_lock(self):
        state = C_CacheProcessWorker.makeState("baseline")
        with self.index.session():
            self.index.updateDocument("baseline", "initial")
            self.index.saveState(state)
        worker = self.start()
        self.request(worker, "update", identity="uncommitted", text="nebula", hold=True, save_state=True)
        self.receive(worker, "held")
        self.kill(worker)
        with self.index.session():
            self.assertEqual(self.index.getChatList(), ["baseline"])
            self.assertEqual(self.index.getState(), state)
            self.assertEqual(self.index.roughMatch("nebula", 10), [])
        self.index.updateDocument("after-crash", "volcano")
        self.assertEqual(self.index.roughMatch("volcano", 10), ["after-crash"])

    def test_killed_initial_builder_does_not_leave_a_permanent_lock_or_partial_index(self):
        worker = self.start()
        self.request(worker, "update", identity="uncommitted", text="nebula", hold=True)
        self.receive(worker, "held")
        self.kill(worker)
        self.assertEqual(self.index.getChatList(), [])
        self.index.updateDocument("committed", "volcano")
        self.assertEqual(self.index.roughMatch("volcano", 10), ["committed"])

    def test_killed_recovery_process_can_be_replaced_by_another_process(self):
        self.directory.mkdir()
        self.index.getIndexPath().write_bytes(b"corrupt cache")
        worker = self.start(pause_recovery="after")
        self.request(worker, "update", identity="never-committed", text="nebula")
        self.receive(worker, "recovered")
        successor = self.start()
        self.request(successor, "update", identity="successor", text="volcano")
        self.assertFalse(successor[1].poll(0.2))
        self.kill(worker)
        self.receive(successor, "done")
        self.assertEqual(self.index.getChatList(), ["successor"])

    def test_lock_timeout_does_not_quarantine_or_modify_a_healthy_cache(self):
        self.index.updateDocument("baseline", "initial")
        before = self.index.getIndexPath().read_bytes()
        writer = self.start()
        contender = self.start(lock_timeout=0.2)
        self.request(writer, "list", hold=True)
        self.receive(writer, "held")
        started = monotonic()
        self.request(contender, "update", identity="timeout", text="nebula")
        response = self.receive(contender, "error")
        self.assertEqual(response["type"], "HistoryUnavailableError")
        self.assertIn("Timed out", response["message"])
        self.assertGreaterEqual(monotonic() - started, 0.15)
        self.release(writer)
        self.assertEqual(self.index.getIndexPath().read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.invalid-*")), [])

    def test_lock_file_is_stable_across_transactions_and_cache_recovery(self):
        self.index.updateDocument("baseline", "initial")
        lock = self.index.getLockPath()
        before = lock.stat().st_dev, lock.stat().st_ino, lock.read_bytes()
        self.index.getIndexPath().write_bytes(b"corrupt cache")
        self.index.updateDocument("rebuilt", "nebula")
        self.assertEqual((lock.stat().st_dev, lock.stat().st_ino, lock.read_bytes()), before)

    def test_history_update_is_indexed_once_when_two_processes_compete(self):
        reader = C_CodexInterface(self.home, search_config=self.config)
        reader.roughMatch("表格", 5)
        writer = self.start(home=str(self.home))
        follower = self.start(home=str(self.home))
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.message("user", "quasar", START + 30)) + "\n")
        self.request(writer, "history", query="quasar", hold=True)
        first = self.receive(writer, "held")
        self.request(follower, "history", query="quasar")
        self.assertFalse(follower[1].poll(0.2))
        self.release(writer)
        second = self.receive(follower, "done")
        self.assertEqual(first["ids"], [CHAT_ID])
        self.assertEqual(first["reads"], [CHAT_ID])
        self.assertEqual(first["updates"], [CHAT_ID])
        self.assertEqual(second["ids"], [CHAT_ID])
        self.assertEqual(second["reads"], [])
        self.assertEqual(second["updates"], [])

    def test_live_process_refreshes_its_in_memory_generation_after_another_commit(self):
        self.index.updateDocument("baseline", "initial")
        reader = self.start()
        self.request(reader, "list")
        self.assertEqual(self.receive(reader, "done")["ids"], ["baseline"])
        self.index.updateDocument("new-chat", "nebula")
        self.request(reader, "query", query="nebula")
        self.assertEqual(self.receive(reader, "done")["ids"], ["new-chat"])

    def test_missing_database_sidecars_are_preserved_before_a_new_database_is_created(self):
        self.directory.mkdir()
        for suffix in ("-journal", "-wal", "-shm"):
            Path(str(self.index.getIndexPath()) + suffix).write_bytes(b"orphaned component")
        self.index.updateDocument("rebuilt", "nebula")
        self.assertEqual(self.index.getChatList(), ["rebuilt"])
        for suffix in ("-journal", "-wal", "-shm"):
            backups = list(self.directory.glob(self.index.getIndexPath().name + ".invalid-*" + suffix))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"orphaned component")

    def test_existing_cache_without_identity_is_upgraded_without_retokenization(self):
        self.reader.roughMatch("表格", 5)
        with closing(sqlite3.connect(self.reader.getSearchIndexPath())) as connection, connection:
            connection.execute("DELETE FROM metadata WHERE key='identity'")
        reader = C_CodexInterface(self.home)
        with patch.object(reader.repository, "_readChatMessages", side_effect=AssertionError("unnecessary reindex")):
            self.assertEqual(reader.roughMatch("表格", 5), [CHAT_ID])

    def test_cache_identity_prevents_stale_memory_when_file_identifier_and_counter_are_reused(self):
        self.index.updateDocument("old-chat", "alpha")
        identity = self.index._generation[0]
        self.index.getIndexPath().unlink()
        replacement = C_PersistentRoughMatchIndex("concurrency", config=self.config)
        replacement.updateDocument("new-chat", "nebula")
        self.index._generation = identity, replacement._generation[1], replacement._generation[2]
        self.assertEqual(self.index.getChatList(), ["new-chat"])

    def test_inherited_index_is_rejected_before_acquiring_a_thread_lock(self):
        self.index._process_id = os.getpid() + 1
        with patch.object(self.index, "_lock") as lock, self.assertRaises(HistoryUnavailableError):
            self.index.getChatList()
        lock.__enter__.assert_not_called()

    def test_inherited_history_reader_is_rejected_before_acquiring_its_repository_lock(self):
        self.reader.repository._search_index._process_id = os.getpid() + 1
        with patch.object(self.reader.repository, "_search_lock") as lock, self.assertRaises(HistoryUnavailableError):
            self.reader.roughMatch("表格", 5)
        lock.__enter__.assert_not_called()

    def test_invalid_lock_settings_are_rejected_without_waiting(self):
        for field, value in (("_lock_timeout", -1), ("_lock_timeout", float("nan")), ("_lock_poll_interval", 0), ("_lock_poll_interval", float("inf"))):
            with self.subTest(field=field, value=value):
                config = C_SearchConfig(self.directory)
                setattr(config, field, value)
                index = C_PersistentRoughMatchIndex("invalid", config=config)
                with self.assertRaises(ValueError):
                    index.getChatList()
        self.assertFalse(self.directory.exists())

    def test_non_contention_lock_error_is_reported_and_the_descriptor_is_closed(self):
        with patch.object(self.index._file_lock, "_tryLock", side_effect=OSError(errno.EBADF, "bad descriptor")), self.assertRaises(HistoryUnavailableError):
            self.index.getChatList()
        self.assertEqual(self.index.getChatList(), [])

    def test_waiting_reader_recovers_the_last_commit_after_a_writer_is_killed(self):
        self.index.updateDocument("baseline", "initial")
        writer = self.start()
        reader = self.start()
        self.request(writer, "update", identity="uncommitted", text="nebula", hold=True)
        self.receive(writer, "held")
        self.request(reader, "query", query="nebula")
        self.assertFalse(reader[1].poll(0.2))
        self.kill(writer)
        self.assertEqual(self.receive(reader, "done")["ids"], [])
        self.assertEqual(self.index.getChatList(), ["baseline"])

    def test_four_processes_recover_one_invalid_cache_without_losing_any_update(self):
        self.directory.mkdir()
        self.index.getIndexPath().write_bytes(b"corrupt cache")
        workers = [self.start() for unused in range(4)]
        for number, worker in enumerate(workers):
            self.request(worker, "update", identity=f"chat-{number}", text=f"topic{number}")
        for worker in workers:
            self.receive(worker, "done")
        self.assertEqual(set(self.index.getChatList()), {f"chat-{number}" for number in range(4)})
        self.assertEqual(len(list(self.directory.glob(self.index.getIndexPath().name + ".invalid-*"))), 1)

    def test_different_index_namespaces_do_not_share_a_global_process_lock(self):
        holder = self.start()
        independent = self.start(namespace="another-index")
        self.request(holder, "update", identity="held", text="nebula", hold=True)
        self.receive(holder, "held")
        self.request(independent, "update", identity="independent", text="volcano")
        self.receive(independent, "done")
        self.release(holder)
        other = C_PersistentRoughMatchIndex("another-index", config=self.config)
        self.assertEqual(other.getChatList(), ["independent"])
        self.assertEqual(self.index.getChatList(), ["held"])

    def test_concurrent_reader_cannot_observe_postings_from_a_removed_chat(self):
        self.index.updateDocument("to-remove", "nebula")
        writer = self.start()
        reader = self.start()
        self.request(writer, "remove", identity="to-remove", hold=True)
        self.receive(writer, "held")
        self.request(reader, "query", query="nebula")
        self.assertFalse(reader[1].poll(0.2))
        self.release(writer)
        self.assertEqual(self.receive(reader, "done")["ids"], [])

    def test_four_long_lived_processes_commit_forty_updates_without_lost_documents(self):
        workers = [self.start() for unused in range(4)]
        expected = set()
        for number, worker in enumerate(workers):
            for offset in range(10):
                identity = f"chat-{number}-{offset}"
                expected.add(identity)
                worker[1].send({"operation": "update", "identity": identity, "text": identity})
        for worker in workers:
            for unused in range(10):
                self.receive(worker, "attempting")
                self.receive(worker, "done")
        self.assertEqual(set(self.index.getChatList()), expected)
        with closing(sqlite3.connect(self.index.getIndexPath())) as connection:
            self.assertEqual(connection.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0], "40")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_competing_updates_of_the_same_chat_do_not_leave_mixed_postings(self):
        workers = [self.start() for unused in range(4)]
        words = ("aaaa", "bbbb", "cccc", "dddd")
        for word, worker in zip(words, workers):
            self.request(worker, "update", identity="same-chat", text=word)
        for worker in workers:
            self.receive(worker, "done")
        with closing(sqlite3.connect(self.index.getIndexPath())) as connection:
            title = connection.execute("SELECT title FROM documents WHERE chat_id='same-chat'").fetchone()[0]
        self.assertEqual(self.index.getChatList(), ["same-chat"])
        for word in words:
            self.assertEqual(self.index.roughMatch(word, 10), ["same-chat"] if word == title else [])

    def test_codex_home_index_directory_is_rejected_even_when_the_database_resolves_elsewhere(self):
        expected = self.home / self.reader.getSearchIndexPath().name
        original = Path.resolve

        def resolve(path, *arguments, **keywords):
            return self.root / "outside.sqlite3" if path == expected else original(path, *arguments, **keywords)

        with patch.object(Path, "resolve", new=resolve), self.assertRaises(ValueError):
            C_CodexInterface(self.home, search_config=C_SearchConfig(self.home))

    def test_lock_path_resolving_into_history_is_rejected_before_any_file_is_opened(self):
        reader = C_CodexInterface(self.home, search_config=self.config)
        expected = reader.repository._search_index.getLockPath()
        original = Path.resolve

        def resolve(path, *arguments, **keywords):
            return self.path if path == expected else original(path, *arguments, **keywords)

        with patch.object(Path, "resolve", new=resolve), self.assertRaises(ValueError):
            C_CodexInterface(self.home, search_config=self.config)
        self.assertFalse(self.directory.exists())

    def test_posix_lock_adapter_uses_exclusive_nonblocking_flock_and_explicit_unlock(self):
        module = import_module("codex_history_interface.ConcreteLocal.History.C_IndexFileLock")
        implementation = SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4, LOCK_UN=8)
        with patch.object(module, "os", SimpleNamespace(name="posix")), patch.dict(sys.modules, {"fcntl": implementation}):
            module.C_IndexFileLock._tryLock(23)
            module.C_IndexFileLock._unlock(23)
        self.assertEqual(implementation.flock.call_args_list, [call(23, 6), call(23, 8)])
