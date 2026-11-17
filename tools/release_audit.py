#!/usr/bin/env python3
"""Проверка публичных релизных архивов КВЕРАТО без токенов."""
from __future__ import annotations
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

RELEASES = {
    "VK": ("KVERATO-VK-v1.0-FINAL.zip", "VK"),
    "Telegram": ("KVERATO-TG-v1.0-FINAL.zip", "TG"),
    "Discord": ("KVERATO-DISCORD-v1.0-FINAL.zip", "DISCORD"),
}
FORBIDDEN = {".env", ".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}
FORBIDDEN_EXT = {".sqlite", ".sqlite3", ".db", ".pyc", ".pem", ".key"}

def read_zip(path: Path):
    files, seen, total = {}, set(), 0
    with zipfile.ZipFile(path) as z:
        for item in z.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith("/") or "\\" in name or
                any(p in (".", "..") or ":" in p or p.endswith((" ", ".")) for p in parts)):
                raise ValueError(f"Недопустимый путь: {name}")
            key = name.rstrip("/").casefold()
            if key in seen:
                raise ValueError(f"Дублированный путь: {name}")
            seen.add(key)
            if any(p.casefold() in FORBIDDEN for p in parts):
                raise ValueError(f"Недопустимый файл: {name}")
            if stat.S_IFMT(item.external_attr >> 16) == stat.S_IFLNK:
                raise ValueError(f"Символическая ссылка в ZIP: {name}")
            total += item.file_size
            if total > 80 * 1024 * 1024:
                raise ValueError(f"Слишком большой архив: {path.name}")
            if item.is_dir():
                continue
            if PurePosixPath(name).suffix.lower() in FORBIDDEN_EXT:
                raise ValueError(f"База / секрет / кэш: {name}")
            files[name] = z.read(item)
    return files

def audit(root: Path):
    platforms = {}
    for label, (filename, tag) in RELEASES.items():
        prefix = f"KVERATO-{tag}-v1.0/"
        raw = read_zip(root / "releases" / "v1.0" / filename)
        if any(not name.startswith(prefix) for name in raw):
            raise ValueError(f"Посторонние файлы в архиве {label}")
        files = {name[len(prefix):]: data for name, data in raw.items()}
        required = {"main.py", ".env.example", "kverato/version.py",
                    "kverato/service.py", "kverato/db.py", "kverato/image_cards.py"}
        if not required.issubset(files):
            raise ValueError(f"{label}: отсутствуют обязательные исходники")
        token = {"VK": "VK_TOKEN", "Telegram": "TELEGRAM_TOKEN",
                 "Discord": "DISCORD_TOKEN"}[label]
        config = files[".env.example"].decode("utf-8-sig")
        lines = [x for x in config.splitlines() if x.strip().startswith(token + "=")]
        if len(lines) != 1 or lines[0].split("=", 1)[1].strip():
            raise ValueError(f"{label}: шаблон токена должен быть пустым")
        if "1.0" not in files["kverato/version.py"].decode("utf-8-sig"):
            raise ValueError(f"{label}: неверная версия")
        n = 0
        for name, data in files.items():
            if name.endswith(".py"):
                compile(data.decode("utf-8-sig"), name, "exec")
                n += 1
            if name.lower().endswith(".bat") and b"\n" in data.replace(b"\r\n", b""):
                raise ValueError(f"{label}: BAT не в CRLF: {name}")
        if n < 15:
            raise ValueError(f"{label}: недостаточно Python-модулей")
        platforms[label] = files
        print(f"OK {label}: {len(files)} файлов, {n} Python-модулей")
    full = read_zip(root / "releases" / "v1.0" / "KVERATO-v1.0-FULL-RELEASE.zip")
    for label, originals in platforms.items():
        prefix = f"KVERATO-v1.0-FULL/{label}/"
        extracted = {name[len(prefix):]: data for name, data in full.items()
                     if name.startswith(prefix)}
        if extracted != originals:
            raise ValueError(f"{label}: полный и отдельный архивы различаются")
    print("OK: полный ZIP идентичен отдельным архивам")
    return platforms

if __name__ == "__main__":
    try:
        audit(Path(__file__).resolve().parents[1])
    except (OSError, ValueError, SyntaxError, UnicodeError, zipfile.BadZipFile) as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        raise SystemExit(1)
