"""Safe SQLite snapshots for migrations and live backups.

SQLite's backup API includes committed transactions from WAL databases. Files
are staged and verified before atomic replacement, without copying -wal/-shm.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def file_sha256(path: str | Path) -> str:
    """Compute SHA-256 in chunks: backups may be much larger than RAM."""
    hasher = hashlib.sha256()
    with open(path, 'rb') as stream:
        while block := stream.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def verify_sqlite(path: str | Path) -> tuple[bool, str]:
    path = Path(path)
    if not path.is_file():
        return False, 'SQLite database does not exist'
    try:
        with closing(sqlite3.connect(f'{path.resolve().as_uri()}?mode=ro', uri=True)) as con:
            check = con.execute('PRAGMA integrity_check').fetchone()
            if not check or check[0] != 'ok':
                return False, 'PRAGMA integrity_check failed'
            broken = con.execute('PRAGMA foreign_key_check').fetchone()
            if broken:
                return False, 'PRAGMA foreign_key_check failed'
    except sqlite3.Error:
        return False, 'Unable to read/validate SQLite database'
    return True, 'ok'


def verify_checksum(path: str | Path) -> bool:
    """Verify the companion hash when present; no hash means no verification."""
    file = Path(path)
    sidecar = file.with_name(file.name + '.sha256')
    if not sidecar.is_file():
        return False
    try:
        expected = sidecar.read_text(encoding='ascii').split()[0].lower()
        return len(expected) == 64 and expected == file_sha256(file)
    except (OSError, UnicodeError, IndexError):
        return False


def snapshot(source: str | Path, dest: str | Path) -> tuple[Path, str]:
    """Create a verified SQLite backup and .sha256 hash file; no source writes."""
    source = Path(source).resolve()
    dest = Path(dest).resolve()
    if not source.is_file():
        raise FileNotFoundError('Source SQLite database is missing')
    if source == dest:
        raise ValueError('Backup destination must differ from source')
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix='.kverato_tmp_', suffix='.sqlite3')
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with closing(sqlite3.connect(f'{source.as_uri()}?mode=ro', uri=True)) as live:
            with closing(sqlite3.connect(tmp)) as backup:
                live.backup(backup)
                backup.execute('PRAGMA journal_mode=DELETE')
        ok, reason = verify_sqlite(tmp)
        if not ok:
            raise RuntimeError('Backup verification failed: ' + reason)
        digest = file_sha256(tmp)
        os.replace(tmp, dest)
        dest.with_name(dest.name + '.sha256').write_text(digest + '  ' + dest.name + '\n', encoding='ascii')
        return dest, digest
    finally:
        tmp.unlink(missing_ok=True)
        for suffix in ('-wal', '-shm'):
            tmp.with_name(tmp.name + suffix).unlink(missing_ok=True)


def daily_prestart_backup(source: str | Path, backup_dir: str | Path,
                          *, now: datetime | None = None, keep: int = 14) -> Path | None:
    """Protect the pre-migration database once per UTC day (max `keep` copies)."""
    source = Path(source)
    if not source.is_file():
        return None  # First installation has no user data.
    current = now or datetime.now(timezone.utc)
    directory = Path(backup_dir)
    destination = directory / f'kverato_prestart_{current:%Y%m%d}.sqlite3'
    if destination.is_file():
        ok, why = verify_sqlite(destination)
        if not ok or not verify_checksum(destination):
            raise RuntimeError('Existing daily backup invalid or hash mismatch: ' + why)
    else:
        snapshot(source, destination)
    old = sorted(directory.glob('kverato_prestart_????????.sqlite3'))
    for item in old[:-keep]:
        item.unlink()
        item.with_name(item.name + '.sha256').unlink(missing_ok=True)
    return destination
