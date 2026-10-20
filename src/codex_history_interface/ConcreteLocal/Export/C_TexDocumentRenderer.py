from datetime import datetime, timezone
from typing import Callable

from ...Abstract.History.D_ChatMessage import D_ChatMessage
from ...errors import TexExportError
from .C_AssetBundle import C_AssetBundle
from .C_MarkdownLatexRenderer import C_MarkdownLatexRenderer, PREAMBLE, tex_escape


class C_TexDocumentRenderer:
    """Assemble a conversation using a fresh Markdown renderer for every document."""

    def __init__(
        self,
        *,
        renderer_type: type[C_MarkdownLatexRenderer] = C_MarkdownLatexRenderer,
        preamble: str = PREAMBLE,
    ) -> None:
        self.renderer_type = renderer_type
        self.preamble = preamble

    @staticmethod
    def formatTimestamp(value: float | None, *, include_timezone: bool = True) -> str:
        if value is None:
            return "未知（本地历史未保存）"
        try:
            local = datetime.fromtimestamp(value, timezone.utc).astimezone()
            return (local if include_timezone else local.replace(tzinfo=None)).isoformat(sep=" ", timespec="seconds")
        except (ValueError, OverflowError, OSError) as error:
            raise TexExportError("A stored timestamp cannot be displayed in the local timezone") from error

    def render(
        self,
        title: str,
        messages: list[D_ChatMessage],
        created_at: float | None,
        modified_at: float | None,
        *,
        assets: C_AssetBundle | None = None,
        warn: Callable[[str], None] | None = None,
        message_timestamps: tuple[float | None, ...] = (),
    ) -> str:
        renderer = self.renderer_type(assets, warn=warn, defer_references=True)
        turns = []
        for index, message in enumerate(messages):
            if message["role"] == "user":
                turns.append({"user": message["content"], "timestamp": message_timestamps[index] if index < len(message_timestamps) else None, "assistant": []})
            else:
                if not turns:
                    turns.append({"user": None, "timestamp": None, "assistant": []})
                turns[-1]["assistant"].append(message["content"])
        escaped_title = tex_escape(renderer.safe_text(title, context="Title"))
        header_title = title[:40] + "..." if len(title) > 40 else title
        escaped_header = escaped_title if header_title == title else tex_escape(renderer.safe_text(header_title, context="Title"))
        pieces = [self.preamble, "\\newcommand{\\CodexConversationTitle}{" + escaped_header + "}\n\\title{" + escaped_title + "}\n\\author{}\n\\date{}\n\\begin{document}\n\\maketitle\n"]
        times = []
        for name, value in (("Creation time", created_at), ("Modification time", modified_at)):
            try:
                times.append(tex_escape(self.formatTimestamp(value)))
            except TexExportError:
                renderer.warn(name + ": an unrepresentable timestamp was displayed as unknown")
                times.append("未知（原始时间不可表示）")
        pieces.append("\\begin{center}\n创建时间：" + times[0] + "\\\\\n最后修改时间：" + times[1] + "\n\\end{center}\n")
        pieces.append("\\tableofcontents\n\\clearpage\n")
        for number, turn in enumerate(turns, 1):
            try:
                question_time = tex_escape(self.formatTimestamp(turn["timestamp"], include_timezone=False))
            except TexExportError:
                renderer.warn(f"Round {number} user message: an unrepresentable timestamp was displayed as unknown")
                question_time = "未知（原始时间不可表示）"
            pieces.append("\\section{第 " + str(number) + " 轮}\n\\subsection*{用户提问\\quad{\\normalfont\\footnotesize\\textcolor{gray}{" + question_time + "}}}\n")
            pieces.append(renderer.render(turn["user"], context=f"Round {number} user message", localize_user_labels=True) if turn["user"] is not None else "（本轮未保留用户提问）\n")
            pieces.append("\\subsection*{AI 回答}\n")
            pieces.extend(renderer.render(answer, context=f"Round {number} assistant reply {reply}") for reply, answer in enumerate(turn["assistant"], 1))
            if not turn["assistant"]:
                pieces.append("（本轮尚无最终答复）\n")
        pieces.append("\\end{document}\n")
        return renderer.finish("".join(pieces))
