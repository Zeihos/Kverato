"""Discord social presentation rules; pure Python, no network/library dependency."""
from __future__ import annotations

import re

PAGE_SIZE = 3
TABS = ('friends', 'incoming', 'outgoing')
LABELS = {'friends': 'Друзья', 'incoming': 'Входящие заявки', 'outgoing': 'Исходящие заявки'}
RESULT_TEXT = {
    'created': '✅ Заявка отправлена. Другой игрок может ответить через /friends.',
    'already': 'Этот игрок уже у тебя в друзьях.',
    'sent': 'Заявка этому игроку уже отправлена.',
    'incoming': 'У тебя уже есть входящая заявка от этого игрока. Открой /friends.',
    'self': 'Нельзя добавить самого себя.',
    'unknown': 'Оба игрока должны сначала зарегистрироваться через /start.',
    'unavailable': 'Заявка недоступна: один из игроков установил блокировку.',
    'cooldown': 'После удаления или отказа нужно подождать 24 часа.',
    'full': 'Достигнут лимит в 100 друзей.',
    'limit': 'Слишком много ожидающих заявок.',
    'daily_limit': 'Достигнут дневной лимит приглашений.',
    'accepted': '✅ Заявка принята. Теперь вы друзья!',
    'declined': 'Заявка отклонена.',
    'cancelled': 'Исходящая заявка отменена.',
    'removed': 'Игрок удалён из друзей.',
    'blocked': 'Игрок заблокирован: заявки и дружба отключены.',
    'unblocked': 'Игрок разблокирован.',
    'expired': 'Заявка устарела. Отправитель может создать новую.',
    'missing': 'Заявка или дружба уже неактуальна.',
    'invalid': 'Некорректные параметры.',
}


def safe_name(raw: object) -> str:
    """Remove Discord markdown/mention punctuation and all control characters."""
    name = re.sub(r'[\x00-\x1f\x7f@*_`~|>\\\[\]#]', ' ', str(raw or 'Игрок'))
    return ' '.join(name.split())[:42] or 'Игрок'


def page_data(overview: dict, tab: str = 'friends', page: int = 0) -> dict:
    if tab not in TABS:
        tab = 'friends'
    items = list(overview.get(tab, []))
    total = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    index = max(0, min(int(page), total - 1))
    selected = items[index * PAGE_SIZE:(index + 1) * PAGE_SIZE]
    counts = {key: len(overview.get(key, [])) for key in TABS}
    body = [f'👥 **{LABELS[tab]}** · страница {index + 1}/{total}',
            f'Друзей: {counts["friends"]} · Входящих: {counts["incoming"]} · Исходящих: {counts["outgoing"]}',
            '']
    if not selected:
        body.append('Пока ничего нет. Пригласи игрока командой /friend.')
    else:
        for number, (uid, name) in enumerate(selected, index * PAGE_SIZE + 1):
            body.append(f'{number}. {safe_name(name)} · ID `{uid}`')
    body.append('\nКнопки доступны только владельцу списка. Заявки действуют 7 дней.')
    return {'text': '\n'.join(body), 'items': selected, 'page': index,
            'pages': total, 'tab': tab, 'counts': counts}


def leaderboard_text(records: list[tuple[str, str, int, int]], owner: int | str, metric: str = 'xp') -> str:
    if metric not in ('xp', 'wins'):
        raise ValueError('Invalid metric')
    index = 2 if metric == 'xp' else 3
    header = '⭐ Опыт' if metric == 'xp' else '🏆 Победы'
    if not records:
        return f'👥 Рейтинг друзей · {header}\nНачни игру через /start.'
    lines = [f'👥 **Рейтинг друзей · {header}**', '']
    for rank, row in enumerate(records[:15], 1):
        lines.append(f'{rank}. {safe_name(row[1])} — {int(row[index]):,}'.replace(',', ' '))
    rank_owner = next((i + 1 for i, row in enumerate(records) if row[0] == str(owner)), None)
    if rank_owner and rank_owner > 15:
        own = records[rank_owner - 1]
        lines.append(f'...\n{rank_owner}. Ты — {int(own[index]):,}'.replace(',', ' '))
    lines.append('\nПоказаны только ты и подтверждённые друзья. Балансы скрыты.')
    return '\n'.join(lines)
