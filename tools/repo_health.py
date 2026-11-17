#!/usr/bin/env python3
"""Проверка публичного репозитория КВЕРАТО без токенов и внешних пакетов."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = {
    "vk": ("VK_TOKEN", "install_vk_windows.bat", "start_vk.bat",
           "start_vk_24x7.bat", "requirements-vk.txt"),
    "telegram": ("TELEGRAM_TOKEN", "install_tg_windows.bat", "start_tg.bat",
                 "start_tg_24x7.bat", "requirements-tg.txt"),
    "discord": ("DISCORD_TOKEN", "install_discord_windows.bat", "start_discord.bat",
                "start_discord_24x7.bat", "requirements-discord.txt"),
}
PROHIBITED_PARTS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".git", "backups", "data",
    ".idea", ".vscode", "node_modules", "venv",
}
PROHIBITED_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".pyc", ".pfx", ".p12", ".pem", ".key"}
TEXT_EXTENSIONS = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".bat", ".sh", ".ps1", ".html"}
CREDENTIAL_PATTERNS = {
    "Telegram": re.compile(r"(?<!\d)\d{8,12}:[A-Za-z0-9_-]{30,}"),
    "Discord": re.compile(r"(?<![\w.-])[A-Za-z0-9_-]{22,28}\.[A-Za-z0-9_-]{6,8}\.[A-Za-z0-9_-]{25,}(?![\w.-])"),
    "VK": re.compile(r"vk1\.a\.[A-Za-z0-9_-]{48,}"),
    "GitHub": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})\b"),
}


def tracked_paths() -> list[Path]:
    data = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [Path(part.decode("utf-8")) for part in data.split(b"\0") if part]


def main() -> int:
    problems = []
    files = tracked_paths()
    for relative in files:
        parts = PurePosixPath(relative.as_posix()).parts
        lower = tuple(part.lower() for part in parts)
        basename = relative.name.lower()
        suffix = relative.suffix.lower()
        if any(part in PROHIBITED_PARTS or part.startswith(".venv") for part in lower):
            problems.append(f"Приватный или временный каталог: {relative}")
        if (basename.startswith(".env") and basename != ".env.example") or suffix in PROHIBITED_EXTENSIONS:
            problems.append(f"Приватный файл отслеживается Git: {relative}")
        if basename.endswith((".sqlite3-wal", ".sqlite3-shm", ".sqlite3.sha256")):
            problems.append(f"Файл сохранений/резервной копии: {relative}")
        path = ROOT / relative
        if not path.is_file():
            problems.append(f"Отсутствует отслеживаемый файл: {relative}")
            continue
        if suffix == ".bat":
            data = path.read_bytes()
            if not data.startswith(b"@echo off"):
                problems.append(f"BAT без корректного заголовка: {relative}")
            if b"\n" in data.replace(b"\r\n", b""):
                problems.append(f"BAT не в Windows CRLF: {relative}")
        if suffix in TEXT_EXTENSIONS or basename == ".env.example":
            if path.stat().st_size > 2 * 1024 * 1024:
                problems.append(f"Неожиданно большой текстовый файл: {relative}")
                continue
            try:
                content = path.read_text(encoding="utf-8-sig")
            except UnicodeError:
                problems.append(f"Невозможно прочитать UTF-8: {relative}")
                continue
            for title, regex in CREDENTIAL_PATTERNS.items():
                if regex.search(content):
                    problems.append(f"Похоже на реальный {title}-токен: {relative}")

    for name, (token, installer, start, watchdog, requirements) in PLATFORMS.items():
        folder = ROOT / "bots" / name
        must_exist = [
            "main.py", ".env.example", installer, start, watchdog, requirements,
            "kverato/db.py", "kverato/service.py", "kverato/version.py",
            "kverato/image_cards.py",
        ]
        for filename in must_exist:
            if not (folder / filename).is_file():
                problems.append(f"{name}: отсутствует {filename}")
        sample = folder / ".env.example"
        if sample.is_file():
            values = [
                line.split("=", 1)[1].strip()
                for line in sample.read_text(encoding="utf-8-sig").splitlines()
                if line.startswith(token + "=")
            ]
            if values != [""]:
                problems.append(f"{name}: {token} в .env.example должен быть пустым")

    for candidate in (
        "bots/vk/.venv312/Scripts/python.exe",
        "bots/telegram/.venv312/Scripts/python.exe",
        "bots/discord/.venv/Scripts/python.exe",
        "bots/vk/.env",
        "bots/discord/data/players.sqlite3",
        "bots/telegram/backups/backup.sqlite3",
    ):
        check = subprocess.run(
            ["git", "check-ignore", "-q", "--", candidate],
            cwd=ROOT, check=False,
        )
        if check.returncode != 0:
            problems.append(f".gitignore не защищает: {candidate}")

    for problem in problems:
        print("ОШИБКА:", problem, file=sys.stderr)
    if problems:
        return 1
    bat_count = sum(path.suffix.lower() == ".bat" for path in files)
    print(f"OK: {len(files)} отслеживаемых файлов, {bat_count} BAT, три платформы, токены и базы защищены")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
