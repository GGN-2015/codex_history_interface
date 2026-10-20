import unittest

from codex_history_interface import TexExportError, TexExportWarning
from codex_history_interface._export_tex import MarkdownLatexRenderer, PREAMBLE, math_source, render_document
from codex_history_interface._tex_math import check_document


class MathPreservationTests(unittest.TestCase):
    def test_balanced_braces_and_escaped_braces_are_preserved(self):
        formula = r"\left\{\frac{x^{2}}{1+x}\right\}"
        self.assertEqual(math_source(formula), formula)

    def test_unclosed_groups_are_isolated_at_the_fragment_boundary(self):
        for formula in (r"{x", r"x}", r"\frac{1}{x", r"\{x}"):
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_shorthand_arguments_are_not_rewritten(self):
        for formula in (r"\frac12", r"\sqrt x", r"\mathbb R", r"\frac\sqrt12"):
            with self.subTest(formula=formula):
                self.assertEqual(math_source(formula), formula)

    def test_mathtools_commands_do_not_require_a_command_whitelist(self):
        for formula in (r"\mathclap{a+b}", r"A\coloneqq B", r"\prescript{14}{6}{\mathrm C}", r"\smashoperator[r]{\sum_{i=1}^{n}}x_i"):
            with self.subTest(formula=formula):
                self.assertEqual(math_source(formula), formula)

    def test_ams_commands_do_not_require_a_command_whitelist(self):
        for formula in (r"\varinjlim_n A_n", r"A\xrightarrow[\text{下标}]{n\to\infty}B", r"\sideset{_a^b}{_c^d}\sum"):
            with self.subTest(formula=formula):
                self.assertEqual(math_source(formula), formula)

    def test_custom_mathematical_command_names_are_preserved(self):
        formula = r"\someFutureMathMacro*[2]{x}"
        self.assertEqual(math_source(formula), formula)

    def test_spacing_and_line_breaks_are_preserved(self):
        formula = "  x_1  +\t\\frac12\n + y  "
        self.assertEqual(math_source(formula), formula)

    def test_comments_are_retained_instead_of_reinterpreted(self):
        formula = "x % comment with } and \\input{secret}\n+y"
        self.assertEqual(math_source(formula), formula)

    def test_a_final_comment_gets_a_terminating_newline(self):
        formula = "x % retained comment"
        self.assertEqual(math_source(formula), formula + "\n")

    def test_escaped_percent_is_not_treated_as_a_comment(self):
        self.assertEqual(math_source(r"50\%+x"), r"50\%+x")

    def test_comments_do_not_activate_character_code_escapes(self):
        self.assertEqual(math_source("x % ^^69 is literal here\n+y"), "x % ^^69 is literal here\n+y")

    def test_mathematical_environments_are_balanced_without_argument_interpretation(self):
        formula = r"\begin{array}[t]{>{\displaystyle}r|c}a&b\\c&d\end{array}"
        self.assertEqual(math_source(formula), formula)

    def test_mismatched_environments_are_isolated(self):
        for formula in (r"\begin{matrix}x\end{pmatrix}", r"\begin{matrix}x", r"\end{matrix}"):
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_new_mathematical_environments_are_not_rejected_by_a_whitelist(self):
        formula = r"\begin{bmatrix*}[r]1&2\\3&4\end{bmatrix*}"
        self.assertEqual(math_source(formula), formula)

    def test_scripts_and_primes_are_not_reparsed(self):
        formula = r"x_i'+y^{\frac12}+\sum\limits_{i=1}^n z_i"
        self.assertEqual(math_source(formula), formula)

    def test_supported_options_and_dimensions_are_not_rewritten(self):
        formula = r"\cfrac[l]{1}{x}+\sqrt[3]{y}+\hspace{1em}\rule[1pt]{2pt}{3pt}"
        self.assertEqual(math_source(formula), formula)

    def test_generalized_fraction_is_preserved(self):
        formula = r"\genfrac{(}{)}{0pt}{0}{a}{b}"
        self.assertEqual(math_source(formula), formula)

    def test_boxes_underlines_accents_and_substack_are_preserved(self):
        formula = r"\boxed{\underline{x}}+\underbrace{a+b}_{n}+\sum_{\substack{i=1\\j=2}}^n\widehat{x}"
        self.assertEqual(math_source(formula), formula)

    def test_text_groups_escape_only_literal_special_characters(self):
        source = math_source(r"\text{a_b & c# $5 ^ ~}")
        for fragment in (r"a\_b", r"\&", r"\#", r"\$5", r"\textasciicircum{}", r"\textasciitilde{}"):
            self.assertIn(fragment, source)

    def test_existing_text_escapes_and_nested_groups_are_preserved(self):
        formula = r"\text{a\_b \& {\textbf{说明}} \$5}"
        self.assertEqual(math_source(formula), formula)

    def test_text_unicode_is_not_rewritten_as_math_macros(self):
        self.assertEqual(math_source(r"\text{中文 α ℝ x²} + α"), r"\text{中文 α ℝ x²} + \alpha ")

    def test_ensuremath_restores_math_mode_inside_text(self):
        self.assertEqual(math_source(r"\text{结果 \ensuremath{α+ℝ}}"), r"\text{结果 \ensuremath{\alpha +\mathbb{R}}}")

    def test_color_boxes_with_valid_tex_text_are_preserved(self):
        formula = r"\textcolor{red!50!blue}{x}+\colorbox{yellow}{a\_b}+\fcolorbox{blue}{white}{ok}"
        self.assertEqual(math_source(formula), formula)

    def test_equation_tags_and_intertext_are_preserved(self):
        formula = r"\begin{align}a&=b\tag{A}\\\intertext{说明}c&=d\end{align}"
        self.assertEqual(math_source(formula), formula)

    def test_balanced_formula_depth_is_not_limited_by_a_recursive_math_parser(self):
        formula = "{" * 1000 + "x" + "}" * 1000
        self.assertEqual(math_source(formula), formula)

    def test_missing_arguments_are_not_guessed_or_repaired(self):
        self.assertEqual(math_source(r"\frac{1}"), r"\frac{1}")

    def test_nested_delimiters_and_trailing_backslashes_cannot_escape_the_wrapper(self):
        for formula in ("x\\", "x$y$", r"x\[y\]"):
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_dangerous_primitives_and_definitions_remain_isolated(self):
        for formula in (r"\input{secret}", r"\write18{command}", r"\openout1=path", r"\csname input\endcsname", r"\newcommand{\foo}{x}", r"\directlua{code}", r"\begin{document}x\end{document}"):
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_encoded_primitives_are_not_accepted_as_unknown_math_commands(self):
        for formula in (r"\^^69nput{secret}", r"^^5cinput{secret}"):
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_blank_lines_only_receive_the_needed_tex_compatibility_normalization(self):
        for formula in ("x\n\n +y", "x\n\n\n\n +y", "x\n \t\n\n +y", "x\r\n\r\n +y"):
            with self.subTest(formula=formula):
                self.assertEqual(math_source(formula), "x\n +y")


class StaticDocumentTests(unittest.TestCase):
    def document(self, answer):
        return render_document("静态检查", [{"role": "user", "content": "问题"}, {"role": "assistant", "content": answer}], None, None)

    def test_renderer_output_has_balanced_groups_and_environments(self):
        source = self.document("| 项目 | 值 |\n| --- | --- |\n| **中文** | $\\boxed{x_1}$ |\n\n```python\na_b = {1: 2}\n```\n\n<u>下划线</u>")
        check_document(source)

    def test_malformed_generated_documents_are_rejected(self):
        for body in ("}", "{", r"\begin{quote}\end{itemize}", r"\(x\]", r"\[x", r"\(\(x\)\)"):
            with self.subTest(body=body), self.assertRaises(TexExportError):
                check_document(r"\begin{document}" + body + r"\end{document}")

    def test_equation_references_resolve_across_messages(self):
        source = self.document(r"\[x=1\label{eq:first}\]" + "\n\n" + r"见 $\eqref{eq:first}$。")
        check_document(source)

    def test_duplicate_equation_labels_are_generated_with_unique_targets(self):
        source = self.document(r"\[x\label{same}\]\[y\label{same}\]")
        self.assertIn(r"\label{codex-eq-1}", source)
        self.assertIn(r"\label{codex-eq-2}", source)
        check_document(source)

    def test_missing_equation_references_preserve_the_original_identifier(self):
        with self.assertWarns(TexExportWarning):
            source = self.document(r"$\eqref{missing}$")
        self.assertIn("未解析的公式引用：missing", source)
        self.assertNotIn(r"\eqref{missing}", source)
        check_document(source)

    def test_code_containing_labels_and_tex_remains_literal(self):
        check_document(self.document("```tex\n\\ref{missing}\n\\begin{bad}\n}\n```"))


class FidelityTests(unittest.TestCase):
    def setUp(self):
        self.renderer = MarkdownLatexRenderer()

    def test_inline_underline_and_strikethrough(self):
        source = self.renderer.render("<u>中文 **重点**</u> 和 <ins>新增</ins>，~~删除~~ <del>旧文</del>")
        self.assertIn(r"\uline{中文 \textbf{重点}}", source)
        self.assertIn(r"\uline{新增}", source)
        self.assertIn(r"\sout{删除}", source)
        self.assertIn(r"\sout{旧文}", source)

    def test_subscript_superscript_keyboard_and_highlight(self):
        source = self.renderer.render("H<sub>2</sub>O x<sup>2</sup> <kbd>Ctrl</kbd> <mark>重点</mark>")
        for fragment in (r"\textsubscript{2}", r"\textsuperscript{2}", r"\texttt{Ctrl}", r"\colorbox{CodexHighlight}{重点}"):
            self.assertIn(fragment, source)

    def test_nested_style_tags_are_balanced(self):
        self.assertIn(r"\uline{a\uline{b}c}", self.renderer.render("<u>a<u>b</u>c</u>"))

    def test_html_attributes_and_scripts_are_not_executed(self):
        source = self.renderer.render('<script>alert(1)</script> <u onclick="bad()">内容</u>')
        self.assertIn("<script>", source.replace(r"\allowbreak{}", ""))
        self.assertNotIn(r"\uline{", source)

    def test_style_tags_in_code_are_literal(self):
        source = self.renderer.render("`<u>code</u>`\n\n```html\n<u>literal</u>\n```")
        self.assertNotIn(r"\uline{", source)

    def test_code_blocks_are_breakable_framed_and_shaded(self):
        self.assertIn(r"\newtcolorbox{CodexCodeBlock}{breakable", PREAMBLE)
        self.assertIn("colback=CodexCodeBackground", PREAMBLE)
        self.assertIn(r"\begin{CodexCodeBlock}", self.renderer.render("```python\nprint(1)\n```"))

    def test_math_and_code_table_pipes_do_not_create_extra_cells(self):
        source = self.renderer.render("| 公式 | 代码 |\n| --- | --- |\n| $|x| + \\|y\\|$ | `a|b` |")
        self.assertIn(r"\(|x| + \|y\|\)", source)
        self.assertIn(r"\texttt{a|b}", source)
        self.assertEqual(source.count(" & "), 2)

    def test_table_headers_are_bold_and_repeat_on_each_page(self):
        source = self.renderer.render("| 表头 |\n| --- |\n| 内容 |")
        self.assertIn(r"\textbf{表头}", source)
        self.assertIn(r"\endhead", source)

    def test_display_math_in_heading_becomes_safe_inline_math(self):
        source = self.renderer.render("## 结论 $$\\boxed{x=1}$$")
        self.assertIn(r"\subsubsection*{结论 \(\boxed{x=1}\)}", source)
        self.assertNotIn(r"\[", source)

    def test_html_line_break_in_table_is_preserved(self):
        source = self.renderer.render("| 项目 |\n| --- |\n| 第一行<br>第二行 |")
        self.assertIn("第一行\\newline{}\n第二行", source)

    def test_adjacent_display_formulas_and_trailing_text_are_not_dropped(self):
        source = self.renderer.render(r"\[x=1\]\[y=2\] 结论不能消失")
        self.assertIn("x=1", source)
        self.assertIn("y=2", source)
        self.assertIn("结论不能消失", source)

    def test_standalone_environment_retains_trailing_text(self):
        source = self.renderer.render(r"\begin{align}a&=b\end{align} 不能丢掉的结论")
        self.assertIn(r"\begin{align}", source)
        self.assertIn("不能丢掉的结论", source)

    def test_multiline_environment_is_not_parsed_as_markdown_headings(self):
        source = self.renderer.render("\\begin{aligned}\nx\n=\ny\n\\end{aligned}")
        self.assertIn("x\n=\ny", source)
        self.assertNotIn(r"\subsubsection", source)

    def test_unclosed_math_environment_is_preserved_without_losing_formula(self):
        for markdown in (r"\begin{align}x&=y", r"\begin{matrix}x\end{pmatrix}"):
            with self.subTest(markdown=markdown), self.assertWarns(TexExportWarning):
                source = self.renderer.render(markdown)
            self.assertIn("公式原文", source)
            check_document(r"\begin{document}" + source + r"\end{document}")

    def test_unclosed_double_dollars_are_retained_and_code_remains_literal(self):
        for markdown in ("$$x", "公式 $$x", "$$\nx=1"):
            with self.subTest(markdown=markdown), self.assertWarns(TexExportWarning):
                source = self.renderer.render(markdown)
            self.assertIn("公式原文", source)
        self.assertIn(r"\texttt{\$\$x}", self.renderer.render("`$$x`"))

    def test_double_dollar_math_allows_surrounding_whitespace(self):
        source = self.renderer.render("公式 $$ x=1 $$ 结论")
        self.assertIn("\\[\n x=1 \n\\]", source)
        self.assertIn("结论", source)

    def test_adjacent_dollar_displays_retain_all_formulas_and_text(self):
        source = self.renderer.render("$$x=1$$$$y=2$$ 结论")
        self.assertIn("x=1", source)
        self.assertIn("y=2", source)
        self.assertIn("结论", source)

    def test_multiline_dollar_display_can_end_before_trailing_text(self):
        source = self.renderer.render("$$\nx=1\n$$ 不能消失")
        self.assertIn("\\[\nx=1\n\\]", source)
        self.assertIn("不能消失", source)

    def test_formula_parser_preserves_fenced_content_with_pipes(self):
        source = self.renderer.render("```tex\n$|x|$\n\\begin{notmath}\n```")
        self.assertNotIn(r"\(|x|\)", source)
        self.assertIn("|", source)

    def test_nested_inline_styles_preserve_content_beyond_the_limit(self):
        with self.assertWarns(TexExportWarning):
            source = self.renderer.render("<u>" * 50 + "NEVER_DROP" + "</u>" * 50)
        self.assertIn(r"NEVER\_DROP", source.replace(r"\allowbreak{}", ""))
        check_document(r"\begin{document}" + source + r"\end{document}")

    def test_deep_lists_are_generated_as_safe_indented_paragraphs(self):
        markdown = "\n".join("  " * depth + "- 第" + str(depth) + "项内容" for depth in range(5))
        with self.assertWarns(TexExportWarning):
            source = self.renderer.render(markdown)
        for depth in range(5):
            self.assertEqual(source.count("第" + str(depth) + "项内容"), 1)
        self.assertEqual(source.count(r"\begin{itemize}"), 4)
        self.assertIn(r"\textbullet{}", source)
        check_document(r"\begin{document}" + source + r"\end{document}")


if __name__ == "__main__":
    unittest.main()
