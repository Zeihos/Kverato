#!/usr/bin/env python3
"""Проверка локальных ссылок в документации КВЕРАТО."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
DOCS = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "SECURITY.md"]
DOCS += sorted((ROOT / "docs").rglob("*.md"))
MD = re.compile(r'!?\[[^\]\n]*\]\(([^)\n]+)\)')
HTML = re.compile(r'<(?:img|a)\b[^>]*?\b(?:src|href)=["\']([^"\']+)["\']', re.I)
PROTOCOL = re.compile(r'^[a-z][a-z0-9+.-]*:', re.I)


def main() -> int:
    errors, checked = [], 0
    for doc in DOCS:
        if not doc.is_file():
            errors.append(f"Нет документа: {doc.relative_to(ROOT)}")
            continue
        text = doc.read_text(encoding="utf-8-sig")
        links = [match.group(1).strip().split(" ", 1)[0].strip("<>")
                 for match in MD.finditer(text)]
        links += [match.group(1) for match in HTML.finditer(text)]
        for url in links:
            if not url or url.startswith(("#", "//")) or PROTOCOL.match(url):
                continue
            target = (doc.parent / unquote(urlsplit(url).path)).resolve()
            try:
                target.relative_to(ROOT.resolve())
            except ValueError:
                errors.append(f"Путь выходит за пределы проекта: {url}")
                continue
            if not target.exists():
                errors.append(f"{doc.relative_to(ROOT)}: нет {url}")
            else:
                checked += 1
    for problem in errors:
        print("ОШИБКА:", problem, file=sys.stderr)
    if errors:
        return 1
    print(f"OK: {len(DOCS)} документов, {checked} локальных ссылок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
