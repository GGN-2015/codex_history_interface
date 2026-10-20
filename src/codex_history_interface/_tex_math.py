import re
from typing import Callable

from .errors import TexExportError


BLOCKED_COMMANDS = set("""
input include includeonly includegraphics openin closein read readline
openout closeout write immediate special shipout directlua latelua
pdfobj pdfxform pdfrefobj pdfannot pdfextension pdfliteral pdfcatalog pdfnames
pdffilesize pdfmdfivesum pdfescapehex pdfunescapehex pdfescapestring
usepackage RequirePackage documentclass LoadClass
newcommand renewcommand providecommand DeclareRobustCommand
newenvironment renewenvironment newtheorem def gdef edef xdef let futurelet
newif newcount newdimen newskip newtoks newread newwrite
csname endcsname expandafter noexpand expanded scantokens detokenize
catcode mathcode lccode uccode sfcode delcode escapechar endlinechar newlinechar
begingroup endgroup bgroup egroup global globaldefs long outer protected
loop repeat everyjob everymath everydisplay everypar everyhbox everyvbox everycr
errhelp errmessage message typeout typein errorstopmode scrollmode nonstopmode batchmode
endinput bye @@input @@end primitive pdfprimitive pdfshellescape shellescape
ExplSyntaxOn ExplSyntaxOff makeatletter makeatother
""".split())
BLOCKED_ENVIRONMENTS = {"document", "filecontents", "filecontents*", "verbatim", "Verbatim"}
TEXT_COMMANDS = {"text", "textrm", "textnormal", "textbf", "textit", "texttt", "textsf", "mbox", "hbox", "fbox", "operatorname", "tag", "intertext", "shortintertext"}
MATH_TOKENS = re.compile(r"%[^\r\n]*|\\(?:begin|end)\s*\{[^{}]+\}|\\(?:[A-Za-z@]+|[\s\S])|[{}$]|\\\Z")


def without_comments(source: str) -> str:
    return re.sub(r"\\(?:[A-Za-z@]+|[\s\S])|%[^\r\n]*", lambda match: "" if match.group().startswith("%") else match.group(), source)


def prepare_math(source: str, *, normalize: Callable[[str], str], escape_text: Callable[[str], str]) -> str:
    if "^^" in without_comments(source):
        raise TexExportError("TeX character-code escapes are not allowed in mathematical content")
    pieces = []
    groups: list[bool] = []
    environments = []
    pending_text = None
    beginning = 0
    final_comment = False

    def append_text(content: str) -> None:
        text_mode = bool(groups and groups[-1])
        pieces.append(escape_text(content) if text_mode else normalize(content))

    for match in MATH_TOKENS.finditer(source):
        gap = source[beginning:match.start()]
        append_text(gap)
        if gap.strip():
            pending_text = None
        token = match.group()
        final_comment = token.startswith("%")
        if final_comment:
            pieces.append(token)
            beginning = match.end()
            continue
        environment = re.fullmatch(r"\\(begin|end)\s*\{([^{}]+)\}", token)
        if environment:
            action, name = environment.groups()
            if name in BLOCKED_ENVIRONMENTS:
                raise TexExportError("Document or file-operation environment is not allowed in a formula: " + name)
            if action == "begin":
                environments.append(name)
            elif not environments or environments.pop() != name:
                raise TexExportError("Mismatched mathematical environment")
            pending_text = None
        elif token == "{":
            groups.append(bool(groups and groups[-1]) if pending_text is None else pending_text)
            pending_text = None
        elif token == "}":
            if not groups:
                raise TexExportError("Unmatched closing group in mathematical content")
            groups.pop()
            pending_text = None
        elif token == "$":
            if not (groups and groups[-1]):
                raise TexExportError("Unexpected nested dollar delimiter in mathematical content")
            token = r"\$"
        elif token.startswith("\\"):
            command = token[1:]
            if command in BLOCKED_COMMANDS:
                raise TexExportError("File access, execution or TeX redefinition is not allowed in a formula: " + token)
            if command in {"(", ")", "[", "]"} or not command:
                raise TexExportError("Unmatched or nested mathematical delimiter")
            if command.isspace():
                token = r"\ "
            pending_text = True if command in TEXT_COMMANDS else False if command == "ensuremath" else None
        pieces.append(token)
        beginning = match.end()
    append_text(source[beginning:])
    if groups or environments:
        raise TexExportError("Unclosed mathematical group or environment")
    if final_comment and beginning == len(source):
        pieces.append("\n")
    return "".join(pieces)


def check_document(source: str) -> None:
    body = source.partition(r"\begin{document}")[2]
    if not body:
        raise TexExportError("Generated TeX has no document body")
    body = without_comments(body)
    tokens = re.findall(r"\\(?:begin|end)\{[^{}]+\}|\\(?:[A-Za-z@]+|[\s\S])|[{}]", body)
    environments = ["document"]
    braces = 0
    delimiters = []
    for token in tokens:
        if token == "{":
            braces += 1
        elif token == "}":
            braces -= 1
            if braces < 0:
                raise TexExportError("Generated TeX has an unmatched closing brace")
        elif token.startswith(r"\begin{"):
            environments.append(token[7:-1])
        elif token.startswith(r"\end{"):
            if not environments or environments.pop() != token[5:-1]:
                raise TexExportError("Generated TeX environments are unbalanced")
        elif token in (r"\(", r"\["):
            if delimiters:
                raise TexExportError("Generated TeX contains nested mathematical delimiters")
            delimiters.append(token)
        elif token in (r"\)", r"\]"):
            if not delimiters or delimiters.pop() != {r"\)": r"\(", r"\]": r"\["}[token]:
                raise TexExportError("Generated TeX mathematical delimiters are unbalanced")
    if braces or environments or delimiters or not body.rstrip().endswith(r"\end{document}"):
        raise TexExportError("Generated TeX has an unclosed group or environment")
    labels = re.findall(r"(?<!\\)\\label\{([^{}]+)\}", body)
    references = re.findall(r"(?<!\\)\\(?:eqref|ref)\{([^{}]+)\}", body)
    if len(labels) != len(set(labels)) or set(references) - set(labels):
        raise TexExportError("Equation labels are duplicated or references cannot be resolved in the export")
