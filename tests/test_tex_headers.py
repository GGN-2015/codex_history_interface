import unittest

from codex_history_interface import C_TexDocumentRenderer, TexExportWarning
from codex_history_interface._export_tex import tex_escape
from test_export_tex import CHAT_ID, ExportFixture


class TexHeaderTests(unittest.TestCase):
    def render(self, title):
        return C_TexDocumentRenderer().render(title, [], None, None)

    def assert_header(self, source, title):
        self.assertIn(r"\newcommand{\CodexConversationTitle}{" + tex_escape(title) + "}\n", source)

    def test_short_title_is_the_centered_page_header(self):
        source = self.render("对话标题")
        self.assert_header(source, "对话标题")
        self.assertIn(r"\fancyhead[C]{\normalfont\small\CodexConversationTitle}", source)

    def test_title_of_exactly_40_characters_does_not_gain_an_ellipsis(self):
        title = "题" * 40
        self.assert_header(self.render(title), title)

    def test_long_title_is_truncated_before_tex_escaping(self):
        title = "题" * 39 + "&尾部不要出现在页眉"
        source = self.render(title)
        self.assert_header(source, "题" * 39 + "&...")
        self.assertIn(r"\title{" + tex_escape(title) + "}", source)

    def test_already_truncated_display_title_is_not_truncated_twice(self):
        title = "题" * 40 + "..."
        self.assert_header(self.render(title), title)

    def test_title_length_uses_unicode_codepoints(self):
        title = "𠀀" * 40 + "尾"
        self.assert_header(self.render(title), "𠀀" * 40 + "...")

    def test_title_special_characters_cannot_inject_header_commands(self):
        title = r"标题 & _ % # $ ^ ~ \input{a}"
        source = self.render(title)
        self.assert_header(source, title)
        self.assertNotIn(r"\input{a}", source)

    def test_title_math_is_kept_as_literal_display_title_text(self):
        title = r"公式 $x_i^2$ 与 \alpha"
        self.assert_header(self.render(title), title)

    def test_unsupported_title_characters_use_existing_safe_text_handling(self):
        with self.assertWarns(TexExportWarning):
            source = self.render("标题\x00结尾")
        self.assert_header(source, "标题[U+0000]结尾")
        self.assertNotIn("\x00", source)

    def test_plain_page_style_covers_title_and_contents_pages(self):
        source = self.render("标题")
        self.assertIn(r"\usepackage{fancyhdr}", source)
        self.assertIn(r"\fancypagestyle{plain}{", source)
        self.assertIn(r"\pagestyle{plain}", source)
        self.assertIn(r"\maketitle", source)
        self.assertIn(r"\tableofcontents", source)
        self.assertLess(source.index(r"\newcommand{\CodexConversationTitle}"), source.index(r"\begin{document}"))
        for style in (r"\pagestyle{empty}", r"\thispagestyle{empty}"):
            self.assertNotIn(style, source)

    def test_page_numbers_and_room_for_header_are_preserved(self):
        source = self.render("标题")
        self.assertIn(r"\fancyhf{}", source)
        self.assertIn(r"\fancyfoot[C]{\thepage}", source)
        self.assertIn(r"\setlength{\headheight}{16pt}", source)
        self.assertIn(r"\renewcommand{\headrulewidth}{0.4pt}", source)

    def test_reusable_renderer_does_not_leak_a_previous_documents_header(self):
        renderer = C_TexDocumentRenderer()
        self.assert_header(renderer.render("第一个标题", [], None, None), "第一个标题")
        source = renderer.render("第二个标题", [], None, None)
        self.assert_header(source, "第二个标题")
        self.assertNotIn("第一个标题", source)


class TexHeaderIntegrationTests(ExportFixture, unittest.TestCase):
    def test_export_header_matches_the_display_title(self):
        self.connection.execute("UPDATE threads SET name = ?", ("对话" * 25,))
        self.connection.commit()
        source = self.reader.exportToTex(CHAT_ID, self.output).read_text(encoding="utf-8")
        expected = tex_escape(self.reader.getChatTitle(CHAT_ID))
        self.assertIn(r"\newcommand{\CodexConversationTitle}{" + expected + "}", source)
        self.assertIn(r"\title{" + expected + "}", source)

    def test_missing_display_name_retains_fallback_marker_and_length_rule(self):
        self.connection.execute("UPDATE threads SET name = NULL, title = ?", ("旧标题" * 20,))
        self.connection.commit()
        source = self.reader.exportToTex(CHAT_ID, self.output).read_text(encoding="utf-8")
        title = self.reader.getChatTitle(CHAT_ID)
        self.assertTrue(title.startswith("<TitleMissing>"))
        self.assertEqual(len(title), 43)
        self.assertIn(r"\newcommand{\CodexConversationTitle}{" + tex_escape(title) + "}", source)


if __name__ == "__main__":
    unittest.main()
