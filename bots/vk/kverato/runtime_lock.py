"""Cross-platform OS-held single-instance lock for VK Long Poll.

The lock file stays on disk; OS releases its lock even after a process crash.
Lock beside the configured SQLite path, not beside the installation, to stop
two different bot installations using the same database at the same time.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class InstanceAlreadyRunningError(RuntimeError):
    pass


@contextmanager
def vk_instance_lock(database_path: str | Path) -> Iterator[None]:
    marker = Path(database_path).resolve().parent / 'kverato_vk.lock'
    marker.parent.mkdir(parents=True, exist_ok=True)
    with marker.open('a+b') as stream:
        # Ensure a byte exists for Windows' region locking.
        if marker.stat().st_size == 0:
            stream.write(b'\x00')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise InstanceAlreadyRunningError('КВЕРАТО VK уже запущен с этой базой данных.') from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, BlockingIOError) as exc:
                raise InstanceAlreadyRunningError('КВЕРАТО VK уже запущен с этой базой данных.') from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
