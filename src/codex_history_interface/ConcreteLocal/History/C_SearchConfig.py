from pathlib import Path
import os
import re
from re import Pattern

from ...Abstract.History.I_SearchConfig import I_SearchConfig


class C_SearchConfig(I_SearchConfig):
    def __init__(self, index_directory: str | Path | None = None) -> None:
        configured = os.environ.get("CODEX_HISTORY_INDEX_DIR", "")
        directory = index_directory if index_directory is not None else configured if configured.strip() else self.getDefaultIndexDirectory()
        self._index_directory = Path(directory).expanduser().resolve()
        self._schema_version = 1
        self._sqlite_timeout = 30.0
        self._sqlite_write_cache_kib = super().getSqliteWriteCacheKiB()
        self._lock_timeout = 30.0
        self._lock_poll_interval = 0.05
        self._cjk_ranges = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af\U00020000-\U0003134f"
        self._token_pattern = re.compile(f"[{self._cjk_ranges}]+|[^\\W_{self._cjk_ranges}]+")
        self._cjk_pattern = re.compile(f"[{self._cjk_ranges}]")
        self._default_max_count = 10

    @staticmethod
    def getDefaultIndexDirectory() -> Path:
        return Path.home() / ".codex_history_interface" / "indexes"

    def getIndexDirectory(self) -> Path:
        return self._index_directory

    def getSchemaVersion(self) -> int:
        return self._schema_version

    def getSqliteTimeout(self) -> float:
        return self._sqlite_timeout

    def getSqliteWriteCacheKiB(self) -> int:
        return self._sqlite_write_cache_kib

    def getLockTimeout(self) -> float:
        return self._lock_timeout

    def getLockPollInterval(self) -> float:
        return self._lock_poll_interval

    def getCjkRanges(self) -> str:
        return self._cjk_ranges

    def getTokenPattern(self) -> Pattern[str]:
        return self._token_pattern

    def getCjkPattern(self) -> Pattern[str]:
        return self._cjk_pattern

    def getDefaultMaxCount(self) -> int:
        return self._default_max_count
