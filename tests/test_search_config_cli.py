import ast
from contextlib import redirect_stderr, redirect_stdout
import inspect
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_history_interface import C_CodexInterface, C_CommandLine, C_SearchConfig, I_SearchConfig
from codex_history_interface.ConcreteLocal.History import C_PersistentRoughMatchIndex, C_RoughMatchIndex
from codex_history_interface.ConcreteLocal.History.C_IndexFileLock import C_IndexFileLock
from test_export_tex import CHAT_ID, ExportFixture


class SearchConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {"CODEX_HOME": "", "CODEX_HISTORY_INDEX_DIR": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def test_abstract_configuration_requires_concrete_getters(self):
        with self.assertRaises(TypeError):
            I_SearchConfig()
        config = C_SearchConfig(self.root / "indexes")
        self.assertIsInstance(config, I_SearchConfig)
        self.assertEqual(config.getIndexDirectory(), self.root / "indexes")
        self.assertEqual(config.getSchemaVersion(), 1)
        self.assertEqual(config.getSqliteTimeout(), 30.0)
        self.assertEqual(config.getSqliteWriteCacheKiB(), 65536)
        self.assertEqual(config.getLockTimeout(), 30.0)
        self.assertEqual(config.getLockPollInterval(), 0.05)
        self.assertEqual(config.getDefaultMaxCount(), 10)
        self.assertTrue(config.getTokenPattern().search("中文 English"))
        self.assertTrue(config.getCjkPattern().match("中"))
        self.assertIsInstance(config.getCjkRanges(), str)
        self.assertFalse(any(isinstance(member, property) for member in vars(I_SearchConfig).values()))
        self.assertFalse(any(isinstance(member, property) for member in vars(C_SearchConfig).values()))

    def test_legacy_configuration_inherits_the_write_cache_default(self):
        config = C_SearchConfig(self.root / "indexes")
        legacy_type = type("LegacySearchConfig", (I_SearchConfig,), {
            name: getattr(C_SearchConfig, name) for name in I_SearchConfig.__abstractmethods__
        })
        legacy = legacy_type()
        legacy.__dict__.update(vars(config))
        self.assertEqual(legacy.getIndexDirectory(), config.getIndexDirectory())
        self.assertEqual(legacy.getSqliteWriteCacheKiB(), 65536)

    def test_default_index_uses_the_actual_user_home_not_a_windows_template(self):
        home = self.root / "relocated profiles" / "测试用户"
        with patch.object(Path, "home", return_value=home):
            self.assertEqual(C_SearchConfig().getIndexDirectory(), home / ".codex_history_interface" / "indexes")

    def test_environment_and_explicit_index_directories_have_clear_precedence(self):
        with patch.dict(os.environ, {"CODEX_HISTORY_INDEX_DIR": str(self.root / "环境 索引")}):
            self.assertEqual(C_SearchConfig().getIndexDirectory(), self.root / "环境 索引")
            self.assertEqual(C_SearchConfig(self.root / "explicit").getIndexDirectory(), self.root / "explicit")

    def test_index_directory_expands_user_home(self):
        self.assertEqual(C_SearchConfig("~/.codex_history_interface/custom").getIndexDirectory(), (Path.home() / ".codex_history_interface" / "custom").resolve())

    def test_codex_data_follows_a_relocated_user_home(self):
        home = self.root / "custom user home"
        data = home / ".codex"
        data.mkdir(parents=True)
        with patch.object(Path, "home", return_value=home), patch.dict(os.environ, {"USERPROFILE": str(home), "HOME": str(home)}):
            reader = C_CodexInterface()
            self.assertEqual(reader.codex_home, data)
            self.assertEqual(reader.getSearchIndexPath().parent, home / ".codex_history_interface" / "indexes")
            self.assertFalse(reader.getSearchIndexPath().exists())

    def test_codex_home_environment_does_not_relocate_the_default_user_cache(self):
        data = self.root / "data elsewhere"
        data.mkdir()
        home = self.root / "different home"
        with patch.dict(os.environ, {"CODEX_HOME": str(data)}), patch.object(Path, "home", return_value=home):
            reader = C_CodexInterface()
            self.assertEqual(reader.codex_home, data)
            self.assertEqual(reader.getSearchIndexPath().parent, home / ".codex_history_interface" / "indexes")

    def test_explicit_codex_home_overrides_environment(self):
        data = self.root / "explicit data"
        data.mkdir()
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "does not exist")}):
            reader = C_CodexInterface(data, search_config=C_SearchConfig(self.root / "indexes"))
        self.assertEqual(reader.codex_home, data)

    def test_home_environment_fallback_can_be_outside_standard_user_directories(self):
        home = self.root / "portable account"
        data = home / ".codex"
        data.mkdir(parents=True)
        with patch.object(Path, "home", return_value=self.root / "missing primary home"), patch.dict(os.environ, {"USERPROFILE": "", "HOME": str(home)}):
            reader = C_CodexInterface(search_config=C_SearchConfig(self.root / "cache"))
            self.assertEqual(reader.codex_home, data)

    def test_new_components_use_classes_without_module_constants_or_functions(self):
        for component in (I_SearchConfig, C_SearchConfig, C_PersistentRoughMatchIndex, C_RoughMatchIndex, C_IndexFileLock, C_CommandLine):
            with self.subTest(component=component.__name__):
                tree = ast.parse(Path(inspect.getfile(component)).read_text(encoding="utf-8"))
                self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Assign, ast.AnnAssign)) for node in tree.body))
                self.assertFalse(any(isinstance(member, property) for member in vars(component).values()))


class SearchCommandLineTests(ExportFixture, unittest.TestCase):
    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = C_CommandLine.main(["--home", str(self.home), *arguments])
        return code, output.getvalue(), errors.getvalue()

    def test_rough_match_command_prints_complete_ids(self):
        code, output, errors = self.invoke("rough-match", "表格和公式", "--max-count", "5")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output), [CHAT_ID])
        self.assertEqual(errors, "")

    def test_search_alias_and_short_max_count_option(self):
        code, output, errors = self.invoke("search", "最终答案", "-n", "1")
        self.assertEqual((code, json.loads(output), errors), (0, [CHAT_ID], ""))

    def test_max_cnt_option_alias(self):
        code, output, errors = self.invoke("rough-match", "表格", "--max-cnt", "1")
        self.assertEqual((code, json.loads(output), errors), (0, [CHAT_ID], ""))

    def test_zero_count_does_not_create_an_index(self):
        code, output, errors = self.invoke("rough-match", "表格", "--max-count", "0")
        self.assertEqual((code, json.loads(output), errors), (0, [], ""))
        self.assertFalse(self.reader.getSearchIndexPath().exists())

    def test_negative_count_is_a_json_error_on_stderr(self):
        code, output, errors = self.invoke("rough-match", "表格", "--max-count", "-1")
        self.assertEqual(code, 2)
        self.assertEqual(output, "")
        self.assertEqual(json.loads(errors)["error"], "ValueError")
        self.assertFalse(self.reader.getSearchIndexPath().exists())

    def test_empty_query_does_not_create_an_index(self):
        code, output, errors = self.invoke("rough-match", "  ")
        self.assertEqual((code, json.loads(output), errors), (0, [], ""))
        self.assertFalse(self.reader.getSearchIndexPath().exists())

    def test_non_integer_count_is_rejected_by_the_parser(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.invoke("rough-match", "表格", "--max-count", "abc")
        self.assertEqual(error.exception.code, 2)

    def test_index_directory_option_overrides_the_environment(self):
        folder = self.root / "CLI 索引"
        with patch.dict(os.environ, {"CODEX_HISTORY_INDEX_DIR": str(self.root / "unused")}):
            code, output, errors = self.invoke("--index-dir", str(folder), "rough-match", "表格")
        self.assertEqual((code, json.loads(output), errors), (0, [CHAT_ID], ""))
        self.assertEqual(len(list(folder.glob("*.sqlite3"))), 1)
        self.assertFalse((self.root / "unused").exists())

    def test_default_count_comes_from_the_configuration_instance(self):
        config = C_SearchConfig(self.root / "indexes")
        config._default_max_count = 3
        arguments = C_CommandLine(config).getParser().parse_args(["search", "Qt"])
        self.assertEqual(arguments.max_count, 3)

    def test_module_entry_point_supports_unicode_paths_and_queries(self):
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "codex_history_interface", "--home", str(self.home), "--index-dir", str(self.root / "进程 索引"), "search", "表格和公式", "-n", "1"], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [CHAT_ID])
        self.assertEqual(result.stderr, "")

    def test_index_inside_codex_home_is_rejected_without_writing_history(self):
        code, output, errors = self.invoke("--index-dir", str(self.home / "indexes"), "search", "表格")
        self.assertEqual((code, output), (2, ""))
        self.assertEqual(json.loads(errors)["error"], "ValueError")
        self.assertFalse((self.home / "indexes").exists())
