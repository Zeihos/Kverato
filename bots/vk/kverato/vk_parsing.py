"""Safe VK text parsing for numeric user mentions.

VK mention syntax: [id123|Имя] or @id123 (Имя).
Ignore mentions of communities (club, public, event) and negative ids.
"""
from __future__ import annotations

import re

MENTION = re.compile(r'(?:\[id([1-9]\d{0,18})\|[^\]\r\n]{1,200}\]|(?<![\w])@id([1-9]\d{0,18})(?!\d))', re.IGNORECASE)


def vk_mentioned_user(text: str) -> str | None:
    """Target only the first VK user mention AFTER the command."""
    tail = text.partition(' ')[2]
    match = MENTION.search(tail)
    return (match.group(1) or match.group(2)) if match else None


def invited_member_id(action, author_id: int) -> int | None:
    """Find joined VK member from a trusted service action, otherwise None."""
    kind = getattr(action, 'type', None)
    kind = getattr(kind, 'value', kind)
    if kind not in ('chat_invite_user', 'chat_invite_user_by_link'):
        return None
    member = getattr(action, 'member_id', None)
    if isinstance(member, int) and 0 < member <= 10**19:
        return member
    if kind == 'chat_invite_user_by_link' and isinstance(author_id, int) and author_id > 0:
        return author_id
    return None
