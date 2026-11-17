"""Plain-text Telegram presentation of platform-neutral game messages.

The original game core emits VK-style [idNUM|caption] tokens. Telegram's
sendMessage without parse_mode renders them verbatim. Use safe plain text
instead of inserting untrusted player names into HTML/Markdown markup.
"""
from __future__ import annotations
import re

_VK_MENTION = re.compile(r'\[id([1-9][0-9]{0,18})\|([^\]\n]{1,80})\]')


def format_text(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError('Expected plain text')
    return _VK_MENTION.sub(lambda m: f'{m.group(2)} (ID {m.group(1)})', text)
