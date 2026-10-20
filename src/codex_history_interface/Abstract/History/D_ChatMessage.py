from typing import Literal, TypedDict


class D_ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str
