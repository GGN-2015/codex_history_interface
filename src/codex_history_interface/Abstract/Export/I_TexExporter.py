from abc import ABC, abstractmethod
from os import PathLike
from pathlib import Path

from ..History.I_HistoryRepository import I_HistoryRepository


class I_TexExporter(ABC):
    """Export visible history to TeX without discovering or running compilers."""

    @abstractmethod
    def exportToTex(
        self,
        repository: I_HistoryRepository,
        chat_id: str,
        output_path: str | PathLike,
        *,
        overwrite: bool = False,
    ) -> Path:
        raise NotImplementedError
