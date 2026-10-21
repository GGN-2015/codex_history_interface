from abc import ABC, abstractmethod
from pathlib import Path
from re import Pattern


class I_SearchConfig(ABC):
    """Configuration fields for tokenization and the persistent search index."""

    _index_directory: Path
    _schema_version: int
    _sqlite_timeout: float
    _sqlite_write_cache_kib: int
    _lock_timeout: float
    _lock_poll_interval: float
    _cjk_ranges: str
    _token_pattern: Pattern[str]
    _cjk_pattern: Pattern[str]
    _default_max_count: int

    @abstractmethod
    def getIndexDirectory(self) -> Path:
        raise NotImplementedError

    @abstractmethod
    def getSchemaVersion(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def getSqliteTimeout(self) -> float:
        raise NotImplementedError

    def getSqliteWriteCacheKiB(self) -> int:
        return 64 * 1024

    @abstractmethod
    def getLockTimeout(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def getLockPollInterval(self) -> float:
        raise NotImplementedError

    @abstractmethod
    def getCjkRanges(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def getTokenPattern(self) -> Pattern[str]:
        raise NotImplementedError

    @abstractmethod
    def getCjkPattern(self) -> Pattern[str]:
        raise NotImplementedError

    @abstractmethod
    def getDefaultMaxCount(self) -> int:
        raise NotImplementedError
