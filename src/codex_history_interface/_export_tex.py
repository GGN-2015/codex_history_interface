from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile
from typing import Callable
from urllib.parse import unquote
import warnings

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from mdit_py_plugins.dollarmath import dollarmath_plugin

from ._assets import AssetBundle, NATIVE_IMAGES, remote_reference
from ._tex_math import prepare_math
from ._tex_references import EquationReferences
from .errors import TexExportError, TexExportWarning
from .interface import D_ChatMessage


TEXT_ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
    "&": r"\&", "#": r"\#", "_": r"\_", "%": r"\%",
    "^": r"\textasciicircum{}", "~": r"\textasciitilde{}",
}
MATH_ENVIRONMENTS = set("""
equation equation* align align* alignat alignat* gather gather* multline multline*
flalign flalign* eqnarray eqnarray* displaymath math aligned alignedat gathered
split cases matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix smallmatrix array
""".split())
DISPLAY_ENVIRONMENTS = {
    "equation", "equation*", "align", "align*", "alignat", "alignat*", "gather",
    "gather*", "multline", "multline*", "flalign", "flalign*", "eqnarray",
    "eqnarray*", "displaymath",
}
GREEK = dict(zip(
    "αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩϵϑϕς",
    "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma tau upsilon phi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega varepsilon vartheta varphi varsigma".split(),
))
MATH_SYMBOLS = {
    **GREEK, "ε": "varepsilon", "ϵ": "epsilon", "φ": "varphi", "ϕ": "phi",
    "≤": "leq", "≥": "geq", "≠": "neq", "≈": "approx", "≡": "equiv",
    "±": "pm", "∓": "mp", "×": "times", "÷": "div", "·": "cdot", "∞": "infty",
    "∈": "in", "∉": "notin", "⊂": "subset", "⊆": "subseteq", "⊃": "supset",
    "⊇": "supseteq", "∩": "cap", "∪": "cup", "∅": "varnothing", "∀": "forall",
    "∃": "exists", "∂": "partial", "∇": "nabla", "∑": "sum", "∏": "prod",
    "∫": "int", "→": "to", "←": "leftarrow", "↔": "leftrightarrow",
    "⇒": "Rightarrow", "⇔": "Leftrightarrow", "∧": "land", "∨": "lor", "¬": "neg",
    "ℏ": "hbar", "ℓ": "ell", "…": "ldots", "⟨": "langle", "⟩": "rangle",
}
MATH_CHARACTERS = {
    "−": "-", "ℝ": r"\mathbb{R}", "ℤ": r"\mathbb{Z}", "ℕ": r"\mathbb{N}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "ℙ": r"\mathbb{P}", "ℍ": r"\mathbb{H}",
}
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾", "0123456789+-=()")
SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
PREAMBLE = r"""\documentclass[UTF8,fontset=fandol]{ctexart}
\usepackage{amsmath,amssymb,mathtools,bm}
\usepackage{longtable,booktabs,array}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage[normalem]{ulem}
\usepackage{tcolorbox}
\tcbuselibrary{breakable}
\definecolor{CodexCodeBackground}{HTML}{F6F8FA}
\definecolor{CodexCodeBorder}{HTML}{D0D7DE}
\definecolor{CodexHighlight}{HTML}{FFF2A8}
\usepackage[margin=24mm]{geometry}
\usepackage[unicode,colorlinks=true,urlcolor=blue,linkcolor=blue,breaklinks=true]{hyperref}
\setlength{\emergencystretch}{3em}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.5em}
\pagestyle{plain}
\setcounter{MaxMatrixCols}{64}
\providecommand{\degree}{\ensuremath{^{\circ}}}
\providecommand{\omicron}{o}
\newtcolorbox{CodexCodeBlock}{breakable,colback=CodexCodeBackground,
colframe=CodexCodeBorder,boxrule=0.4pt,arc=2pt,left=6pt,right=6pt,top=5pt,bottom=5pt,
fontupper=\small\ttfamily,before upper={\raggedright\setlength{\parindent}{0pt}\setlength{\parskip}{0pt}}}
"""


def validate_text(text: str) -> None:
    if any((ord(character) < 32 and character not in "\n\r\t") or 0xD800 <= ord(character) <= 0xDFFF for character in text):
        raise TexExportError("Conversation text contains unsupported control characters or invalid Unicode")


def tex_escape(text: str, *, code: bool = False) -> str:
    validate_text(text)
    pieces = []
    for character in text:
        if code and character == " ":
            pieces.append(r"\hspace*{\fontdimen2\font}")
        elif character in "\r\n\t":
            pieces.append(" ")
        else:
            pieces.append(TEXT_ESCAPES.get(character, character))
    return "".join(pieces)


def wrappable_text(text: str, *, code: bool = False) -> str:
    pieces = re.split(r"(\S{24,})", text)
    return "".join(
        r"\allowbreak{}".join(tex_escape(character, code=code) for character in piece)
        if len(piece) >= 24 and not any(character.isspace() for character in piece)
        else tex_escape(piece, code=code)
        for piece in pieces
    )


def literal_math_token(state: StateBlock | StateInline, content: str, reason: str, *, display: bool) -> None:
    token = state.push("codex_math_literal", "", 0)
    token.content = content
    token.meta.update(reason=reason, display=display)


def delimiter_block(state: StateBlock, start_line: int, end_line: int, silent: bool, opening: str, closing: str) -> bool:
    beginning = state.bMarks[start_line] + state.tShift[start_line]
    if state.sCount[start_line] - state.blkIndent >= 4 or not state.src.startswith(opening, beginning):
        return False
    if silent:
        return True
    lines = []
    for line_number in range(start_line, end_line):
        beginning = state.bMarks[line_number] + state.tShift[line_number]
        line = state.src[beginning:state.eMarks[line_number]]
        ending = line.find(closing, len(opening) if line_number == start_line else 0)
        while ending >= 0:
            backslashes = len(line[:ending]) - len(line[:ending].rstrip("\\"))
            if backslashes % 2 == 0:
                break
            ending = line.find(closing, ending + len(closing))
        if ending >= 0:
            lines.append(line[:ending])
            token = state.push("math_block", "math", 0)
            token.block = True
            token.content = "\n".join(lines)[len(opening):]
            token.markup = opening
            token.map = [start_line, line_number + 1]
            suffix = line[ending + len(closing):]
            if suffix.strip():
                state.push("paragraph_open", "p", 1)
                token = state.push("inline", "", 0)
                token.content = suffix.lstrip()
                token.children = []
                state.push("paragraph_close", "p", -1)
            state.line = line_number + 1
            return True
        lines.append(line)
    boundary = next((number for number in range(start_line + 1, end_line) if not state.src[state.bMarks[number]:state.eMarks[number]].strip()), end_line)
    content = "\n".join(lines[:boundary - start_line])
    literal_math_token(state, content, "Unclosed mathematical delimiter: " + opening, display=True)
    state.line = boundary
    return True


def bracket_display_block(state: StateBlock, start_line: int, end_line: int, silent: bool) -> bool:
    return delimiter_block(state, start_line, end_line, silent, r"\[", r"\]")


def dollar_display_block(state: StateBlock, start_line: int, end_line: int, silent: bool) -> bool:
    return delimiter_block(state, start_line, end_line, silent, "$$", "$$")


def bracket_display_inline(state: StateInline, silent: bool) -> bool:
    opening = state.src[state.pos:state.pos + 2]
    if opening not in (r"\[", r"\("):
        return False
    closing = r"\]" if opening == r"\[" else r"\)"
    ending = state.src.find(closing, state.pos + 2, state.posMax)
    while ending >= 0:
        backslashes = 0
        position = ending - 1
        while position >= 0 and state.src[position] == "\\":
            backslashes += 1
            position -= 1
        if backslashes % 2 == 0:
            break
        ending = state.src.find(closing, ending + 2, state.posMax)
    if ending < 0:
        if not silent:
            literal_math_token(state, state.src[state.pos:state.posMax], "Unclosed mathematical delimiter: " + opening, display=opening == r"\[")
        state.pos = state.posMax
        return True
    if not silent:
        token = state.push("math_inline_double" if opening == r"\[" else "math_inline", "math", 0)
        token.content = state.src[state.pos + 2:ending]
        token.markup = opening
    state.pos = ending + 2
    return True


def environment_end(text: str) -> tuple[int, str | None]:
    stack = []
    for match in re.finditer(r"\\(?:begin|end)\{([A-Za-z*]+)\}|\\(?:[A-Za-z@]+|[\s\S])|%[^\n]*", text):
        if match.group(1) is None:
            continue
        if match.group().startswith(r"\begin{"):
            stack.append(match.group(1))
        elif not stack or stack.pop() != match.group(1):
            return match.end(), "Mismatched mathematical environment"
        elif not stack:
            return match.end(), None
    boundary = re.search(r"\n[ \t]*\n", text)
    return (boundary.start() if boundary else len(text)), "Unclosed mathematical environment"


def environment_inline(state: StateInline, silent: bool) -> bool:
    match = re.match(r"\\begin\{([A-Za-z*]+)\}", state.src[state.pos:state.posMax])
    if match is None or match.group(1) not in MATH_ENVIRONMENTS:
        return False
    content = state.src[state.pos:state.posMax]
    ending, reason = environment_end(content)
    if not silent:
        if reason:
            literal_math_token(state, content[:ending], reason, display=True)
        else:
            token = state.push("math_inline_double", "math", 0)
            token.content = content[:ending]
    state.pos += ending
    return True


def environment_block(state: StateBlock, start_line: int, end_line: int, silent: bool) -> bool:
    beginning = state.bMarks[start_line] + state.tShift[start_line]
    match = re.match(r"\\begin\{([A-Za-z*]+)\}", state.src[beginning:state.eMarks[start_line]])
    if state.sCount[start_line] - state.blkIndent >= 4 or match is None or match.group(1) not in MATH_ENVIRONMENTS:
        return False
    if silent:
        return True
    content = "\n".join(state.src[state.bMarks[number] + state.tShift[number]:state.eMarks[number]] for number in range(start_line, end_line))
    ending, reason = environment_end(content)
    if reason:
        literal_math_token(state, content[:ending], reason, display=True)
    else:
        token = state.push("math_block", "math", 0)
        token.content = content[:ending]
        token.block = True
    consumed = content[:ending].count("\n") + 1
    suffix = content[ending:].split("\n", 1)[0]
    if suffix.strip():
        state.push("paragraph_open", "p", 1)
        token = state.push("inline", "", 0)
        token.content = suffix.lstrip()
        token.children = []
        state.push("paragraph_close", "p", -1)
    state.line = start_line + consumed
    return True


def double_dollar_inline(state: StateInline, silent: bool) -> bool:
    if not state.src.startswith("$$", state.pos):
        return False
    ending = state.src.find("$$", state.pos + 2, state.posMax)
    while ending >= 0:
        backslashes = len(state.src[:ending]) - len(state.src[:ending].rstrip("\\"))
        if backslashes % 2 == 0:
            break
        ending = state.src.find("$$", ending + 2, state.posMax)
    if ending < 0:
        if not silent:
            literal_math_token(state, state.src[state.pos:state.posMax], "Unclosed mathematical delimiter: $$", display=True)
        state.pos = state.posMax
        return True
    if not silent:
        token = state.push("math_inline_double", "math", 0)
        token.content = state.src[state.pos + 2:ending]
        token.markup = "$$"
    state.pos = ending + 2
    return True


def styled_inline(state: StateInline, silent: bool) -> bool:
    beginning = state.pos
    match = re.match(r"<(u|ins|s|del|sub|sup|kbd|mark)\s*>", state.src[beginning:state.posMax], re.I)
    line_break = re.match(r"<br\s*/?>", state.src[beginning:state.posMax], re.I)
    if line_break:
        if not silent:
            state.push("hardbreak", "br", 0)
        state.pos += line_break.end()
        return True
    if match is None:
        return False
    style = match.group(1).lower()
    depth = 1
    for ending in re.finditer(r"</?" + style + r"\s*>", state.src[beginning + match.end():state.posMax], re.I):
        depth += -1 if ending.group().startswith("</") else 1
        if depth:
            continue
        content = state.src[beginning + match.end():beginning + match.end() + ending.start()]
        if not silent:
            token = state.push("codex_style", "", 0)
            token.meta["style"] = style
            token.children = []
            nesting = state.env.get("codex_style_depth", 0)
            if nesting >= 40:
                token.type = "codex_literal"
                token.content = state.src[beginning:beginning + match.end() + ending.end()]
                token.meta["reason"] = "Deep inline styles were preserved literally"
                state.pos = beginning + match.end() + ending.end()
                return True
            state.env["codex_style_depth"] = nesting + 1
            try:
                state.md.inline.parse(content, state.md, state.env, token.children)
            finally:
                state.env["codex_style_depth"] = nesting
        state.pos = beginning + match.end() + ending.end()
        return True
    return False


def protect_table_pipes(state) -> None:
    if "|" not in state.src:
        return
    sentinel = next((chr(value) for value in range(0xE000, 0xF8FF) if chr(value) not in state.src), None)
    if sentinel is None:
        counter = 0
        sentinel = "CODEXPIPEMARKER0"
        while sentinel in state.src:
            counter += 1
            sentinel = "CODEXPIPEMARKER" + str(counter)
    pattern = re.compile(r"(?P<code>`+)(?!`)[^\n]*?(?P=code)(?!`)|\\\([^\n]*?\\\)|(?<![\\$])(?P<math>\${1,2})(?!\$)[^\n]*?(?<!\\)(?P=math)(?!\$)")
    state.src = pattern.sub(lambda match: match.group().replace("|", sentinel), state.src)
    state.env["codex_table_pipe"] = sentinel


def restore_table_pipes(state) -> None:
    sentinel = state.env.pop("codex_table_pipe", None)
    if sentinel is not None:
        for token in state.tokens:
            token.content = token.content.replace(sentinel, "|")


def normalize_math_symbols(text: str) -> str:
    result = "".join("\\" + MATH_SYMBOLS[character] + " " if character in MATH_SYMBOLS else MATH_CHARACTERS.get(character, character) for character in text)
    result = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾]+", lambda match: "^{" + match.group().translate(SUPERSCRIPTS) + "}", result)
    return re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎]+", lambda match: "_{" + match.group().translate(SUBSCRIPTS) + "}", result)


def math_source(text: str) -> str:
    validate_text(text)
    text = re.sub(r"\n(?:[ \t]*\n)+", "\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    return prepare_math(text, normalize=normalize_math_symbols, escape_text=tex_escape)


class MarkdownLatexRenderer:
    def __init__(self, assets: AssetBundle | None = None, *, warn: Callable[[str], None] | None = None, defer_references: bool = False) -> None:
        self.assets = assets
        self.warn_callback = warn
        self.notices: set[str] = set()
        self.references = EquationReferences()
        self.defer_references = defer_references
        self.message_number = 0
        self.formula_number = 0
        self.context = "Document"
        self.parser = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])
        validate_link = self.parser.validateLink
        self.parser.validateLink = lambda reference: reference.lower().startswith("file:") or validate_link(reference)
        self.parser.use(dollarmath_plugin, allow_space=False, allow_digits=False, allow_labels=False, double_inline=True)
        self.parser.block.ruler.disable(["math_block"])
        self.parser.block.ruler.before("fence", "bracket_display_block", bracket_display_block, {"alt": ["paragraph", "reference", "blockquote", "list"]})
        self.parser.block.ruler.before("fence", "dollar_display_block", dollar_display_block, {"alt": ["paragraph", "reference", "blockquote", "list"]})
        self.parser.block.ruler.before("fence", "environment_block", environment_block, {"alt": ["paragraph", "reference", "blockquote", "list"]})
        self.parser.inline.ruler.before("escape", "bracket_display_inline", bracket_display_inline)
        self.parser.inline.ruler.before("escape", "environment_inline", environment_inline)
        self.parser.inline.ruler.before("math_inline", "double_dollar_inline", double_dollar_inline)
        self.parser.inline.ruler.before("escape", "styled_inline", styled_inline)
        self.parser.core.ruler.before("block", "protect_table_pipes", protect_table_pipes)
        self.parser.core.ruler.before("inline", "restore_table_pipes", restore_table_pipes)

    def warn(self, text: str) -> None:
        if text in self.notices:
            return
        self.notices.add(text)
        if self.warn_callback:
            self.warn_callback(text)
        else:
            warnings.warn(text, TexExportWarning, stacklevel=3)

    def safe_text(self, text: str, *, context: str | None = None) -> str:
        invalid = {character for character in text if (ord(character) < 32 and character not in "\n\r\t") or 0xD800 <= ord(character) <= 0xDFFF}
        if invalid:
            markers = {character: "[U+" + format(ord(character), "04X") + "]" for character in invalid}
            self.warn((context or self.context) + ": unsupported Unicode/control characters were preserved as visible code-point markers")
            return "".join(markers.get(character, character) for character in text)
        return text

    def literal_math(self, original: str, reason: str, *, display: bool) -> str:
        self.formula_number += 1
        self.warn(f"{self.context}, formula {self.formula_number}: {reason}; original TeX was preserved literally, not executed or guessed")
        if display:
            return self.code_block(original, "公式原文（未作数学排版）")
        return "〔公式原文〕" + r"\texttt{" + wrappable_text(original, code=True) + "}"

    def math(self, text: str, *, display: bool, original: str | None = None) -> str:
        try:
            source = math_source(text)
        except TexExportError as error:
            raw = original if original is not None else text
            return self.literal_math(raw, str(error).removeprefix("Invalid mathematical content: "), display=display)
        self.formula_number += 1
        source = self.references.register(source, self.message_number, self.context + f", formula {self.formula_number}")
        environment = re.fullmatch(r"\s*\\begin\s*\{([A-Za-z*]+)\}([\s\S]*)\\end\s*\{\1\}\s*", source)
        if environment:
            name, body = environment.groups()
            if display and name in DISPLAY_ENVIRONMENTS:
                return "\n" + source + "\n"
            if name in {"math", "displaymath", "equation", "equation*"}:
                source = body
            elif not display and name in DISPLAY_ENVIRONMENTS:
                inline_name = "alignedat" if name.startswith("alignat") else "gathered" if name.startswith(("gather", "multline")) else "aligned"
                source = "\\begin{" + inline_name + "}" + body + "\\end{" + inline_name + "}"
        if not display:
            return r"\(" + source + r"\)"
        return "\n\\[" + ("" if source.startswith("\n") else "\n") + source + ("" if source.endswith("\n") else "\n") + "\\]\n"

    def original_math(self, token: Token) -> str:
        if token.content.lstrip().startswith(r"\begin{") and not token.markup:
            return token.content
        opening = token.markup or (r"\(" if token.type == "math_inline" else r"\[")
        closing = {"$": "$", "$$": "$$", r"\(": r"\)", r"\[": r"\]"}.get(opening, r"\]")
        return opening + token.content + closing

    def finish(self, source: str) -> str:
        return self.references.resolve(source, escape=tex_escape, warn=self.warn)

    def link(self, reference: str, label: str, *, image: bool = False) -> str:
        reference = reference.strip()
        if remote_reference(reference):
            validate_text(reference)
            return r"\href{" + tex_escape(reference) + "}{" + label + "}"
        if self.assets is None:
            return label
        asset = self.assets.add(reference)
        if asset is None:
            return label + "（本地附件不可用，未复制）"
        if image and asset.staged.suffix.lower() in NATIVE_IMAGES:
            return "\n\\par\\noindent\\includegraphics[width=\\linewidth,height=0.65\\textheight,keepaspectratio]{" + asset.filename + "}\\par\n"
        return r"\href{run:" + asset.filename + "}{" + label + "}"

    def text(self, text: str, *, linked: bool = False) -> str:
        pattern = re.compile(
            r':codex-file-citation\{\s*path="(?P<citation>[^"\n]+)"[^{}\n]*\}'
            r'|(?P<web>https?://[^\s<>"()]+)'
            r'|"(?P<quoted>(?:[A-Za-z]:[\\/]|/(?!/)|~/)[^"\n]+)"'
            r'|(?<![\w/])(?P<spaced>(?:[A-Za-z]:[\\/]|/(?!/)|~/)[^\n<>"{}]*?\.[A-Za-z0-9]{1,12})(?=$|[，。；,;)]|\s)'
            r'|(?<![\w/])(?P<path>(?:[A-Za-z]:[\\/][^\s<>"{}]+|~?/(?!/)(?=[^\s<>"{}]*(?:/|\.[A-Za-z0-9]))[^\s<>"{}]+))'
        )
        pieces = []
        beginning = 0
        for match in pattern.finditer(text):
            pieces.append(wrappable_text(text[beginning:match.start()]))
            original = next(value for value in match.groupdict().values() if value is not None)
            reference = original.rstrip(".,;，。；）)") if match.group("web") or match.group("path") else original
            if match.group("web"):
                label = wrappable_text(reference)
            else:
                label = wrappable_text(unquote(reference).replace("\\", "/").rsplit("/", 1)[-1])
            pieces.append(label if linked else self.link(reference, label))
            pieces.append(wrappable_text(original[len(reference):]))
            beginning = match.end()
        pieces.append(wrappable_text(text[beginning:]))
        return "".join(pieces)

    def inline(self, tokens: list[Token], *, compact: bool = False) -> str:
        pieces = []
        links = []
        styles = 0
        for token in tokens:
            kind = token.type
            if kind == "text":
                pieces.append(self.text(token.content, linked=bool(links)))
            elif kind == "code_inline":
                content = token.content
                label = r"\texttt{" + wrappable_text(content, code=True) + "}"
                if self.assets is not None and re.match(r"^(?:[A-Za-z]:[\\/]|/(?!/)|~/|file:)", content):
                    label = r"\texttt{" + wrappable_text(unquote(content).replace("\\", "/").rsplit("/", 1)[-1], code=True) + "}"
                    label = self.link(content, label)
                pieces.append(label)
            elif kind == "math_inline":
                pieces.append(self.math(token.content, display=False, original=self.original_math(token)))
            elif kind == "math_inline_double":
                pieces.append(self.math(token.content, display=not (compact or styles or links), original=self.original_math(token)))
            elif kind == "codex_math_literal":
                pieces.append(self.literal_math(token.content, token.meta["reason"], display=bool(token.meta["display"]) and not (compact or styles or links)))
            elif kind == "codex_literal":
                self.warn(self.context + ": " + token.meta["reason"])
                pieces.append(r"\texttt{" + wrappable_text(token.content, code=True) + "}")
            elif kind in ("strong_open", "em_open", "s_open"):
                styles += 1
                pieces.append({"strong_open": r"\textbf{", "em_open": r"\emph{", "s_open": r"\sout{"}[kind])
            elif kind in ("strong_close", "em_close", "s_close"):
                styles -= 1
                pieces.append("}")
            elif kind == "codex_style":
                command = {"u": "uline", "ins": "uline", "s": "sout", "del": "sout", "sub": "textsubscript", "sup": "textsuperscript", "kbd": "texttt", "mark": "colorbox{CodexHighlight}"}[token.meta["style"]]
                pieces.append("\\" + command + "{" + self.inline(token.children or [], compact=True) + "}")
            elif kind == "softbreak":
                pieces.append("\n")
            elif kind == "hardbreak":
                pieces.append(r"\newline{}" + "\n")
            elif kind == "link_open":
                links.append((len(pieces), token.attrGet("href") or ""))
            elif kind == "link_close":
                beginning, reference = links.pop()
                label = "".join(pieces[beginning:])
                del pieces[beginning:]
                pieces.append(self.link(reference, label))
            elif kind == "image":
                label = "[图片：" + tex_escape(token.content or "未提供描述") + "]"
                pieces.append(self.link(token.attrGet("src") or "", label, image=not (compact or styles or links)))
            elif token.children:
                pieces.append(self.inline(token.children, compact=compact))
            else:
                pieces.append(tex_escape(token.content))
        return "".join(pieces)

    def code_block(self, text: str, language: str) -> str:
        validate_text(text)
        pieces = [r"\begin{CodexCodeBlock}" + "\n"]
        if language:
            pieces.append("[" + tex_escape(language) + "]\\par\n\\nopagebreak[4]\n")
        for line in text.expandtabs(4).splitlines():
            encoded = r"\allowbreak{}".join(tex_escape(character, code=True) for character in line)
            pieces.append(r"\strut{}" + encoded + "\\par\n")
        pieces.append("\\end{CodexCodeBlock}\n")
        return "".join(pieces)

    def table(self, tokens: list[Token]) -> str:
        rows = []
        row = []
        alignments = []
        header = True
        for token in tokens:
            if token.type == "th_open":
                style = token.attrGet("style") or ""
                alignments.append("centering" if "center" in style else "raggedleft" if "right" in style else "raggedright")
            elif token.type == "inline":
                row.append(self.inline(token.children or [], compact=True))
            elif token.type == "tr_close":
                rows.append((row, header))
                row = []
            elif token.type == "thead_close":
                header = False
        count = len(alignments)
        if not count:
            self.warn(self.context + ": a table without recognizable columns was preserved literally")
            return self.code_block("\n".join(token.content for token in tokens if token.content), "表格原文")
        width = r"\dimexpr(\linewidth-" + str(2 * count) + r"\tabcolsep)/" + str(count) + r"\relax"
        columns = "".join(r">{\%s\arraybackslash}p{%s}" % (alignment, width) for alignment in alignments)
        pieces = ["\\begingroup\n\\setlength{\\tabcolsep}{\\dimexpr\\linewidth/" + str(10 * count) + "\\relax}\n\\ifdim\\tabcolsep>6pt\\setlength{\\tabcolsep}{6pt}\\fi\n", r"\begin{longtable}{@{}" + columns + "@{}}\n\\toprule\n"]
        for cells, is_header in rows:
            pieces.append(" & ".join(r"\textbf{" + cell + "}" if is_header else cell for cell in cells) + r" \\" + "\n")
            if is_header:
                pieces.append("\\midrule\n\\endhead\n")
        pieces.append("\\bottomrule\n\\end{longtable}\n\\endgroup\n")
        return "".join(pieces)

    def render(self, text: str, *, context: str | None = None) -> str:
        self.message_number += 1
        self.context = context or f"Message {self.message_number}"
        self.formula_number = 0
        tokens = self.parser.parse(self.safe_text(text))
        pieces = []
        index = 0
        lists = []
        quotes = []
        heading = False
        while index < len(tokens):
            token = tokens[index]
            kind = token.type
            if kind == "table_open":
                ending = next(position for position in range(index + 1, len(tokens)) if tokens[position].type == "table_close")
                pieces.append(self.table(tokens[index + 1:ending]))
                index = ending
            elif kind == "inline":
                pieces.append(self.inline(token.children or [], compact=heading))
            elif kind == "paragraph_close":
                pieces.append("\\par\n")
            elif kind in ("fence", "code_block"):
                pieces.append(self.code_block(token.content, token.info.strip().split(" ", 1)[0]))
            elif kind in ("math_block", "math_block_label", "math_block_eqno", "amsmath"):
                pieces.append(self.math(token.content, display=True, original=self.original_math(token)))
            elif kind == "codex_math_literal":
                pieces.append(self.literal_math(token.content, token.meta["reason"], display=True))
            elif kind == "heading_open":
                heading = True
                pieces.append(r"\subsubsection*{")
            elif kind == "heading_close":
                heading = False
                pieces.append("}\n")
            elif kind in ("bullet_list_open", "ordered_list_open"):
                ordered = kind == "ordered_list_open"
                depth = sum(entry["native"] for entry in lists) + sum(quotes)
                same_depth = sum(entry["native"] and entry["ordered"] == ordered for entry in lists)
                native = depth < 6 and same_depth < 4 and (not lists or lists[-1]["native"])
                start = int(token.attrGet("start") or 1)
                lists.append({"ordered": ordered, "native": native, "next": start})
                if not native:
                    self.warn(self.context + ": deep list nesting was generated as indented paragraphs, preserving item labels and content")
                else:
                    pieces.append("\\begin{" + ("enumerate" if ordered else "itemize") + "}\n")
                if native and ordered and start != 1:
                    counter = ("enumi", "enumii", "enumiii", "enumiv")[same_depth]
                    pieces.append("\\setcounter{" + counter + "}{" + str(start - 1) + "}\n")
            elif kind in ("bullet_list_close", "ordered_list_close"):
                entry = lists.pop()
                if entry["native"]:
                    pieces.append("\\end{" + ("enumerate" if entry["ordered"] else "itemize") + "}\n")
            elif kind == "list_item_open":
                entry = lists[-1]
                if entry["native"]:
                    pieces.append(r"\item{} ")
                else:
                    label = str(entry["next"]) + ". " if entry["ordered"] else r"\textbullet{} "
                    pieces.append("\\par\\noindent\\hspace*{" + str(min(len(lists), 12) / 2) + "em}" + label)
                entry["next"] += 1
            elif kind == "list_item_close":
                pieces.append("\\par\n" if lists and not lists[-1]["native"] else "\n")
            elif kind == "blockquote_open":
                native = sum(entry["native"] for entry in lists) + sum(quotes) < 6
                quotes.append(native)
                if native:
                    pieces.append("\\begin{quote}\n")
                else:
                    self.warn(self.context + ": deep blockquote nesting was generated using grouped indentation")
                    pieces.append("\\par\\begingroup\\addtolength{\\leftskip}{1em}\n")
            elif kind == "blockquote_close":
                pieces.append("\\end{quote}\n" if quotes.pop() else "\\par\\endgroup\n")
            elif kind == "hr":
                pieces.append(r"\par\noindent\rule{\linewidth}{0.4pt}\par" + "\n")
            elif token.content:
                pieces.append(tex_escape(token.content))
            index += 1
        source = "".join(pieces)
        return source if self.defer_references else self.finish(source)


def local_time_text(value: float | None) -> str:
    if value is None:
        return "未知（本地历史未保存）"
    try:
        return datetime.fromtimestamp(value, timezone.utc).astimezone().isoformat(sep=" ")
    except (ValueError, OverflowError, OSError) as error:
        raise TexExportError("A stored timestamp cannot be displayed in the local timezone") from error


def render_document(title: str, messages: list[D_ChatMessage], created_at: float | None, modified_at: float | None, *, assets: AssetBundle | None = None, warn: Callable[[str], None] | None = None) -> str:
    renderer = MarkdownLatexRenderer(assets, warn=warn, defer_references=True)
    turns = []
    for message in messages:
        if message["role"] == "user":
            turns.append({"user": message["content"], "assistant": []})
        else:
            if not turns:
                turns.append({"user": None, "assistant": []})
            turns[-1]["assistant"].append(message["content"])
    pieces = [PREAMBLE, "\\title{" + tex_escape(renderer.safe_text(title, context="Title")) + "}\n\\author{}\n\\date{}\n\\begin{document}\n\\maketitle\n"]
    times = []
    for name, value in (("Creation time", created_at), ("Modification time", modified_at)):
        try:
            times.append(tex_escape(local_time_text(value)))
        except TexExportError:
            renderer.warn(name + ": an unrepresentable timestamp was displayed as unknown")
            times.append("未知（原始时间不可表示）")
    pieces.append("\\begin{center}\n创建时间：" + times[0] + "\\\\\n最后修改时间：" + times[1] + "\n\\end{center}\n")
    for number, turn in enumerate(turns, 1):
        pieces.append("\\section{第 " + str(number) + " 轮}\n\\subsection*{用户提问}\n")
        pieces.append(renderer.render(turn["user"], context=f"Round {number} user message") if turn["user"] is not None else "（本轮未保留用户提问）\n")
        pieces.append("\\subsection*{AI 回答}\n")
        pieces.extend(renderer.render(answer, context=f"Round {number} assistant reply {reply}") for reply, answer in enumerate(turn["assistant"], 1))
        if not turn["assistant"]:
            pieces.append("（本轮尚无最终答复）\n")
    pieces.append("\\end{document}\n")
    return renderer.finish("".join(pieces))


def export_destination(output_path: str | os.PathLike, home: Path, overwrite: bool) -> Path:
    destination = Path(output_path).expanduser().resolve()
    if destination.suffix.lower() != ".tex":
        raise ValueError("output_path must end with .tex")
    if destination.is_relative_to(home.resolve()):
        raise ValueError("The TeX destination must be outside the read-only Codex data directory")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {destination}; pass overwrite=True explicitly")
    return destination


def export_document(
    destination: Path,
    title: str,
    messages: list[D_ChatMessage],
    created_at: float | None,
    modified_at: float | None,
    *,
    overwrite: bool,
    bases: tuple[Path, ...] = (),
    protected: tuple[Path, ...] = (),
    warn: Callable[[str], None] | None = None,
    render_warn: Callable[[str], None] | None = None,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="codex-history-assets-") as folder:
        bundle = AssetBundle(Path(folder), destination, bases=bases, protected=protected, warn=warn)
        source = render_document(title, messages, created_at, modified_at, assets=bundle, warn=render_warn)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            bundle.publish()
            write_document(destination, source, overwrite=overwrite)
        except BaseException:
            bundle.rollback()
            raise
    return destination


def write_document(destination: Path, source: str, *, overwrite: bool) -> None:
    if not overwrite:
        stream = destination.open("x", encoding="utf-8", newline="\n")
        try:
            with stream:
                stream.write(source)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    else:
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent, prefix=".codex-export-", suffix=".tmp", delete=False) as stream:
                pending = Path(stream.name)
                stream.write(source)
            os.replace(pending, destination)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
