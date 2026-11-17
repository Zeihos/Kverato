"""Read-only readiness checks for the Discord release.

Never performs an economic command and never prints tokens, Discord IDs, or
individual balances. Safe to call from the owner's DM while the bot is live.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from contextlib import closing
import sqlite3

from PIL import Image

REQUIRED_TABLES = frozenset({
    'players', 'purchases', 'transfers', 'duels',
    'discord_preferences', 'discord_interactions', 'discord_friend_links',
})
REQUIRED_IMAGES = (
    'avatar_placeholder.png', 'wallpaper_glass.jpg',
    'icons/crown.png', 'icons/coins.png',
)


@dataclass(frozen=True)
class HealthReport:
    database_ok: bool
    schema_ok: bool
    foreign_keys_ok: bool
    assets_ok: bool
    missing_tables: tuple[str, ...]
    bad_assets: tuple[str, ...]
    details: str = ''

    @property
    def ready(self) -> bool:
        return all((self.database_ok, self.schema_ok,
                    self.foreign_keys_ok, self.assets_ok))

    def summary(self) -> str:
        mark = lambda value: '✅' if value else '❌'
        return (f'🛡 **КВЕРАТО — локальная самопроверка**\n'
                f'{mark(self.database_ok)} SQLite: целостность\n'
                f'{mark(self.schema_ok)} Игровая схема\n'
                f'{mark(self.foreign_keys_ok)} Связность данных\n'
                f'{mark(self.assets_ok)} Файлы графики\n'
                + ('⚠️ Требует исправления перед релизом.' if not self.ready
                   else '✅ Основные локальные компоненты исправны.'))


def inspect_release(db_path: str | Path, assets_dir: str | Path) -> HealthReport:
    """Non-mutating local preflight; failures return booleans, not secrets."""
    db_path, assets_dir = Path(db_path), Path(assets_dir)
    database_ok = schema_ok = foreign_keys_ok = False
    missing = tuple(sorted(REQUIRED_TABLES))
    details = ''
    if db_path.is_file():
        try:
            # `mode=ro` prevents a health check from creating an empty player DB.
            uri = db_path.resolve().as_uri() + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True, timeout=10)) as connection:
                database_ok = connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                tables = {r[0] for r in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                missing = tuple(sorted(REQUIRED_TABLES - tables))
                schema_ok = not missing
                foreign_keys_ok = connection.execute('PRAGMA foreign_key_check').fetchone() is None
        except (sqlite3.DatabaseError, OSError, ValueError) as exc:
            details = type(exc).__name__
    bad = []
    for relative in REQUIRED_IMAGES:
        path = assets_dir / relative
        try:
            if not path.is_file() or path.stat().st_size > 12_000_000:
                bad.append(relative)
                continue
            with Image.open(path) as picture:
                picture.verify()
        except (OSError, ValueError):
            bad.append(relative)
    return HealthReport(database_ok, schema_ok, foreign_keys_ok,
                        not bad, missing, tuple(bad), details)
