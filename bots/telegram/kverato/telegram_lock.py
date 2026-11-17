"""OS-held Telegram single-instance lock; released on process exit/crash."""
from __future__ import annotations
import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def telegram_instance_lock(path: str | Path):
    filename = Path(path).resolve().parent / 'kverato_telegram.lock'
    filename.parent.mkdir(parents=True, exist_ok=True)
    with filename.open('a+b') as fp:
        if filename.stat().st_size == 0:
            fp.write(b'0')
            fp.flush()
        fp.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise AlreadyRunning('Telegram-бот КВЕРАТО уже работает с этой базой.') from None
            try:
                yield
            finally:
                fp.seek(0)
                msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError):
                raise AlreadyRunning('Telegram-бот КВЕРАТО уже работает с этой базой.') from None
            try:
                yield
            finally:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
