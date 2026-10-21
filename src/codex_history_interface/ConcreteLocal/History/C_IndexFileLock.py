from contextlib import contextmanager
import errno
import math
import os
from pathlib import Path
from time import monotonic, sleep
from typing import Iterator

from ...Abstract.History.I_SearchConfig import I_SearchConfig
from ...errors import HistoryUnavailableError


class C_IndexFileLock:
    def __init__(self, path: Path, config: I_SearchConfig) -> None:
        self._path = path
        self._config = config

    @staticmethod
    def _tryLock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(descriptor: int) -> None:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)

    @contextmanager
    def acquire(self) -> Iterator[None]:
        timeout = self._config.getLockTimeout()
        interval = self._config.getLockPollInterval()
        if not math.isfinite(timeout) or timeout < 0 or not math.isfinite(interval) or interval <= 0:
            raise ValueError("Lock timeout must be finite and non-negative; poll interval must be finite and positive")
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            deadline = monotonic() + timeout
            while True:
                try:
                    self._tryLock(descriptor)
                    break
                except OSError as error:
                    if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK):
                        raise HistoryUnavailableError(f"Cannot lock search index {self._path}: {error}") from error
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise HistoryUnavailableError(f"Timed out waiting for search index lock {self._path} after {timeout} seconds") from error
                    sleep(min(interval, remaining))
            try:
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                yield
            finally:
                self._unlock(descriptor)
        finally:
            os.close(descriptor)
