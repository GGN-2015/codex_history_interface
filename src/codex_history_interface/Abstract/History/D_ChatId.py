class D_ChatId(str):
    """Immutable conversation ID value, compatible with existing string-ID APIs and JSON."""

    __slots__ = ()

    def __new__(cls, value: str) -> "D_ChatId":
        if not isinstance(value, str):
            raise TypeError("chat ID must be a string")
        if not value.strip():
            raise ValueError("chat ID must not be empty or whitespace-only")
        return super().__new__(cls, value)

    @property
    def value(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str.__repr__(self)})"
