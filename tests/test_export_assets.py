import base64
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote
import zlib

from codex_history_interface import C_TexExporter, HistoryReadWarning, TexExportError, TexExportWarning
from codex_history_interface._assets import AssetBundle
from test_export_tex import CHAT_ID, START, ExportFixture


def png_bytes():
    def chunk(kind, content):
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\x00\xff" * 2)) + chunk(b"IEND", b"")


class AssetExportTests(ExportFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.inputs = self.root / "input files"
        self.inputs.mkdir()
        self.records[0]["payload"]["cwd"] = str(self.inputs)

    def file(self, name="reference.pdf", content=b"attachment contents"):
        path = self.inputs / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def export(self, answer, **options):
        self.records[-1] = self.message("assistant", answer, START + 20)
        self.write()
        self.reader.exportToTex(CHAT_ID, self.output, **options)
        return self.output.read_text(encoding="utf-8")

    def assets(self):
        return sorted(self.output.parent.glob("asset-*"))

    def test_local_file_is_copied_beside_tex_with_relative_link(self):
        original = self.file()
        source = self.export("[论文](<" + original.as_posix() + ">)")
        self.assertEqual(len(self.assets()), 1)
        asset = self.assets()[0]
        self.assertEqual(asset.read_bytes(), original.read_bytes())
        self.assertIn(r"\href{run:" + asset.name + "}{论文}", source)
        self.assertNotIn(str(self.inputs), source)
        self.assertNotIn(self.inputs.as_posix(), source)

    def test_local_image_is_copied_and_embedded_by_relative_name(self):
        original = self.file("图像 # %.png", png_bytes())
        source = self.export("![示例](<" + quote(original.as_posix(), safe="/:") + ">)")
        asset = self.assets()[0]
        self.assertIn(r"\includegraphics[", source)
        self.assertIn("{" + asset.name + "}", source)
        self.assertEqual(asset.read_bytes(), png_bytes())
        self.assertRegex(asset.name, r"^asset-[A-Za-z0-9_-]+\.png$")

    def test_relative_source_uses_original_session_working_directory(self):
        self.file("figures/image.png", png_bytes())
        source = self.export("![图](figures/image.png)")
        self.assertIn(r"\includegraphics", source)
        self.assertEqual(len(self.assets()), 1)

    def test_relative_source_can_use_database_working_directory(self):
        self.records[0]["payload"].pop("cwd")
        self.connection.execute("ALTER TABLE threads ADD COLUMN cwd TEXT")
        self.connection.execute("UPDATE threads SET cwd = ?", (str(self.inputs),))
        self.connection.commit()
        self.file()
        source = self.export("[论文](reference.pdf)")
        self.assertIn(r"\href{run:asset-", source)

    def test_ambiguous_relative_files_are_not_guessed(self):
        self.file()
        alternate = self.root / "alternate"
        alternate.mkdir()
        (alternate / "reference.pdf").write_bytes(b"different")
        self.connection.execute("ALTER TABLE threads ADD COLUMN cwd TEXT")
        self.connection.execute("UPDATE threads SET cwd = ?", (str(alternate),))
        self.connection.commit()
        with self.assertWarns(HistoryReadWarning):
            source = self.export("[论文](reference.pdf)")
        self.assertIn("本地附件不可用", source)
        self.assertEqual(self.assets(), [])

    def test_missing_file_is_explicit_and_does_not_link_to_original_path(self):
        missing = self.inputs / "missing.pdf"
        with self.assertWarns(HistoryReadWarning):
            source = self.export("[附件](<" + missing.as_posix() + ">)")
        self.assertIn("本地附件不可用，未复制", source)
        self.assertNotIn(missing.as_posix(), source)
        self.assertEqual(self.assets(), [])
        self.assertTrue(self.reader.diagnostics)

    def test_no_working_directory_does_not_guess_process_cwd(self):
        self.records[0]["payload"].pop("cwd")
        with self.assertWarns(HistoryReadWarning):
            source = self.export("[附件](reference.pdf)")
        self.assertIn("本地附件不可用", source)

    def test_same_file_is_only_copied_once(self):
        original = self.file()
        source = self.export("[甲](<" + original.as_posix() + ">) [乙](<" + original.as_posix() + ">)")
        self.assertEqual(len(self.assets()), 1)
        self.assertEqual(source.count(r"\href{run:"), 2)

    def test_same_basename_different_content_gets_different_names(self):
        first = self.file("first/result.pdf", b"first")
        second = self.file("second/result.pdf", b"second")
        self.export("[甲](<" + first.as_posix() + ">) [乙](<" + second.as_posix() + ">)")
        self.assertEqual({asset.read_bytes() for asset in self.assets()}, {b"first", b"second"})

    def test_existing_identical_copy_is_reused_without_modification(self):
        original = self.file()
        answer = "[附件](<" + original.as_posix() + ">)"
        self.export(answer)
        copied = self.assets()[0]
        modified = copied.stat().st_mtime_ns
        self.export(answer, overwrite=True)
        self.assertEqual(copied.stat().st_mtime_ns, modified)

    def test_existing_different_attachment_is_never_overwritten(self):
        original = self.file()
        answer = "[附件](<" + original.as_posix() + ">)"
        self.export(answer)
        previous = self.output.read_bytes()
        copied = self.assets()[0]
        copied.write_bytes(b"user-edited copy")
        with self.assertRaises(TexExportError):
            self.export(answer, overwrite=True)
        self.assertEqual(copied.read_bytes(), b"user-edited copy")
        self.assertEqual(self.output.read_bytes(), previous)

    def test_invalid_math_does_not_prevent_exporting_tex_and_attachments(self):
        original = self.file()
        with self.assertWarns(TexExportWarning):
            source = self.export("[附件](<" + original.as_posix() + ">)\n\n" + r"$\frac{1}{x$")
        self.assertIn("公式原文", source)
        self.assertTrue(self.output.is_file())
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())

    def test_tex_publication_failure_rolls_back_only_new_attachments(self):
        original = self.file()
        with patch.object(C_TexExporter, "writeDocument", side_effect=OSError("cannot publish")):
            with self.assertRaises(OSError):
                self.export("[附件](<" + original.as_posix() + ">)")
        self.assertEqual(self.assets(), [])
        self.assertTrue(original.is_file())

    def test_sources_remain_byte_identical_and_timestamps_unchanged(self):
        original = self.file()
        before = original.stat()
        expected = original.read_bytes()
        self.export("[附件](<" + original.as_posix() + ">)")
        after = original.stat()
        self.assertEqual((before.st_size, before.st_mtime_ns), (after.st_size, after.st_mtime_ns))
        self.assertEqual(original.read_bytes(), expected)

    def test_publication_uses_staged_copies_not_original_files(self):
        original = self.file()
        staged = []
        publish = AssetBundle.publish

        def inspect_and_publish(bundle):
            staged.extend(bundle.files.values())
            self.assertEqual(len(staged), 1)
            self.assertNotEqual(staged[0], original)
            self.assertEqual(staged[0].read_bytes(), original.read_bytes())
            publish(bundle)

        with patch.object(AssetBundle, "publish", autospec=True, side_effect=inspect_and_publish):
            self.export("[附件](<" + original.as_posix() + ">)")
        self.assertTrue(all(not path.exists() for path in staged))

    def test_file_uri_with_spaces_is_copied(self):
        original = self.file("file with spaces.pdf")
        source = self.export("[文件](" + original.as_uri() + ")")
        self.assertEqual(len(self.assets()), 1)
        self.assertIn(r"\href{run:", source)

    def test_plain_absolute_path_and_file_citation_are_rewritten(self):
        original = self.file()
        answer = "## 文件：" + original.as_posix() + "\n\n"
        answer += ':codex-file-citation{path="' + original.as_posix() + '" purpose="output"}'
        source = self.export(answer)
        self.assertEqual(len(self.assets()), 1)
        self.assertNotIn(":codex-file-citation", source)
        self.assertNotIn(self.inputs.as_posix(), source)
        self.assertEqual(source.count(r"\href{run:"), 2)

    def test_inline_code_file_reference_is_rewritten_but_code_block_is_literal(self):
        original = self.file()
        source = self.export("`" + original.as_posix() + "`\n\n```\n" + original.as_posix() + "\n```")
        self.assertEqual(len(self.assets()), 1)
        self.assertIn(r"\href{run:", source)
        self.assertIn(r"\begin{CodexCodeBlock}", source)

    def test_absolute_file_line_and_column_references_copy_the_base_file(self):
        original = self.file("source.md")
        references = [original.as_posix() + suffix for suffix in (":1", ":12:4", "#L8", "#L8C3", "#L8-L12", "#L8C3-L12C7")]
        source = self.export("\n\n".join("[源文件](<" + reference + ">)" for reference in references))
        self.assertEqual(len(self.assets()), 1)
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())
        self.assertEqual(source.count(r"\href{run:"), len(references))

    def test_relative_file_line_reference_is_not_mistaken_for_a_uri_scheme(self):
        original = self.file("source.md")
        source = self.export("[源文件](source.md:12:4)")
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())
        self.assertIn(r"\href{run:", source)

    def test_encoded_file_line_and_column_suffixes_are_supported(self):
        original = self.file("source with spaces.md")
        reference = quote(original.as_posix(), safe="/") + "%3A12%3A4"
        source = self.export("[源文件](<" + reference + ">)")
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())
        self.assertIn(r"\href{run:", source)

    def test_file_uri_line_and_fragment_references_copy_the_base_file(self):
        original = self.file("source.md")
        source = self.export("[行](" + original.as_uri() + ":12:4)\n\n[锚点](" + original.as_uri() + "#L12C4)")
        self.assertEqual(len(self.assets()), 1)
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())
        self.assertEqual(source.count(r"\href{run:"), 2)

    def test_inline_code_line_reference_keeps_the_label_and_copies_the_file(self):
        original = self.file("source.md")
        source = self.export("`" + original.as_posix() + ":12:4`")
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())
        self.assertIn("source.md:12:4", source)
        self.assertIn(r"\href{run:", source)

    def test_encoded_literal_hash_in_an_existing_filename_is_not_a_line_anchor(self):
        original = self.file("source#L12")
        source = self.export("[文件](<" + quote(original.as_posix(), safe="/:") + ">)")
        self.assertEqual(self.assets()[0].read_bytes(), original.read_bytes())
        self.assertIn(r"\href{run:", source)

    def test_network_line_references_are_rejected_without_probing_the_filesystem(self):
        bundle = AssetBundle(self.root, self.output)
        with patch.object(Path, "is_file", side_effect=AssertionError("must not access network files")), self.assertWarns(HistoryReadWarning):
            self.assertIsNone(bundle.local_path(r"\\server\share\source.md:12:4"))

    def test_webpages_and_remote_images_are_links_not_downloads(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("no downloads")), patch("socket.create_connection", side_effect=AssertionError("no network")):
            source = self.export("[网页](https://example.invalid/p?q=a%20b&n=1#top)\n\n![图像](https://example.invalid/image.png)")
        self.assertIn(r"\href{https://example.invalid/p?q=a\%20b\&n=1\#top}{网页}", source)
        self.assertIn(r"urlcolor=blue", source)
        self.assertNotIn(r"\includegraphics", source)
        self.assertEqual(self.assets(), [])

    def test_url_used_as_label_does_not_create_nested_links(self):
        source = self.export("[https://example.invalid](https://example.invalid)")
        self.assertEqual(source.count(r"\href{"), 1)

    def test_prose_slash_is_not_mistaken_for_a_local_attachment(self):
        source = self.export("commutator /determinant 的关系")
        self.assertIn("/determinant", source)
        self.assertNotIn("附件不可用", source)
        self.assertEqual(self.reader.diagnostics, ())

    def test_unsupported_local_image_format_is_kept_as_clickable_attachment(self):
        original = self.file("diagram.svg", b"<svg/>")
        source = self.export("![图](<" + original.as_posix() + ">)")
        self.assertIn(r"\href{run:", source)
        self.assertNotIn(r"\includegraphics", source)
        self.assertEqual(self.assets()[0].suffix, ".svg")

    def test_embedded_image_is_decoded_to_sibling_file_not_raw_base64(self):
        image = "data:image/png;base64," + base64.b64encode(png_bytes()).decode("ascii")
        source = self.export("![图](" + image + ")")
        self.assertIn(r"\includegraphics", source)
        self.assertNotIn("base64", source)
        self.assertEqual(self.assets()[0].read_bytes(), png_bytes())

    def test_response_attachment_metadata_is_used_only_for_tex_export(self):
        original = self.file("upload.png", png_bytes())
        self.records[2]["payload"]["content"].append({"type": "input_image", "image_url": original.as_uri()})
        self.write()
        self.assertIn("[图片]", self.reader.getChatMessage(CHAT_ID)[0]["content"])
        source = self.export("答案")
        self.assertIn(r"\includegraphics", source)
        self.assertEqual(source.count(r"\section{"), 1)

    def test_event_local_images_are_copied(self):
        original = self.file("upload.png", png_bytes())
        self.records = [self.records[0], {"type": "event_msg", "payload": {"type": "user_message", "message": "看图", "local_images": [str(original)]}}, self.records[-1]]
        source = self.export("回答")
        self.assertIn(r"\includegraphics", source)

    def test_reasoning_and_tool_attachment_references_are_not_copied(self):
        original = self.file("secret.png", png_bytes())
        self.records[3]["payload"]["content"][0]["text"] = "![隐藏](<" + original.as_posix() + ">)"
        self.export("正常答复")
        self.assertEqual(self.assets(), [])

    def test_protected_codex_files_are_not_copied_as_attachments(self):
        secret = self.home / "auth.json"
        secret.write_bytes(b"do not read or copy")
        with self.assertWarns(HistoryReadWarning):
            source = self.export("[附件](<" + secret.as_posix() + ">)")
        self.assertEqual(self.assets(), [])
        self.assertNotIn("do not read or copy", source)

    def test_network_file_reference_is_not_opened(self):
        with self.assertWarns(HistoryReadWarning):
            source = self.export("[文件](file://server/share/private.pdf)")
        self.assertEqual(self.assets(), [])
        self.assertNotIn(r"\href{run:", source)

    def test_assets_from_archived_conversation_are_copied(self):
        original = self.file()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records[-1] = self.message("assistant", "[附件](<" + original.as_posix() + ">)", START + 20)
        self.write()
        archived = self.home / "archived_sessions" / self.path.name
        archived.parent.mkdir()
        self.path.replace(archived)
        self.reader.exportToTex(CHAT_ID, self.output)
        self.assertEqual(len(self.assets()), 1)


class PortableAssetExportTests(ExportFixture, unittest.TestCase):
    def test_relative_images_files_urls_math_survive_removal_and_relocation(self):
        inputs = self.root / "originals"
        inputs.mkdir()
        image = inputs / "image # %.png"
        image.write_bytes(png_bytes())
        attached = inputs / "report.txt"
        attached.write_text("local report", encoding="utf-8")
        answer = "![图像](<" + quote(image.as_posix(), safe="/:") + ">)\n\n"
        answer += "[文件](<" + attached.as_posix() + ">) [网页](https://example.invalid/path?q=a%20b&n=1#top)\n\n"
        answer += "公式：\n\\[\n\\boxed{\\frac{1}{2}+x=3}\n\\]\n"
        self.records[-1] = self.message("assistant", answer, START + 20)
        self.write()
        self.reader.exportToTex(CHAT_ID, self.output)
        source = self.output.read_text(encoding="utf-8")
        image.unlink()
        attached.unlink()
        copies = {path.name: path for path in self.output.parent.glob("asset-*")}
        self.assertEqual(len(copies), 2)
        with tempfile.TemporaryDirectory() as folder:
            moved = Path(folder)
            for filename, original in copies.items():
                shutil.copyfile(original, moved / filename)
            shutil.copyfile(self.output, moved / "conversation.tex")
            moved_source = (moved / "conversation.tex").read_text(encoding="utf-8")
            self.assertEqual(moved_source, source)
            for filename, original in copies.items():
                self.assertIn(filename, moved_source)
                self.assertEqual((moved / filename).read_bytes(), original.read_bytes())
        self.assertNotIn(inputs.as_posix(), source)
        self.assertIn(r"\href{run:", source)
        self.assertIn(r"\href{https:", source)


if __name__ == "__main__":
    unittest.main()
