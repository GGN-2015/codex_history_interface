from dataclasses import dataclass
from typing import Literal

from .interface import D_ChatMessage


CONTEXT_TAGS = (
    "environment_context", "permissions instructions", "collaboration_mode",
    "app-context", "skills_instructions", "plugins_instructions", "user_instructions",
)
ATTACHMENTS = {
    "input_image": "[图片]", "image": "[图片]", "image_url": "[图片]",
    "input_audio": "[音频]", "audio": "[音频]", "input_file": "[附件]",
}
FINAL_PHASES = (None, "", "final", "final_answer")


@dataclass(frozen=True)
class MessageCandidate:
    role: Literal["user", "assistant"]
    content: str
    source: str

    def public(self) -> D_ChatMessage:
        return {"role": self.role, "content": self.content}


def strip_context(text: str) -> str:
    while text.strip():
        stripped = text.lstrip()
        closing = next((f"</{tag}>" for tag in CONTEXT_TAGS if stripped.startswith(f"<{tag}>")), None)
        if stripped.startswith("# AGENTS.md instructions for"):
            closing = "</INSTRUCTIONS>"
        if closing is None:
            return text
        boundary = stripped.find(closing)
        if boundary < 0:
            return ""
        text = stripped[boundary + len(closing):].lstrip("\r\n")
    return text


def content_text(content: object, role: str) -> str:
    if isinstance(content, str):
        return strip_context(content) if role == "user" else content
    if not isinstance(content, list):
        return ""
    pieces = []
    for part in content:
        if not isinstance(part, dict):
            continue
        kind = part.get("type")
        text = ""
        if kind in (None, "text", "input_text", "output_text") and isinstance(part.get("text"), str):
            text = part["text"]
        elif kind == "refusal" and isinstance(part.get("refusal"), str):
            text = part["refusal"]
        elif isinstance(kind, str) and kind in ATTACHMENTS:
            text = ATTACHMENTS[kind]
        if role == "user":
            text = strip_context(text)
        if text.strip():
            pieces.append(text)
    return "\n".join(pieces)


def visible_message(record: dict) -> MessageCandidate | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    kind = record.get("type")
    item_type = payload.get("type")
    if kind == "response_item" and item_type == "message":
        role = payload.get("role")
        source = "response"
    elif kind == "event_msg" and item_type in ("user_message", "agent_message"):
        role = "user" if item_type == "user_message" else "assistant"
        source = "event"
    elif kind == "event_msg" and item_type == "task_complete":
        role, source = "assistant", "completed"
    elif kind == "projection" and item_type in ("userMessage", "agentMessage"):
        role = "user" if item_type == "userMessage" else "assistant"
        source = "projection"
    else:
        return None
    if role not in ("user", "assistant"):
        return None
    if role == "assistant" and (
        payload.get("phase") not in FINAL_PHASES
        or payload.get("channel") not in FINAL_PHASES
        or payload.get("recipient") not in (None, "", "all")
    ):
        return None
    if source == "response" or (source == "projection" and role == "user"):
        text = content_text(payload.get("content"), role)
    else:
        key = "last_agent_message" if source == "completed" else "text" if source == "projection" else "message"
        text = payload.get(key)
        if not isinstance(text, str):
            return None
        if role == "user":
            text = strip_context(text)
            for field, placeholder in (("images", "[图片]"), ("audio", "[音频]")):
                media = payload.get(field) or payload.get("local_" + field)
                count = len(media) if isinstance(media, list) else 1 if media else 0
                if count:
                    text = "\n".join(([text] if text else []) + [placeholder] * count)
    if not text.strip():
        return None
    return MessageCandidate(role, text, source)


class MessageMerger:
    def __init__(self) -> None:
        self._counts: dict[tuple, dict[tuple[str, str], int]] = {}
        self._emitted: dict[tuple[str, str], int] = {}

    def accept(self, message: MessageCandidate, origin: str) -> bool:
        key = message.role, message.content
        counts = self._counts.setdefault((origin, message.source), {})
        counts[key] = counts.get(key, 0) + 1
        if counts[key] <= self._emitted.get(key, 0):
            return False
        self._emitted[key] = counts[key]
        return True
