import os
from pathlib import Path
from typing import Iterable

from ..Abstract import D_ChatId, D_ChatMessage, I_CodexInterface, I_HistoryRepository, I_TexExporter
from .Export.C_TexExporter import C_TexExporter
from .History.C_HistoryRepository import C_HistoryRepository
from .History.C_HistoryStore import C_HistoryStore


class C_CodexInterface(I_CodexInterface):
    """Compatible facade composed from a history repository and a TeX exporter."""

    def __init__(
        self,
        codex_home: str | os.PathLike | None = None,
        *,
        databases: Iterable[str | os.PathLike] = (),
        repository: I_HistoryRepository | None = None,
        tex_exporter: I_TexExporter | None = None,
    ) -> None:
        databases = tuple(databases)
        if repository is not None and (codex_home is not None or databases):
            raise ValueError("Pass either repository or codex_home/databases, not both")
        self._repository = repository if repository is not None else C_HistoryRepository(codex_home, databases=databases)
        self._tex_exporter = tex_exporter if tex_exporter is not None else C_TexExporter()

    @property
    def repository(self) -> I_HistoryRepository:
        return self._repository

    @property
    def tex_exporter(self) -> I_TexExporter:
        return self._tex_exporter

    @property
    def codex_home(self) -> Path:
        return self._repository.codex_home

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._repository.diagnostics

    @property
    def _store(self) -> C_HistoryStore:
        if isinstance(self._repository, C_HistoryRepository):
            return self._repository._store
        raise AttributeError("The injected repository does not expose a local history store")

    def getChatList(self) -> list[str]:
        return self._repository.getChatList()

    def getChatListInTimeRange(self, timestamp1: float, timestamp2: float) -> list[str]:
        return self._repository.getChatListInTimeRange(timestamp1, timestamp2)

    def roughMatch(self, content: str, maxCnt: int) -> list[D_ChatId]:
        return self._repository.roughMatch(content, maxCnt)

    def getChatTitle(self, chat_id: str) -> str:
        return self._repository.getChatTitle(chat_id)

    def getChatMessage(self, chat_id: str) -> list[D_ChatMessage]:
        return self._repository.getChatMessage(chat_id)

    def getLastMessageTimeStamp(self, chat_id: str) -> float | None:
        return self._repository.getLastMessageTimeStamp(chat_id)

    def getCreateTimeStamp(self, chat_id: str) -> float | None:
        return self._repository.getCreateTimeStamp(chat_id)

    def exportToTex(
        self,
        chat_id: str,
        output_path: str | os.PathLike,
        *,
        overwrite: bool = False,
    ) -> Path:
        return self._tex_exporter.exportToTex(self._repository, chat_id, output_path, overwrite=overwrite)
