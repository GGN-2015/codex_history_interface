from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch
import warnings

from codex_history_interface import TexExportWarning
from codex_history_interface import C_CommandLine
from markdown_it import MarkdownIt

from codex_history_interface._export_tex import MarkdownLatexRenderer, math_source, render_document
from codex_history_interface._tex_math import check_document
from test_export_tex import CHAT_ID, START, ExportFixture


class MathGenerationTests(unittest.TestCase):
    def document(self, answer, **options):
        return render_document("生成测试", [{"role": "user", "content": "问题"}, {"role": "assistant", "content": answer}], None, None, **options)

    def test_unbraced_macro_arguments_are_preserved_instead_of_rewritten(self):
        for source in (r"\frac12", r"\sqrt x", r"\mathbb R", r"x^{\frac12}"):
            with self.subTest(source=source):
                self.assertEqual(math_source(source), source)

    def test_generated_math_is_used_by_the_document_renderer(self):
        source = self.document(r"$\frac12+\sqrt x$")
        self.assertIn(r"\(\frac12+\sqrt x\)", source)
        check_document(source)

    def test_unrecognized_formula_commands_remain_math_without_affecting_neighbors(self):
        notices = []
        source = self.document(r"前式 $x_i^2$，中间 $\unknown{x}$，后式 $\boxed{y}$。" + "\n\n**最终结论**", warn=notices.append)
        self.assertIn(r"\(x_i^2\)", source)
        self.assertIn(r"\(\boxed{y}\)", source)
        self.assertIn(r"\textbf{最终结论}", source)
        self.assertIn(r"\(\unknown{x}\)", source)
        self.assertNotIn("公式原文", source)
        self.assertEqual(notices, [])
        check_document(source)

    def test_literal_fallback_preserves_the_complete_original_formula(self):
        original = r"$$\unknown{a_b}+\frac{1}{x$$"
        with self.assertWarns(TexExportWarning):
            source = self.document(original)
        plain = source.replace(r"\allowbreak{}", "")
        self.assertIn(r"\$\$\textbackslash{}unknown\{a\_b\}+\textbackslash{}frac\{1\}\{x\$\$", plain)
        self.assertNotIn(r"\frac{1}{", source)
        check_document(source)

    def test_unclosed_formula_does_not_consume_following_paragraph(self):
        with self.assertWarns(TexExportWarning):
            source = self.document("\\[x=1\n\n**后续段落**\n\n$y=2$")
        self.assertIn(r"\textbf{后续段落}", source)
        self.assertIn(r"\(y=2\)", source)
        check_document(source)

    def test_unclosed_environment_does_not_consume_following_paragraph(self):
        with self.assertWarns(TexExportWarning):
            source = self.document("\\begin{align}x&=y\n\n**后续段落**")
        self.assertIn(r"\textbf{后续段落}", source)
        check_document(source)

    def test_invalid_formula_in_a_table_uses_inline_fallback(self):
        with self.assertWarns(TexExportWarning):
            source = self.document("| 公式 | 结论 |\n| --- | --- |\n| $\\frac{1}{x$ | **保留** |")
        self.assertIn(r"\begin{longtable}", source)
        self.assertIn(r"\textbf{保留}", source)
        self.assertNotIn(r"\begin{CodexCodeBlock}", source)
        check_document(source)

    def test_invalid_heading_formula_does_not_emit_block_content_in_a_title(self):
        with self.assertWarns(TexExportWarning):
            source = self.document(r"## 标题 $\frac{1}{x$")
        self.assertIn(r"\subsubsection*{标题", source)
        self.assertNotIn(r"\begin{CodexCodeBlock}", source)
        check_document(source)

    def test_display_formulas_inside_emphasis_are_generated_inline(self):
        source = self.document(r"**结论 $$\boxed{x=1}$$**")
        self.assertIn(r"\textbf{结论 \(\boxed{x=1}\)}", source)
        check_document(source)

    def test_unknown_commands_are_preserved_without_false_unsupported_warnings(self):
        notices = []
        source = self.document(r"$\validButUnsupported{x}$", warn=notices.append)
        self.assertEqual(notices, [])
        self.assertIn(r"\(\validButUnsupported{x}\)", source)
        self.assertNotIn("公式原文", source)

    def test_source_problems_generate_balanced_documents_instead_of_exceptions(self):
        samples = (r"$x^^2$", r"$\left(x$", r"$\begin{cases}a&b&c\end{cases}$", r"$\frac{1}$", r"$\input{secret}$", r"\[x}", "$$x", r"\begin{matrix}x\end{pmatrix}", "a\x00b", "a\ud800b", r"$\eqref{missing}$")
        for sample in samples:
            with self.subTest(sample=repr(sample)), warnings.catch_warnings(record=True):
                warnings.simplefilter("always", TexExportWarning)
                check_document(self.document(sample))


class MarkdownMathContractTests(unittest.TestCase):
    def document(self, content, **options):
        return render_document("Markdown 转换", [{"role": "user", "content": content}], None, None, **options)

    def test_the_mature_markdown_parser_is_the_structure_entrypoint(self):
        renderer = MarkdownLatexRenderer()
        self.assertIsInstance(renderer.parser, MarkdownIt)
        with patch.object(renderer.parser, "parse", wraps=renderer.parser.parse) as parse:
            source = renderer.render("> **重点**\n>\n> - 列表 $x_i$\n\n```python\na_b = 1\n```")
        parse.assert_called_once()
        self.assertIn(r"\begin{quote}", source)
        self.assertIn(r"\textbf{重点}", source)
        self.assertIn(r"\begin{itemize}", source)
        self.assertIn(r"\(x_i\)", source)
        self.assertIn(r"\begin{CodexCodeBlock}", source)

    def test_all_supported_delimiters_preserve_mathtools_formula_content(self):
        formula = r"\mathclap{a+b}\coloneqq\prescript{14}{6}{\mathrm C}"
        for opening, closing in (("$", "$"), (r"\(", r"\)"), ("$$", "$$"), (r"\[", r"\]")):
            with self.subTest(opening=opening):
                notices = []
                source = self.document(opening + formula + closing, warn=notices.append)
                self.assertIn(formula, source)
                self.assertEqual(notices, [])
                self.assertNotIn("公式原文", source)
                check_document(source)

    def test_text_code_and_math_have_separate_escape_rules(self):
        source = self.document("正文 a_b，代码 `a_b`，数学 $a_b$。")
        self.assertIn(r"正文 a\_b", source)
        self.assertIn(r"\texttt{a\_b}", source)
        self.assertIn(r"\(a_b\)", source)
        check_document(source)

    def test_unknown_macros_in_headings_and_tables_are_not_plain_text_fallbacks(self):
        notices = []
        source = self.document("## $\\mathclap{x}$\n\n| 公式 | 值 |\n| --- | --- |\n| $A\\coloneqq B$ | $\\varinjlim_n A_n$ |", warn=notices.append)
        self.assertIn(r"\subsubsection*{\(\mathclap{x}\)}", source)
        self.assertIn(r"\(A\coloneqq B\)", source)
        self.assertIn(r"\(\varinjlim_n A_n\)", source)
        self.assertEqual(notices, [])
        check_document(source)

    def test_code_fences_do_not_enter_the_math_preparation_layer(self):
        original = "```tex\n$$\\input{secret} | α_1$$\n```"
        with patch("codex_history_interface.ConcreteLocal.Export.C_MarkdownLatexRenderer.prepare_math", side_effect=AssertionError("code must stay literal")):
            source = self.document(original)
        self.assertIn(r"\begin{CodexCodeBlock}", source)
        self.assertNotIn(r"\input{", source)
        self.assertNotIn(r"\alpha", source)
        check_document(source)

    def test_trailing_comments_cannot_consume_generated_math_closers(self):
        for markdown in ("$x % retained$", r"\(x % retained\)", "$$x % retained$$"):
            with self.subTest(markdown=markdown):
                source = self.document(markdown)
                self.assertIn("% retained\n\\", source)
                check_document(source)

    def test_comments_do_not_register_equation_labels_or_references(self):
        notices = []
        source = self.document("\\[x % \\label{comment} \\eqref{missing}\n+y\\]", warn=notices.append)
        self.assertIn(r"% \label{comment} \eqref{missing}", source)
        self.assertNotIn("codex-eq-", source)
        self.assertEqual(notices, [])
        check_document(source)

    def test_display_equation_in_a_table_has_only_an_inline_outer_wrapper(self):
        source = self.document("| 公式 | 值 |\n| --- | --- |\n| \\[\\begin{equation}x=1\\end{equation}\\] | 是 |")
        self.assertIn(r"\(x=1\)", source)
        self.assertNotIn(r"\begin{equation}", source)
        check_document(source)

    def test_display_alignment_in_emphasis_uses_an_inline_compatible_environment(self):
        source = self.document(r"**\[\begin{align}a&=b\\c&=d\end{align}\]**")
        self.assertIn(r"\textbf{\(\begin{aligned}a&=b\\c&=d\end{aligned}\)}", source)
        self.assertNotIn(r"\begin{align}", source)
        check_document(source)


class ReferenceGenerationTests(unittest.TestCase):
    def document(self, messages):
        return render_document("引用测试", messages, None, None)

    def test_repeated_labels_are_unique_and_references_prefer_the_same_message(self):
        source = self.document([
            {"role": "user", "content": r"\[a=1\label{same}\] 见 $\eqref{same}$"},
            {"role": "assistant", "content": r"\[b=2\label{same}\] 见 $\eqref{same}$"},
        ])
        self.assertIn(r"\label{codex-eq-1}", source)
        self.assertIn(r"\label{codex-eq-2}", source)
        self.assertIn(r"\eqref{codex-eq-1}", source)
        self.assertIn(r"\eqref{codex-eq-2}", source)
        check_document(source)

    def test_forward_references_resolve_across_messages(self):
        source = self.document([
            {"role": "user", "content": r"见 $\eqref{answer}$"},
            {"role": "assistant", "content": r"\[x=1\label{answer}\]"},
        ])
        self.assertIn(r"\eqref{codex-eq-1}", source)
        self.assertNotIn(r"\CodexPendingReference", source)
        check_document(source)

    def test_repeated_label_references_choose_the_nearest_preceding_definition(self):
        source = self.document([{"role": "user", "content": r"\[a\label{r}\] $\ref{r}$ \[b\label{r}\] $\ref{r}$"}])
        self.assertIn(r"\ref{codex-eq-1}", source)
        self.assertIn(r"\ref{codex-eq-2}", source)
        check_document(source)

    def test_missing_reference_retains_identifier_with_safe_text_escaping(self):
        with self.assertWarns(TexExportWarning):
            source = self.document([{"role": "user", "content": r"$\eqref{missing_id}$"}])
        self.assertIn(r"未解析的公式引用：missing\_id", source)
        self.assertNotIn(r"\eqref{missing_id}", source)
        check_document(source)

    def test_label_like_text_in_code_does_not_create_a_target(self):
        with self.assertWarns(TexExportWarning):
            source = self.document([{"role": "user", "content": "`\\label{code}`\n\n$\\ref{code}$"}])
        self.assertNotIn(r"\label{codex-eq-", source)
        self.assertIn("未解析的公式引用：code", source)
        check_document(source)


class LayoutGenerationTests(unittest.TestCase):
    def document(self, body, **options):
        return render_document("标题", [{"role": "user", "content": body}], None, None, **options)

    def test_wide_table_padding_is_generated_proportionally_to_column_count(self):
        columns = 60
        source = self.document("|" + "|".join(["列"] * columns) + "|\n|" + "|".join(["---"] * columns) + "|\n|" + "|".join(["值"] * columns) + "|")
        self.assertIn(r"\setlength{\tabcolsep}{\dimexpr\linewidth/600\relax}", source)
        self.assertIn(r"\ifdim\tabcolsep>6pt", source)
        check_document(source)

    def test_flattened_numbered_list_keeps_its_starting_number(self):
        body = "\n\n".join("   " * depth + "3. 层级" + str(depth) for depth in range(7))
        with self.assertWarns(TexExportWarning):
            source = self.document(body)
        self.assertEqual(source.count(r"\begin{enumerate}"), 4)
        for depth in range(7):
            self.assertIn("层级" + str(depth), source)
        self.assertRegex(source, r"\\hspace\*\{[\d.]+em\}3\. 层级4")
        check_document(source)

    def test_deep_quotes_and_lists_share_a_safe_native_nesting_budget(self):
        body = "> " * 6 + "- " + "内容"
        with self.assertWarns(TexExportWarning):
            source = self.document(body)
        self.assertEqual(source.count(r"\begin{quote}"), 6)
        self.assertNotIn(r"\begin{itemize}", source)
        self.assertIn("内容", source)
        check_document(source)

    def test_excess_quotes_use_grouped_indentation(self):
        with self.assertWarns(TexExportWarning):
            source = self.document("> " * 9 + "内容")
        self.assertEqual(source.count(r"\begin{quote}"), 6)
        self.assertIn(r"\addtolength{\leftskip}{1em}", source)
        self.assertIn("内容", source)
        check_document(source)

    def test_invalid_unicode_in_title_and_messages_uses_visible_markers(self):
        with self.assertWarns(TexExportWarning):
            source = render_document("标题\ud800", [{"role": "user", "content": "内容\x00\x1b"}], None, None)
        self.assertIn("[U+D800]", source)
        self.assertIn("[U+0000][U+001B]", source)
        source.encode("utf-8")
        check_document(source)

    def test_unrepresentable_timestamp_is_displayed_as_unknown(self):
        with self.assertWarns(TexExportWarning):
            source = render_document("标题", [], float("inf"), None)
        self.assertIn("未知（原始时间不可表示）", source)
        check_document(source)


class PackageGenerationTests(ExportFixture, unittest.TestCase):
    def test_cli_exports_mathtools_commands_without_unsupported_warnings(self):
        self.records[-1] = self.message("assistant", r"结果 $A\coloneqq\mathclap{B}$", START + 20)
        self.write()
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = C_CommandLine.main(["--home", str(self.home), "export-tex", CHAT_ID, str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), str(self.output.resolve()))
        self.assertEqual(error.getvalue(), "")
        self.assertIn(r"\(A\coloneqq\mathclap{B}\)", self.output.read_text(encoding="utf-8"))

    def test_document_checker_is_not_a_runtime_export_gate(self):
        with patch("codex_history_interface._tex_math.check_document", side_effect=AssertionError("export must not depend on a post-generation validation gate")):
            self.reader.exportToTex(CHAT_ID, self.output)
        check_document(self.output.read_text(encoding="utf-8"))

    def test_multiple_bad_fragments_do_not_drop_later_rounds(self):
        self.records[-1] = self.message("assistant", r"$\frac{1}{x$", START + 20)
        self.records.extend([self.message("user", "下一轮", START + 30), self.message("assistant", r"正常结果 $\boxed{x=1}$", START + 40)])
        self.write()
        original = self.reader.getChatMessage(CHAT_ID)
        with self.assertWarns(TexExportWarning):
            self.reader.exportToTex(CHAT_ID, self.output)
        source = self.output.read_text(encoding="utf-8")
        self.assertEqual(source.count(r"\section{"), 2)
        self.assertIn("下一轮", source)
        self.assertIn(r"\(\boxed{x=1}\)", source)
        self.assertEqual(self.reader.getChatMessage(CHAT_ID), original)
        check_document(source)

    def test_cli_warnings_go_to_stderr_while_stdout_is_success_json(self):
        self.records[-1] = self.message("assistant", r"$\frac{1}{x$", START + 20)
        self.write()
        output = io.StringIO()
        error = io.StringIO()
        with warnings.catch_warnings(), redirect_stdout(output), redirect_stderr(error):
            warnings.simplefilter("always", TexExportWarning)
            code = C_CommandLine.main(["--home", str(self.home), "export-tex", CHAT_ID, str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), str(self.output.resolve()))
        self.assertIn("TexExportWarning", error.getvalue())
        self.assertTrue(self.reader.getChatList())


if __name__ == "__main__":
    unittest.main()
