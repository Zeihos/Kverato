#!/usr/bin/env python3
"""Однократный безопасный импорт КВЕРАТО из релизных архивов в Git."""
from __future__ import annotations
import sys
from pathlib import Path
from release_audit import audit

MAPPING = {"VK": "vk", "Telegram": "telegram", "Discord": "discord"}

def main():
    root = Path(__file__).resolve().parents[1]
    dest = root / "bots"
    try:
        platforms = audit(root)
        if dest.exists() and any(dest.iterdir()):
            raise ValueError("Каталог bots уже существует; исходники НЕ перезаписаны")
        for platform, files in platforms.items():
            for name, data in files.items():
                path = dest / MAPPING[platform] / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            print(f"Импорт {platform}: {len(files)} файлов")
        print("Готово: исходники трех платформ перенесены в bots/")
        return 0
    except (OSError, ValueError, SyntaxError) as error:
        print(f"ОШИБКА: {error}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
