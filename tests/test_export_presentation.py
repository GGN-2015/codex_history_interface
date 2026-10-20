from datetime import datetime, timezone
import unittest

from codex_history_interface._export_tex import local_time_text, render_document
from codex_history_interface._tex_math import check_document
from test_export_tex import CHAT_ID, START, ExportFixture


class ExportTimePresentationTests(unittest.TestCase):
    def test_fractional_times_display_seconds_without_rounding(self):
        for timestamp in (START + 0.683, START + 59.999999, 0.25):
            with self.subTest(timestamp=timestamp):
                expected = datetime.fromtimestamp(timestamp, timezone.utc).astimezone().replace(microsecond=0)
                actual = local_time_text(timestamp)
                self.assertEqual(actual, expected.isoformat(sep=" "))
                self.assertNotRegex(actual, r"\d{2}:\d{2}:\d{2}\.\d+")
                self.assertEqual(datetime.fromisoformat(actual).utcoffset(), expected.utcoffset())

    def test_both_subtitle_times_are_formatted_to_seconds(self):
        source = render_document("时间测试", [], START + 0.683, START + 20.703)
        self.assertIn("创建时间：" + local_time_text(START), source)
        self.assertIn("最后修改时间：" + local_time_text(START + 20), source)
        self.assertNotIn(".683", source)
        self.assertNotIn(".703", source)

    def test_missing_time_still_has_an_explicit_label(self):
        self.assertEqual(local_time_text(None), "未知（本地历史未保存）")


class UserLabelPresentationTests(unittest.TestCase):
    def document(self, content, *, role="user"):
        return render_document("中文提示", [{"role": role, "content": content}], None, None)

    def test_user_wrapper_headings_default_to_chinese(self):
        source = self.document("## Files mentioned by the user:\n\nreport.pdf\n\n## My request for Codex:\n\n请解释。")
        self.assertIn(r"\subsubsection*{用户提及的文件：}", source)
        self.assertIn(r"\subsubsection*{用户请求：}", source)
        self.assertNotIn("Files mentioned by the user", source)
        self.assertNotIn("My request for Codex", source)
        check_document(source)

    def test_standalone_paragraph_labels_are_localized(self):
        source = self.document("Files mentioned by the user:\n\nreport.pdf\n\nMy request for Codex:\n\n请解释。")
        self.assertIn("用户提及的文件：", source)
        self.assertIn("用户请求：", source)

    def test_labels_without_colons_are_localized(self):
        source = self.document("## Files mentioned by the user\n\n## My request for Codex")
        self.assertIn(r"\subsubsection*{用户提及的文件}", source)
        self.assertIn(r"\subsubsection*{用户请求}", source)

    def test_emphasized_labels_retain_emphasis(self):
        source = self.document("**Files mentioned by the user:**\n\n*My request for Codex:*")
        self.assertIn(r"\textbf{用户提及的文件：}", source)
        self.assertIn(r"\emph{用户请求：}", source)

    def test_code_fences_inline_code_and_quoted_labels_remain_literal(self):
        source = self.document("`Files mentioned by the user:`\n\n```text\nMy request for Codex:\n```\n\n> My request for Codex:\n\n- Files mentioned by the user:")
        self.assertNotIn("用户提及的文件", source)
        self.assertNotIn("用户请求", source)
        self.assertIn("My request for Codex:", source)

    def test_prose_with_a_marker_phrase_is_not_translated(self):
        source = self.document("Please preserve the phrase My request for Codex: here.")
        self.assertIn("My request for Codex:", source)
        self.assertNotIn("用户请求", source)

    def test_assistant_content_is_not_translated(self):
        source = self.document("## My request for Codex:\n\nFiles mentioned by the user:", role="assistant")
        self.assertIn("My request for Codex:", source)
        self.assertIn("Files mentioned by the user:", source)


class TableOfContentsTests(unittest.TestCase):
    def test_automatic_section_directory_precedes_the_first_round(self):
        source = render_document("目录测试", [{"role": "user", "content": "第一问"}, {"role": "assistant", "content": "## 正文标题\n\n回答"}, {"role": "user", "content": "第二问"}], None, None)
        self.assertEqual(source.count(r"\tableofcontents"), 1)
        self.assertIn(r"\setcounter{tocdepth}{1}", source)
        self.assertIn(r"\renewcommand{\contentsname}{目录}", source)
        self.assertIn("linktoc=all", source)
        self.assertLess(source.index("最后修改时间："), source.index(r"\tableofcontents"))
        self.assertLess(source.index(r"\tableofcontents"), source.index(r"\section{第 1 轮}"))
        self.assertIn("\\tableofcontents\n\\clearpage\n", source)
        self.assertEqual(source.count(r"\section{"), 2)
        self.assertIn(r"\subsubsection*{正文标题}", source)
        check_document(source)


class PresentationApiTests(ExportFixture, unittest.TestCase):
    def test_export_localization_does_not_modify_returned_history(self):
        original = "## Files mentioned by the user:\n\nreport.pdf\n\n## My request for Codex:\n\n解释。"
        self.records[2] = self.message("user", original, START + 10)
        self.write()
        before = self.reader.getChatMessage(CHAT_ID)
        self.reader.exportToTex(CHAT_ID, self.output)
        after = self.reader.getChatMessage(CHAT_ID)
        self.assertEqual(before, after)
        self.assertEqual(before[0]["content"], original)
        source = self.output.read_text(encoding="utf-8")
        self.assertIn("用户提及的文件：", source)
        self.assertIn("用户请求：", source)

    def test_display_precision_does_not_reduce_timestamp_api_precision(self):
        self.connection.execute("UPDATE threads SET created_at = ?, updated_at = ?", (START + 0.683, START + 100.703))
        self.connection.commit()
        self.records[-1] = self.message("assistant", "回答", START + 20.875)
        self.write()
        self.reader.exportToTex(CHAT_ID, self.output)
        self.assertEqual(self.reader.getCreateTimeStamp(CHAT_ID), START + 0.683)
        self.assertEqual(self.reader.getLastMessageTimeStamp(CHAT_ID), START + 20.875)
        source = self.output.read_text(encoding="utf-8")
        self.assertIn("创建时间：" + local_time_text(START), source)
        self.assertIn("最后修改时间：" + local_time_text(START + 100), source)
        self.assertNotRegex(source, r"\d{2}:\d{2}:\d{2}\.\d+")


if __name__ == "__main__":
    unittest.main()
