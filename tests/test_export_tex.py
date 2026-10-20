from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import inspect
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from codex_history_interface import C_CodexInterface, ChatNotFoundError, HistoryUnavailableError, I_CodexInterface, TexExportError, TexExportWarning
from codex_history_interface.__main__ import main
from codex_history_interface._export_tex import MarkdownLatexRenderer, local_time_text, math_source, render_document
from codex_history_interface._tex_math import check_document


CHAT_ID = "11111111-1111-4111-8111-111111111111"
START = 1_700_000_000.0
COMPLEX_ANSWER = r"""### 表格、公式与代码

| 项目 | 数学结果 | 说明 |
| :--- | :---: | ---: |
| 平方 | $x_i^2$ | **中文**、`a_b` |
| 分数 | \(\frac{1}{2}\) | 价格 $5 与 $10 |

\[
\begin{aligned}
f(x)&=\sum_{i=1}^n x_i^2\\
g(x)&=\frac{\sqrt{x}}{1+x}
\end{aligned}
\]

$$\begin{equation}E=mc^2\end{equation}$$

\begin{align}
a&=b+c\\
d&=e-f
\end{align}

Unicode 数学：$α ≤ β + x² + y₁₂$。

```python
def example(value="中文"):
    text = r"\end{CodexCodeBlock}\input{auth.json}"
    print(value, text, "$ & % # _ { } ^ ~ \")

    return value
```

> 引用内容包含 **强调** 与普通的 50% 文本。

3. 第三项
4. 第四项

普通正文中的原始命令 \input{auth.json} 只作为文本显示。
"""


class ExportFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "Codex 数据"
        self.home.mkdir()
        self.database = self.home / "state_9.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT, created_at REAL, created_at_ms REAL, updated_at REAL, updated_at_ms REAL, archived INTEGER)")
        self.connection.execute("INSERT INTO threads VALUES (?, '导出测试', '旧标题', ?, NULL, ?, NULL, 0)", (CHAT_ID, START, START + 100))
        self.connection.commit()
        self.path = self.home / "sessions" / ("rollout-" + CHAT_ID + ".jsonl")
        self.records = [
            {"type": "session_meta", "payload": {"id": CHAT_ID, "timestamp": START}},
            self.message("system", "SYSTEM_SECRET", START + 5),
            self.message("user", "请解释表格和公式", START + 10),
            self.message("assistant", "REASONING_SECRET", START + 500, "analysis"),
            self.message("assistant", "PROGRESS_SECRET", START + 600, "commentary"),
            self.message("assistant", "最终答案", START + 20),
        ]
        self.write()
        self.reader = C_CodexInterface(self.home)
        self.output = self.root / "导出文件 & O'Reilly" / "对话.tex"

    def message(self, role, text, timestamp, phase="final_answer"):
        return {"type": "response_item", "timestamp": timestamp, "payload": {"type": "message", "role": role, "phase": phase, "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}

    def write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in self.records) + "\n", encoding="utf-8")


class ExportTests(ExportFixture, unittest.TestCase):
    def export(self, **options):
        result = self.reader.exportToTex(CHAT_ID, self.output, **options)
        self.assertEqual(result, self.output.resolve())
        return self.output.read_text(encoding="utf-8")

    def test_exports_only_visible_messages_and_no_raw_history(self):
        source = self.export()
        self.assertIn("请解释表格和公式", source)
        self.assertIn("最终答案", source)
        for secret in ("SYSTEM_SECRET", "REASONING_SECRET", "PROGRESS_SECRET", "response_item", "session_meta"):
            self.assertNotIn(secret, source)

    def test_each_user_message_starts_one_section(self):
        self.records.extend([self.message("user", "第二问", START + 30), self.message("assistant", "第二答", START + 40)])
        self.write()
        source = self.export()
        self.assertEqual(source.count(r"\section{"), 2)
        self.assertIn(r"\section{第 1 轮}", source)
        self.assertIn(r"\section{第 2 轮}", source)
        self.assertLess(source.index("第二问"), source.index("第二答"))

    def test_multiple_final_outputs_remain_in_the_same_round(self):
        self.records.append(self.message("assistant", "补充答案", START + 30))
        self.write()
        source = self.export()
        self.assertEqual(source.count(r"\section{"), 1)
        self.assertIn("补充答案", source)

    def test_consecutive_questions_and_missing_reply_are_explicit(self):
        self.records = [self.records[0], self.message("user", "第一问", START + 1), self.message("user", "第二问", START + 2)]
        self.write()
        source = self.export()
        self.assertEqual(source.count(r"\section{"), 2)
        self.assertEqual(source.count("本轮尚无最终答复"), 2)

    def test_orphan_assistant_is_not_assigned_a_fabricated_question(self):
        self.records = [self.records[0], self.message("assistant", "孤立答复", START + 1)]
        self.write()
        source = self.export()
        self.assertIn("本轮未保留用户提问", source)
        self.assertIn("孤立答复", source)

    def test_subtitle_contains_local_creation_and_modification_times(self):
        source = self.export()
        self.assertIn("创建时间：" + local_time_text(START), source)
        self.assertIn("最后修改时间：" + local_time_text(START + 100), source)
        self.assertNotIn("最后修改时间：" + local_time_text(START + 600), source)

    def test_modification_milliseconds_have_priority(self):
        self.connection.execute("UPDATE threads SET updated_at_ms = ?", ((START + 150.125) * 1000,))
        self.connection.commit()
        self.assertIn(local_time_text(START + 150.125), self.export())

    def test_modification_uses_index_then_last_visible_message(self):
        self.connection.execute("UPDATE threads SET updated_at = NULL")
        self.connection.commit()
        index = self.home / "session_index.jsonl"
        index.write_text(json.dumps({"id": CHAT_ID, "updated_at": START + 200}), encoding="utf-8")
        self.assertIn("最后修改时间：" + local_time_text(START + 200), self.export())
        index.unlink()
        self.output.unlink()
        self.assertIn("最后修改时间：" + local_time_text(START + 20), self.export())

    def test_export_title_uses_existing_40_character_rule(self):
        self.connection.execute("UPDATE threads SET name = ?", ("题" * 41,))
        self.connection.commit()
        self.assertIn(r"\title{" + "题" * 40 + "...}", self.export())

    def test_unknown_chat_id_does_not_create_output(self):
        with self.assertRaises(ChatNotFoundError):
            self.reader.exportToTex("unknown", self.output)
        self.assertFalse(self.output.exists())

    def test_empty_visible_history_is_not_exported(self):
        self.records = self.records[:2]
        self.write()
        with self.assertRaises(HistoryUnavailableError):
            self.reader.exportToTex(CHAT_ID, self.output)
        self.assertFalse(self.output.exists())

    def test_requires_tex_extension(self):
        for suffix in (".pdf", ".jsonl", ""):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.reader.exportToTex(CHAT_ID, self.root / ("invalid" + suffix))

    def test_history_directory_cannot_be_used_as_destination(self):
        destination = self.home / "exports" / "unsafe.tex"
        with self.assertRaises(ValueError):
            self.reader.exportToTex(CHAT_ID, destination)
        self.assertFalse(destination.parent.exists())

    def test_export_does_not_change_history_source_bytes(self):
        paths = [self.database, self.path]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.export()
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def test_external_history_journal_with_tex_suffix_is_protected(self):
        destination = self.root / "outside-history.tex"
        self.path.replace(destination)
        self.connection.execute("ALTER TABLE threads ADD COLUMN rollout_path TEXT")
        self.connection.execute("UPDATE threads SET rollout_path = ?", (str(destination),))
        self.connection.commit()
        before = destination.read_bytes()
        with self.assertRaises(ValueError):
            self.reader.exportToTex(CHAT_ID, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), before)

    def test_external_database_with_tex_suffix_is_protected(self):
        destination = self.root / "outside-database.tex"
        destination.write_bytes(self.database.read_bytes())
        reader = C_CodexInterface(self.home, databases=[destination])
        before = destination.read_bytes()
        with self.assertRaises(ValueError):
            reader.exportToTex(CHAT_ID, destination, overwrite=True)
        self.assertEqual(destination.read_bytes(), before)

    def test_default_does_not_overwrite_an_existing_file(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"existing document")
        with self.assertRaises(FileExistsError):
            self.reader.exportToTex(CHAT_ID, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing document")

    def test_overwrite_preserves_invalid_formula_as_literal_source(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"existing document")
        self.records[-1] = self.message("assistant", r"$\frac{1}{x$", START + 20)
        self.write()
        with self.assertWarns(TexExportWarning):
            self.reader.exportToTex(CHAT_ID, self.output, overwrite=True)
        source = self.output.read_text(encoding="utf-8")
        self.assertIn("公式原文", source)
        self.assertNotIn("existing document", source)
        check_document(source)

    def test_successful_overwrite_leaves_only_the_requested_tex(self):
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"existing document")
        self.export(overwrite=True)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_invalid_formula_does_not_prevent_creating_output_document(self):
        self.records[-1] = self.message("assistant", r"$\frac{1}{x$", START + 20)
        self.write()
        with self.assertWarns(TexExportWarning):
            self.reader.exportToTex(CHAT_ID, self.output)
        self.assertTrue(self.output.is_file())
        self.assertIn("formula 1", self.reader.diagnostics[0])
        self.assertIn("Round 1 assistant", self.reader.diagnostics[0])
        check_document(self.output.read_text(encoding="utf-8"))

    def test_unsafe_math_is_published_only_as_literal_text(self):
        self.records[-1] = self.message("assistant", r"$\input{auth.json}$", START + 20)
        self.write()
        with self.assertWarns(TexExportWarning):
            self.reader.exportToTex(CHAT_ID, self.output)
        source = self.output.read_text(encoding="utf-8")
        self.assertNotIn(r"\input{", source)
        self.assertIn("公式原文", source)
        check_document(source)

    def test_cli_export_returns_path_as_json(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = main(["--home", str(self.home), "export-tex", CHAT_ID, str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), str(self.output.resolve()))
        self.assertTrue(self.output.is_file())
        self.assertEqual(error.getvalue(), "")

    def test_cli_content_warning_preserves_json_stdout_and_success_exit(self):
        self.records[-1] = self.message("assistant", r"$\frac{1}{x$", START + 20)
        self.write()
        error = io.StringIO()
        output = io.StringIO()
        with redirect_stderr(error), redirect_stdout(output), self.assertWarns(TexExportWarning):
            code = main(["--home", str(self.home), "export-tex", CHAT_ID, str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue()), str(self.output.resolve()))
        self.assertTrue(self.output.is_file())

    def test_export_neither_searches_for_nor_invokes_a_compiler(self):
        with patch.dict("os.environ", {"PATH": ""}), patch("shutil.which", side_effect=AssertionError("compiler discovery is forbidden")), patch("subprocess.run", side_effect=AssertionError("subprocess execution is forbidden")), patch("subprocess.Popen", side_effect=AssertionError("subprocess execution is forbidden")):
            source = self.export()
        self.assertIn(r"\end{document}", source)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])

    def test_public_interfaces_have_no_compiler_or_timeout_parameter(self):
        for interface in (I_CodexInterface, C_CodexInterface):
            with self.subTest(interface=interface.__name__):
                self.assertEqual(list(inspect.signature(interface.exportToTex).parameters), ["self", "chat_id", "output_path", "overwrite"])

    def test_cli_rejects_removed_compiler_and_timeout_options(self):
        for option, value in (("--compiler", "xelatex"), ("--timeout", "120")):
            with self.subTest(option=option), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["--home", str(self.home), "export-tex", CHAT_ID, str(self.output), option, value])
            self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.output.exists())


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = MarkdownLatexRenderer()

    def test_tables_preserve_cells_alignment_and_inline_formatting(self):
        source = self.renderer.render("| 左 | 右 |\n| :--- | ---: |\n| $x_i^2$ | **重点** 与 `a_b` |")
        self.assertIn(r"\begin{longtable}", source)
        self.assertIn(r"\raggedright", source)
        self.assertIn(r"\raggedleft", source)
        self.assertIn(r"\(x_i^2\)", source)
        self.assertIn(r"\textbf{重点}", source)
        self.assertIn(r"\texttt{a\_b}", source)
        self.assertIn(r"\endhead", source)

    def test_inline_and_display_delimiters_are_rendered_as_math(self):
        source = self.renderer.render(r"$x_i^2$ and \(\frac{a}{b}\)" + "\n\n" + r"\[\sqrt{x}\]" + "\n\n" + r"$$\sum_i x_i$$")
        self.assertIn(r"\(x_i^2\)", source)
        self.assertIn(r"\(\frac{a}{b}\)", source)
        self.assertIn(r"\sqrt{x}", source)
        self.assertIn(r"\sum_i x_i", source)
        self.assertEqual(source.count(r"\["), 2)

    def test_standalone_ams_environment_is_not_double_wrapped(self):
        source = self.renderer.render(r"\begin{align}a&=b\\c&=d\end{align}")
        self.assertIn(r"\begin{align}", source)
        self.assertNotIn(r"\[", source)

    def test_display_ams_environment_inside_dollars_is_not_nested(self):
        source = self.renderer.render(r"$$\begin{equation}x=1\end{equation}$$")
        self.assertIn(r"\begin{equation}", source)
        self.assertNotIn(r"\[", source)

    def test_unicode_math_preserves_meaning_without_missing_font_symbols(self):
        source = math_source("α ≤ β + x² + y₁₂")
        self.assertIn(r"\alpha", source)
        self.assertIn(r"\leq", source)
        self.assertIn("x^{2}", source)
        self.assertIn("y_{12}", source)

    def test_unicode_greek_variants_keep_their_distinct_shapes(self):
        self.assertEqual(math_source("ε ϵ φ ϕ"), r"\varepsilon  \epsilon  \varphi  \phi ")

    def test_unicode_sets_and_minus_use_standard_math_glyphs(self):
        self.assertEqual(math_source("ℝ−ℤ"), r"\mathbb{R}-\mathbb{Z}")

    def test_code_math_stays_literal(self):
        source = self.renderer.render("`$x_i$`\n\n```tex\n$x_i$\n```")
        self.assertNotIn(r"\(x_i\)", source)
        self.assertIn(r"\texttt{\$x\_i\$}", source)
        self.assertIn(r"\begin{CodexCodeBlock}", source)

    def test_code_is_escaped_and_cannot_end_the_document(self):
        source = self.renderer.render("```\n\\end{document}\n\\input{auth.json}\n```\n")
        self.assertNotIn(r"\end{document}", source)
        self.assertNotIn(r"\input{auth.json}", source)
        self.assertIn(r"\textbackslash{}", source)
        self.assertIn(r"\allowbreak{}", source)

    def test_code_preserves_indentation_and_blank_lines(self):
        source = self.renderer.render("```python\n    pass\n\nnext_line\n```\n")
        self.assertGreaterEqual(source.count(r"\hspace*{\fontdimen2\font}"), 4)
        self.assertIn("\\strut{}\\par\n", source)

    def test_currency_and_plain_special_characters_are_escaped(self):
        source = self.renderer.render("价格 $5 和 $10，50%，a_b & c#。")
        self.assertNotIn(r"\(", source)
        self.assertIn(r"\$5", source)
        self.assertIn(r"50\%", source)
        self.assertIn(r"a\_b \& c\#", source)

    def test_multiline_bracket_math_interrupts_paragraph_text(self):
        source = self.renderer.render("公式如下：\n\\[\n\\boxed{x_1+x_2=3}\n\\]\n结论。")
        self.assertIn("\\[\n\\boxed{x_1+x_2=3}\n\\]", source)
        self.assertNotIn(r"\textbackslash{}boxed", source)
        self.assertLess(source.index("公式如下"), source.index(r"\boxed"))
        self.assertLess(source.index(r"\boxed"), source.index("结论"))

    def test_multiline_bracket_math_in_list_and_quote(self):
        source = self.renderer.render("- 公式：\n  \\[\n  x_1+x_2=3\n  \\]\n\n> 结论：\n> \\[\n> y=2\n> \\]")
        self.assertEqual(source.count("\\[\n"), 2)
        self.assertIn("x_1+x_2=3", source)
        self.assertIn("y=2", source)

    def test_bracket_math_in_code_and_escaped_openers_stay_literal(self):
        source = self.renderer.render(r"`\[x_1\]` and \\[x_2\\]")
        self.assertNotIn("\\[\n", source)
        self.assertIn(r"\textbackslash{}[x\_1", source)

    def test_multiline_bracket_math_displays_unsafe_commands_literally(self):
        with self.assertWarns(TexExportWarning):
            source = self.renderer.render("公式：\n\\[\n\\input{private}\n\\]")
        self.assertNotIn(r"\input{", source)
        self.assertIn("公式原文", source)

    def test_math_equals_lines_are_not_markdown_headings(self):
        source = self.renderer.render("记\n\\[\n[A,A]_{\\mathrm{alg}}\n=\n\\{x:x=0\\}.\n\\]\n正文。")
        self.assertIn("\\[\n[A,A]_{\\mathrm{alg}}\n=\n", source)
        self.assertNotIn(r"\subsubsection", source)
        self.assertNotIn(r"\textbackslash", source)

    def test_escaped_newlines_are_spaces_but_matrix_rows_are_preserved(self):
        self.assertEqual(math_source("a\\\nb"), r"a\ b")
        formula = "\\begin{matrix}a\\\\\nb\\end{matrix}"
        self.assertEqual(math_source(formula), formula)

    def test_unclosed_math_is_explicitly_preserved_as_source(self):
        for markdown in (r"公式：\[x_1", r"公式：\(x_1", "公式：\n\\[\nx=1"):
            with self.subTest(markdown=markdown), self.assertWarns(TexExportWarning):
                source = self.renderer.render(markdown)
            self.assertIn("公式原文", source)

    def test_unclosed_delimiters_inside_code_remain_literal(self):
        source = self.renderer.render("`\\[x_1`\n\n```tex\n\\(x_1\n```")
        self.assertIn(r"\texttt{", source)
        self.assertIn(r"\begin{CodexCodeBlock}", source)

    def test_long_paths_links_and_inline_code_can_wrap(self):
        path = "C:/example/" + "long_file_name_" * 10 + ".pdf"
        for markdown in (path, "`" + path + "`"):
            with self.subTest(markdown=markdown):
                source = self.renderer.render(markdown)
                self.assertIn(r"\allowbreak{}", source)
                self.assertIn(r"\_", source)

    def test_raw_tex_and_remote_images_are_not_executed_or_downloaded(self):
        source = self.renderer.render(r"\input{secret} ![图像](https://example.invalid/a.png)")
        self.assertIn(r"\textbackslash{}input\{secret\}", source)
        self.assertIn("[图片：图像]", source)
        self.assertNotIn(r"\includegraphics", source)

    def test_headings_do_not_add_extra_round_sections(self):
        source = render_document("测试", [{"role": "user", "content": "# 问题"}, {"role": "assistant", "content": "## 回答"}], START, START)
        self.assertEqual(source.count(r"\section{"), 1)
        self.assertIn(r"\subsubsection*{问题}", source)

    def test_title_special_characters_cannot_inject_tex(self):
        source = render_document(r"标题 & _ % \input{a}", [], None, None)
        self.assertIn(r"\title{标题 \& \_ \% \textbackslash{}input\{a\}}", source)
        self.assertIn("未知（本地历史未保存）", source)

    def test_file_operations_execution_and_redefinition_are_not_emitted_as_math(self):
        for formula in (r"\input{auth.json}", r"\write18{command}", r"\csname input\endcsname", r"\def\x{a}", r"\directlua{code}", r"\begin{filecontents}{x}a\end{filecontents}", r"\^^69nput{x}"):
            with self.subTest(formula=formula), self.assertRaises(TexExportError):
                math_source(formula)

    def test_unrecognized_mathematical_macros_are_not_rejected(self):
        formula = r"\unknowncustommacro{x}"
        self.assertEqual(math_source(formula), formula)

    def test_unsupported_characters_are_preserved_as_code_point_markers(self):
        for text in ("a\x00b", "a\x1bb", "a\ud800b"):
            with self.subTest(text=repr(text)), self.assertWarns(TexExportWarning):
                source = self.renderer.render(text)
            self.assertIn("[U+", source)

    def test_subtitle_time_uses_system_local_timezone_without_changing_epoch(self):
        expected = datetime.fromtimestamp(START, timezone.utc).astimezone()
        actual = local_time_text(START)
        self.assertEqual(actual, expected.isoformat(sep=" "))
        self.assertEqual(datetime.fromisoformat(actual).timestamp(), START)
        self.assertIsNotNone(datetime.fromisoformat(actual).utcoffset())


class ComplexExportTests(ExportFixture, unittest.TestCase):
    def test_multiline_bracket_formulas_and_long_links_export(self):
        answer = "公式如下：\n\\[\n\\boxed{\\frac{1}{2}+x=3}\n\\]\n"
        answer += r"\(A=\bigoplus_i A_i,\quad a\precsim b\)" + "\n"
        answer += "记\n\\[\n[A,A]\n=\n\\{0\\}\n\\]\n"
        answer += "集合：\n\\[\\{0,\\\n1\\}\\]\n"
        answer += "[参考](https://example.invalid/" + "long_path_" * 30 + ")"
        self.records[-1] = self.message("assistant", answer, START + 20)
        self.write()
        result = self.reader.exportToTex(CHAT_ID, self.output)
        source = result.read_text(encoding="utf-8")
        self.assertIn(r"\boxed{\frac{1}{2}+x=3}", source)
        self.assertIn(r"\href{https://example.invalid/", source)

    def test_rounds_tables_math_code_and_unicode_export(self):
        self.records[-1] = self.message("assistant", COMPLEX_ANSWER, START + 20)
        self.records.extend([self.message("user", "第二问：保留原文与换行。", START + 30), self.message("assistant", "第二答：\\(a+b=c\\)。", START + 40)])
        self.write()
        result = self.reader.exportToTex(CHAT_ID, self.output)
        source = result.read_text(encoding="utf-8")
        self.assertEqual(source.count(r"\section{"), 2)
        self.assertIn(r"\begin{longtable}", source)
        self.assertIn(r"\begin{CodexCodeBlock}", source)
        self.assertEqual(list(self.output.parent.iterdir()), [self.output])
        self.assertNotIn("REASONING_SECRET", source)

    def test_archived_conversation_exports(self):
        archive = self.home / "archived_sessions" / self.path.name
        archive.parent.mkdir(parents=True)
        self.path.replace(archive)
        self.connection.execute("UPDATE threads SET archived = 1")
        self.connection.commit()
        self.assertTrue(self.reader.exportToTex(CHAT_ID, self.output).is_file())

    def test_invalid_formula_can_be_exported_without_guessing_missing_content(self):
        self.records[-1] = self.message("assistant", r"$\frac{1}{x$", START + 20)
        self.write()
        self.output.parent.mkdir(parents=True)
        self.output.write_bytes(b"preserve existing file")
        with self.assertWarns(TexExportWarning):
            self.reader.exportToTex(CHAT_ID, self.output, overwrite=True)
        source = self.output.read_text(encoding="utf-8")
        self.assertIn("公式原文", source)
        self.assertNotIn(r"\frac{1}{x", source)
        check_document(source)


if __name__ == "__main__":
    unittest.main()
