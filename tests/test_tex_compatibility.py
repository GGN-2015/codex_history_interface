import unittest

from codex_history_interface import TexExportError, TexExportWarning
from codex_history_interface._export_tex import MarkdownLatexRenderer, math_source, render_document
from codex_history_interface._tex_math import check_document
from test_export_tex import CHAT_ID, START, ExportFixture


def document(answer, *, warn=None, title="兼容性测试"):
    return render_document(title, [{"role": "user", "content": "问题"}, {"role": "assistant", "content": answer}], None, None, warn=warn)


class CitationMarkerTests(unittest.TestCase):
    def test_single_citation_becomes_a_readable_placeholder(self):
        notices = []
        source = document("参考\ue200cite\ue202turn693562search0\ue201。", warn=notices.append)
        self.assertIn("[cite turn693562search0]", source)
        for marker in "\ue200\ue201\ue202":
            self.assertNotIn(marker, source)
        self.assertEqual(notices, [])
        check_document(source)

    def test_multiple_sources_keep_their_full_identifiers(self):
        source = document("\ue200 cite \ue202 turn0search0 \ue202 turn12view2 \ue201")
        self.assertIn("[cite turn0search0; turn12view2]", source)
        self.assertNotIn(r"\cite{", source)

    def test_markers_are_normalized_in_code_and_titles(self):
        marker = "\ue200cite\ue202turn0search0\ue201"
        source = document("`" + marker + "`\n\n```text\n" + marker + "\n```", title="标题" * 15 + marker)
        plain = source.replace(r"\allowbreak{}", "")
        self.assertIn(r"\texttt{[cite\hspace*{\fontdimen2\font}turn0search0]}", plain)
        self.assertIn("[cite turn0search0]", plain)
        for character in "\ue200\ue201\ue202":
            self.assertNotIn(character, source)
        check_document(source)

    def test_unknown_private_characters_and_marker_types_are_not_stripped(self):
        text = "\ue123 \ue200other\ue202value\ue201"
        self.assertIn(text, document(text))

    def test_citation_identifiers_are_escaped_as_text(self):
        source = document("\ue200cite\ue202turn_id%0\ue201")
        self.assertIn(r"[cite turn\_id\%0]", source)
        check_document(source)


class MathDelimiterTests(unittest.TestCase):
    def test_balanced_nested_and_invisible_delimiters_are_preserved(self):
        formulas = (
            r"\left(\left[x\right]\right)",
            r"\left.\begin{aligned}x&=1\\y&=2\end{aligned}\right\}",
            r"{\left(x\right)}",
            r"\left\{x\middle|x>0\right\}",
            "\\left(x % \\right) is a comment\n\\right)",
            r"\leftarrow x \rightarrow y",
        )
        for formula in formulas:
            with self.subTest(formula=formula):
                self.assertEqual(math_source(formula), formula)

    def test_missing_or_cross_scope_pairs_are_rejected(self):
        formulas = (
            r"\left(x", r"x\right)", r"\left(x{\right)}",
            r"{\left(x}\right)",
            r"\begin{aligned}\left(x\end{aligned}\right)",
            r"\left(\begin{aligned}x\right)\end{aligned}",
        )
        for formula in formulas:
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_unbalanced_formula_is_literal_and_neighbors_still_render(self):
        notices = []
        source = document(r"前式 $x=1$，坏式 $\left(x$，后式 $y=2$。", warn=notices.append)
        self.assertIn(r"\(x=1\)", source)
        self.assertIn(r"\(y=2\)", source)
        self.assertIn(r"\textbackslash{}left(x", source)
        self.assertNotIn(r"\(\left(x\)", source)
        self.assertEqual(len(notices), 1)
        self.assertIn("formula 2", notices[0])
        self.assertIn("missing \\right", notices[0])
        self.assertIn("not executed or guessed", notices[0])
        check_document(source)


class FormulaLocationTests(unittest.TestCase):
    def test_inline_formula_locations_include_message_lines_and_excerpt(self):
        samples = (
            ("开头\n\n正文\n$\\left(x$", "message line 4"),
            ("开头\n\n> 正文\n> $\\left(x$", "message line 4"),
            ("开头\n\n- 正文\n  $\\left(x$", "message line 4"),
            ("开头\n\n<u>正文\n$\\left(x$</u>", "message line 4"),
            ("开头\n\n| 公式 |\n| --- |\n| $\\left(x$ |", "message line 5"),
            ("开头\n\n## 标题 $\\left(x$", "message line 3"),
            ("开头\r\n\r\n正文\r\n$\\left(x$", "message line 4"),
            ("开头\n\n$\\left(\nx$", "message lines 3-4"),
        )
        for markdown, location in samples:
            with self.subTest(markdown=markdown):
                notices = []
                check_document(document(markdown, warn=notices.append))
                self.assertEqual(len(notices), 1)
                self.assertIn("Round 1 assistant reply 1, formula 1", notices[0])
                self.assertIn(location, notices[0])
                self.assertIn("near ", notices[0])
                self.assertIn("left", notices[0])

    def test_block_delimiters_and_environments_have_line_ranges(self):
        samples = (
            ("开头\n\n\\[\nx=1\n\n后续", "message lines 3-4"),
            ("开头\n\n$$\nx=1\n\n后续", "message lines 3-4"),
            ("开头\n\n\\begin{align}\nx&=1\n\n后续", "message lines 3-4"),
            ("开头\n\n\\[\n\\left(x\n\\]", "message lines 3-5"),
            ("开头\n\n\\begin{align}\n\\left(x\n\\end{align}", "message lines 3-5"),
        )
        for markdown, location in samples:
            with self.subTest(markdown=markdown):
                notices = []
                check_document(document(markdown, warn=notices.append))
                self.assertEqual(len(notices), 1)
                self.assertIn(location, notices[0])

    def test_formula_after_a_display_on_the_same_line_keeps_its_location(self):
        for opening in (r"\[x=1\]", r"\begin{align}x&=1\end{align}"):
            with self.subTest(opening=opening):
                notices = []
                document("开头\n\n" + opening + r" $\left(x$", warn=notices.append)
                self.assertIn("formula 2, message line 3", notices[0])

    def test_line_numbers_reset_for_each_message(self):
        notices = []
        render_document("标题", [
            {"role": "user", "content": "第一行\n第二行\n第三行"},
            {"role": "assistant", "content": r"$\left(x$"},
        ], None, None, warn=notices.append)
        self.assertIn("message line 1", notices[0])


class UnresolvedReferenceReportTests(unittest.TestCase):
    def test_report_groups_identifiers_and_preserves_occurrence_locations(self):
        notices = []
        source = document("见 $\\eqref{missing_id}+\\ref{missing_id}$\n\n再见 $\\ref{other}$", warn=notices.append)
        report = source.split(r"\section*{未解析的公式引用清单}", 1)[1]
        self.assertEqual(report.count(r"\item \texttt{missing\_id}"), 1)
        self.assertIn("出现 2 次", report)
        self.assertIn("formula 1, message line 1", report)
        self.assertIn("formula 2, message line 3", report)
        self.assertLess(source.index("未解析的公式引用清单"), source.index(r"\end{document}"))
        self.assertIn(r"未解析的公式引用：missing\_id", source)
        self.assertEqual(len(notices), 2)
        check_document(source)

    def test_forward_resolved_references_do_not_appear_in_the_report(self):
        notices = []
        source = render_document("标题", [
            {"role": "user", "content": r"$\ref{later}+\ref{missing}$"},
            {"role": "assistant", "content": r"\[x=1\label{later}\]"},
        ], None, None, warn=notices.append)
        report = source.split(r"\section*{未解析的公式引用清单}", 1)[1]
        self.assertNotIn("later", report)
        self.assertIn("missing", report)
        self.assertIn(r"\ref{codex-eq-1}", source)
        check_document(source)

    def test_fully_resolved_and_reference_free_documents_have_no_report(self):
        for answer in ("普通正文", r"\[x=1\label{ok}\] $\eqref{ok}$"):
            with self.subTest(answer=answer):
                self.assertNotIn("未解析的公式引用清单", document(answer))

    def test_markdown_fragments_do_not_grow_a_document_appendix(self):
        notices = []
        source = MarkdownLatexRenderer(warn=notices.append).render(r"$\ref{missing}$")
        self.assertIn("未解析的公式引用：missing", source)
        self.assertNotIn("未解析的公式引用清单", source)


class ReadableSourceTests(unittest.TestCase):
    def test_long_cjk_prose_and_punctuation_do_not_gain_forced_breaks(self):
        for text in ("中文自然断行标点，。" * 8, "かなカナ한글漢字，。！？（）" * 8, "𠀀" * 30):
            with self.subTest(text=text):
                source = MarkdownLatexRenderer().render(text)
                self.assertIn(text, source)
                self.assertNotIn(r"\allowbreak{}", source)

    def test_long_latin_identifiers_and_urls_still_wrap(self):
        for text in ("a" * 40, "https://example.com/" + "a" * 40, "`" + "a" * 40 + "`"):
            with self.subTest(text=text):
                self.assertIn(r"\allowbreak{}", MarkdownLatexRenderer().render(text))

    def test_mixed_cjk_and_long_identifiers_keep_cjk_runs_intact(self):
        chinese = "中文，保持标点。" * 6
        source = MarkdownLatexRenderer().render(chinese + "a" * 40 + chinese)
        self.assertEqual(source.count(chinese), 2)
        self.assertEqual(source.count(r"\allowbreak{}"), 39)

    def test_code_blocks_keep_their_existing_wrapping_and_literal_tex(self):
        source = MarkdownLatexRenderer().render("```tex\n\\noindent\\textbf{证明:}\n2\\\\Delta*\\tau(u)\n```")
        plain = source.replace(r"\allowbreak{}", "")
        self.assertIn(r"\begin{CodexCodeBlock}", source)
        self.assertIn(r"\textbackslash{}noindent\textbackslash{}textbf\{证明:\}", plain)
        self.assertIn(r"2\textbackslash{}\textbackslash{}Delta*\textbackslash{}tau(u)", plain)
        self.assertNotIn(r"\noindent\textbf{证明:}", source)
        check_document(r"\begin{document}" + source + r"\end{document}")

    def test_bare_tex_in_prose_is_not_executed(self):
        source = document(r"\noindent\textbf{证明:}")
        self.assertIn(r"\textbackslash{}noindent\textbackslash{}textbf\{证明:\}", source)
        self.assertNotIn(r"\noindent\textbf{证明:}", source)


class ExportPreservationTests(ExportFixture, unittest.TestCase):
    def test_readable_export_does_not_change_history_messages(self):
        self.records[-1] = self.message("assistant", "正文\ue200cite\ue202turn0search0\ue201\n\n$\\ref{missing}$", START + 20)
        self.write()
        original = self.reader.getChatMessage(CHAT_ID)
        with self.assertWarns(TexExportWarning):
            self.reader.exportToTex(CHAT_ID, self.output)
        source = self.output.read_text(encoding="utf-8")
        self.assertIn("[cite turn0search0]", source)
        self.assertIn("未解析的公式引用清单", source)
        self.assertEqual(self.reader.getChatMessage(CHAT_ID), original)
        check_document(source)


if __name__ == "__main__":
    unittest.main()
