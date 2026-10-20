from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import warnings

import codex_history_interface as package
from codex_history_interface import (
    Abstract,
    ConcreteLocal,
    C_AssetBundle,
    C_CodexInterface,
    C_HistoryRepository,
    C_HistoryStore,
    C_MarkdownLatexRenderer,
    C_RoughMatchIndex,
    C_TexDocumentRenderer,
    C_TexExporter,
    D_ChatExport,
    D_ChatId,
    D_ChatMessage,
    I_CodexInterface,
    I_HistoryRepository,
    I_TexExporter,
    TexExportWarning,
)
from test_export_tex import CHAT_ID, START, ExportFixture


class C_MemoryHistoryRepository(I_HistoryRepository):
    def __init__(self, home: Path) -> None:
        self._home = home
        self.messages: list[D_ChatMessage] = [
            {"role": "user", "content": "用户问题"},
            {"role": "assistant", "content": "最终答复 $x^2$"},
        ]
        self.notices: list[str] = []
        self.export_requests: list[str] = []

    def __bool__(self):
        return False

    @property
    def codex_home(self):
        return self._home

    @property
    def diagnostics(self):
        return tuple(self.notices)

    def getChatList(self):
        return [CHAT_ID]

    def getChatListInTimeRange(self, timestamp1, timestamp2):
        return [CHAT_ID] if any(timestamp1 <= value <= timestamp2 for value in (START + 0.683, START + 20.25)) else []

    def roughMatch(self, content, maxCnt):
        text = self.getChatTitle(CHAT_ID) + " ".join(message["content"] for message in self.messages)
        return [D_ChatId(CHAT_ID)] if maxCnt > 0 and content in text else []

    def getChatTitle(self, chat_id):
        return "内存对话"

    def getChatMessage(self, chat_id):
        return [dict(message) for message in self.messages]

    def getLastMessageTimeStamp(self, chat_id):
        return START + 20.25

    def getCreateTimeStamp(self, chat_id):
        return START + 0.683

    def getChatExport(self, chat_id):
        self.export_requests.append(chat_id)
        return D_ChatExport(
            self.getChatTitle(chat_id), self.getChatMessage(chat_id),
            self.getCreateTimeStamp(chat_id), START + 100.125,
            protected=(self.codex_home,),
        )

    def reportWarning(self, text, *, category=UserWarning):
        if text not in self.notices:
            self.notices.append(text)
            warnings.warn(text, category, stacklevel=2)


class C_RecordingTexExporter(I_TexExporter):
    def __init__(self):
        self.calls = []

    def __bool__(self):
        return False

    def exportToTex(self, repository, chat_id, output_path, *, overwrite=False):
        self.calls.append((repository, chat_id, output_path, overwrite))
        return Path(output_path).resolve()


class ObjectExportTests(unittest.TestCase):
    def test_layered_and_root_imports_are_the_same_types(self):
        exports = {
            "Abstract.I_CodexInterface": I_CodexInterface,
            "Abstract.History.I_HistoryRepository": I_HistoryRepository,
            "Abstract.History.D_ChatMessage": D_ChatMessage,
            "Abstract.History.D_ChatExport": D_ChatExport,
            "Abstract.History.D_ChatId": D_ChatId,
            "Abstract.Export.I_TexExporter": I_TexExporter,
            "ConcreteLocal.C_CodexInterface": C_CodexInterface,
            "ConcreteLocal.History.C_HistoryRepository": C_HistoryRepository,
            "ConcreteLocal.History.C_HistoryStore": C_HistoryStore,
            "ConcreteLocal.History.C_RoughMatchIndex": C_RoughMatchIndex,
            "ConcreteLocal.Export.C_TexExporter": C_TexExporter,
            "ConcreteLocal.Export.C_TexDocumentRenderer": C_TexDocumentRenderer,
            "ConcreteLocal.Export.C_MarkdownLatexRenderer": C_MarkdownLatexRenderer,
            "ConcreteLocal.Export.C_AssetBundle": C_AssetBundle,
        }
        for module_path, expected in exports.items():
            with self.subTest(module_path=module_path):
                module = import_module("codex_history_interface." + module_path)
                self.assertIs(getattr(module, expected.__name__), expected)
                self.assertIs(getattr(package, expected.__name__), expected)

    def test_every_package_exports_existing_unique_names(self):
        modules = [package, Abstract, Abstract.History, Abstract.Export, ConcreteLocal, ConcreteLocal.History, ConcreteLocal.Export]
        for module in modules:
            with self.subTest(module=module.__name__):
                self.assertEqual(len(module.__all__), len(set(module.__all__)))
                for name in module.__all__:
                    self.assertTrue(hasattr(module, name), name)

    def test_parent_packages_reexport_their_component_classes(self):
        self.assertIs(Abstract.I_HistoryRepository, Abstract.History.I_HistoryRepository)
        self.assertIs(Abstract.I_TexExporter, Abstract.Export.I_TexExporter)
        self.assertIs(ConcreteLocal.C_HistoryRepository, ConcreteLocal.History.C_HistoryRepository)
        self.assertIs(ConcreteLocal.C_RoughMatchIndex, ConcreteLocal.History.C_RoughMatchIndex)
        self.assertIs(ConcreteLocal.C_TexExporter, ConcreteLocal.Export.C_TexExporter)

    def test_legacy_imports_keep_type_identity(self):
        from codex_history_interface.client import C_CodexInterface as legacy_client
        from codex_history_interface.interface import D_ChatMessage as legacy_message, I_CodexInterface as legacy_interface
        from codex_history_interface._assets import AssetBundle
        from codex_history_interface._export_tex import MarkdownLatexRenderer
        from codex_history_interface._storage import HistoryStore

        self.assertIs(legacy_client, C_CodexInterface)
        self.assertIs(legacy_interface, I_CodexInterface)
        self.assertIs(legacy_message, D_ChatMessage)
        self.assertIs(AssetBundle, C_AssetBundle)
        self.assertIs(MarkdownLatexRenderer, C_MarkdownLatexRenderer)
        self.assertIs(HistoryStore, C_HistoryStore)

    def test_interfaces_are_abstract(self):
        for interface in (I_CodexInterface, I_HistoryRepository, I_TexExporter):
            with self.subTest(interface=interface.__name__), self.assertRaises(TypeError):
                interface()

    def test_interface_defines_eight_abstract_methods(self):
        self.assertEqual(I_CodexInterface.__abstractmethods__, {
            "getChatList", "getChatListInTimeRange", "roughMatch", "getChatTitle", "getChatMessage", "getLastMessageTimeStamp",
            "getCreateTimeStamp", "exportToTex",
        })


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = C_MemoryHistoryRepository(self.root / "not-created-codex-home")

    def test_injected_falsey_repository_never_opens_local_history(self):
        with patch.object(C_HistoryRepository, "__init__", side_effect=AssertionError("must not open Codex")):
            reader = C_CodexInterface(repository=self.repository)
            self.assertIs(reader.repository, self.repository)
            self.assertEqual(reader.getChatList(), [CHAT_ID])
            self.assertEqual(reader.codex_home, self.repository.codex_home)
        self.assertFalse(self.repository.codex_home.exists())

    def test_facade_delegates_all_read_methods(self):
        reader = C_CodexInterface(repository=self.repository)
        self.assertEqual(reader.getChatList(), self.repository.getChatList())
        for name in ("getChatTitle", "getChatMessage", "getLastMessageTimeStamp", "getCreateTimeStamp"):
            with self.subTest(method=name):
                self.assertEqual(getattr(reader, name)(CHAT_ID), getattr(self.repository, name)(CHAT_ID))

    def test_facade_delegates_time_range_arguments_to_injected_repository(self):
        reader = C_CodexInterface(repository=self.repository)
        with patch.object(self.repository, "getChatListInTimeRange", wraps=self.repository.getChatListInTimeRange) as query:
            self.assertEqual(reader.getChatListInTimeRange(START, START + 1), [CHAT_ID])
            query.assert_called_once_with(START, START + 1)
        self.assertEqual(reader.getChatListInTimeRange(START + 2, START + 3), [])
        self.assertFalse(self.repository.codex_home.exists())

    def test_facade_delegates_rough_match_arguments_without_local_access(self):
        reader = C_CodexInterface(repository=self.repository)
        with patch.object(self.repository, "roughMatch", wraps=self.repository.roughMatch) as query:
            self.assertEqual(reader.roughMatch("问题", 2), [CHAT_ID])
            query.assert_called_once_with("问题", 2)
        self.assertFalse(self.repository.codex_home.exists())

    def test_injected_falsey_exporter_receives_same_repository_and_options(self):
        exporter = C_RecordingTexExporter()
        reader = C_CodexInterface(repository=self.repository, tex_exporter=exporter)
        output = self.root / "custom.tex"
        self.assertIs(reader.tex_exporter, exporter)
        self.assertEqual(reader.exportToTex(CHAT_ID, output, overwrite=True), output.resolve())
        self.assertEqual(exporter.calls, [(self.repository, CHAT_ID, output, True)])
        self.assertFalse(output.exists())

    def test_injected_repository_rejects_ambiguous_storage_arguments(self):
        for options in ({"codex_home": self.root}, {"databases": [self.root / "state.sqlite"]}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "either repository"):
                C_CodexInterface(repository=self.repository, **options)

    def test_standalone_exporter_supports_a_non_local_repository(self):
        output = self.root / "standalone.tex"
        result = C_TexExporter().exportToTex(self.repository, CHAT_ID, output)
        self.assertEqual(result, output.resolve())
        self.assertEqual(self.repository.export_requests, [CHAT_ID])
        source = output.read_text(encoding="utf-8")
        self.assertIn("内存对话", source)
        self.assertIn("用户问题", source)
        self.assertIn("最终答复", source)
        self.assertIn(r"\tableofcontents", source)

    def test_exporter_validates_output_before_reading_history(self):
        with self.assertRaises(ValueError):
            C_TexExporter().exportToTex(self.repository, CHAT_ID, self.root / "wrong.pdf")
        self.assertEqual(self.repository.export_requests, [])

    def test_protected_history_directory_remains_read_only(self):
        output = self.repository.codex_home / "conversation.tex"
        with self.assertRaises(ValueError):
            C_CodexInterface(repository=self.repository).exportToTex(CHAT_ID, output)
        self.assertFalse(output.exists())
        self.assertEqual(self.repository.export_requests, [])

    def test_rendering_warnings_reach_repository_diagnostics(self):
        self.repository.messages[1]["content"] = r"$\input{private}$"
        reader = C_CodexInterface(repository=self.repository)
        with self.assertWarns(TexExportWarning):
            reader.exportToTex(CHAT_ID, self.root / "warning.tex")
        self.assertTrue(reader.diagnostics)
        self.assertEqual(reader.diagnostics, self.repository.diagnostics)

    def test_facade_and_independent_exporter_produce_identical_tex(self):
        exporter = C_TexExporter()
        first = C_CodexInterface(repository=self.repository, tex_exporter=exporter).exportToTex(CHAT_ID, self.root / "first.tex")
        second = exporter.exportToTex(self.repository, CHAT_ID, self.root / "second.tex")
        self.assertEqual(first.read_bytes(), second.read_bytes())


class ComponentReuseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.messages: list[D_ChatMessage] = [{"role": "user", "content": "问题"}, {"role": "assistant", "content": r"$x^2$"}]

    def test_document_renderer_produces_source_without_files_or_history(self):
        source = C_TexDocumentRenderer().render("独立渲染", self.messages, START, START + 1)
        self.assertIn("独立渲染", source)
        self.assertIn(r"\section{第 1 轮}", source)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_reused_document_renderer_starts_with_fresh_markdown_state(self):
        instances = []

        class C_TrackingRenderer(C_MarkdownLatexRenderer):
            def __init__(self, *arguments, **options):
                super().__init__(*arguments, **options)
                instances.append(self)

        renderer = C_TexDocumentRenderer(renderer_type=C_TrackingRenderer)
        first = renderer.render("标题", self.messages, None, None)
        second = renderer.render("标题", self.messages, None, None)
        self.assertEqual(first, second)
        self.assertEqual(len(instances), 2)
        self.assertIsNot(instances[0], instances[1])
        self.assertIsNot(instances[0].references, instances[1].references)

    def test_document_renderer_accepts_a_custom_preamble(self):
        renderer = C_TexDocumentRenderer(preamble="CUSTOM-PREAMBLE\n")
        self.assertTrue(renderer.render("标题", [], None, None).startswith("CUSTOM-PREAMBLE\n"))

    def test_exporter_accepts_a_reusable_document_renderer(self):
        renderer = C_TexDocumentRenderer()
        exporter = C_TexExporter(document_renderer=renderer)
        output = self.root / "document.tex"
        with patch.object(renderer, "render", wraps=renderer.render) as render:
            exporter.exportDocument(output, "标题", self.messages, None, None)
        self.assertEqual(render.call_count, 1)
        self.assertIs(exporter.document_renderer, renderer)

    def test_exporter_accepts_an_asset_bundle_subclass(self):
        bundles = []

        class C_TrackingAssetBundle(C_AssetBundle):
            def publish(self):
                bundles.append(self)
                super().publish()

        exporter = C_TexExporter(asset_bundle_type=C_TrackingAssetBundle)
        exporter.exportDocument(self.root / "assets.tex", "标题", self.messages, None, None)
        self.assertEqual(len(bundles), 1)
        self.assertFalse(bundles[0].staging.exists())

    def test_direct_export_honors_protected_paths(self):
        protected = self.root / "history"
        output = protected / "cannot-replace.tex"
        with self.assertRaisesRegex(ValueError, "protected"):
            C_TexExporter().exportDocument(output, "标题", self.messages, None, None, protected=(protected,))
        self.assertFalse(output.exists())

    def test_direct_export_keeps_suffix_and_overwrite_rules(self):
        exporter = C_TexExporter()
        with self.assertRaises(ValueError):
            exporter.exportDocument(self.root / "wrong.pdf", "标题", [], None, None)
        output = self.root / "existing.tex"
        output.write_text("original", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            exporter.exportDocument(output, "标题", [], None, None)
        self.assertEqual(output.read_text(encoding="utf-8"), "original")
        exporter.exportDocument(output, "新标题", [], None, None, overwrite=True)
        self.assertIn("新标题", output.read_text(encoding="utf-8"))

    def test_one_document_renderer_can_render_independent_documents_concurrently(self):
        renderer = C_TexDocumentRenderer()
        expected = renderer.render("标题", self.messages, None, None)
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda index: renderer.render("标题", self.messages, None, None), range(8)))
        self.assertEqual(results, [expected] * 8)


class LocalRepositoryTests(ExportFixture, unittest.TestCase):
    def test_default_facade_uses_concrete_components(self):
        self.assertIsInstance(self.reader.repository, C_HistoryRepository)
        self.assertIsInstance(self.reader.tex_exporter, C_TexExporter)
        self.assertIsInstance(self.reader._store, C_HistoryStore)

    def test_standalone_repository_matches_the_facade_read_methods(self):
        repository = C_HistoryRepository(self.home)
        self.assertEqual(repository.getChatList(), self.reader.getChatList())
        self.assertEqual(repository.getChatListInTimeRange(START, START + 1), self.reader.getChatListInTimeRange(START, START + 1))
        for name in ("getChatTitle", "getChatMessage", "getCreateTimeStamp", "getLastMessageTimeStamp"):
            with self.subTest(method=name):
                self.assertEqual(getattr(repository, name)(CHAT_ID), getattr(self.reader, name)(CHAT_ID))

    def test_export_snapshot_contains_visible_content_and_file_boundaries(self):
        snapshot = self.reader.repository.getChatExport(CHAT_ID)
        self.assertIsInstance(snapshot, D_ChatExport)
        self.assertEqual(snapshot.title, self.reader.getChatTitle(CHAT_ID))
        self.assertEqual(snapshot.messages, self.reader.getChatMessage(CHAT_ID))
        self.assertEqual(snapshot.created_at, START)
        self.assertEqual(snapshot.modified_at, START + 100)
        self.assertIn(self.home.resolve(), snapshot.protected)
        self.assertIn(self.path.resolve(), snapshot.protected)
        self.assertNotIn("REASONING_SECRET", str(snapshot.messages))

    def test_independent_repository_and_exporter_match_facade_bytes(self):
        expected = self.reader.exportToTex(CHAT_ID, self.output)
        repository = C_HistoryRepository(self.home)
        result = C_TexExporter().exportToTex(repository, CHAT_ID, self.root / "independent.tex")
        self.assertEqual(result.read_bytes(), expected.read_bytes())
