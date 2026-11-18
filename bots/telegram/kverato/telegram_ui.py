"""Telegram navigation and private display preferences.

All callback actions are allowlisted; economy-changing commands are never
buttons. Only per-player presentation settings may be changed inline.
"""
from __future__ import annotations

from .service import ALIASES
from .image_cards import CARD_COMMANDS

SECTION_TITLES = {
    'main': '👑 КВЕРАТО | Главное меню',
    'profile': '👤 Персонаж',
    'economy': '💰 Экономика',
    'games': '🎮 Игры и соревнования',
    'world': '🌆 Город и кланы',
    'shop': '🛍 Магазины и коллекции',
    'ratings': '🏆 Рейтинги',
    'catalog': '📚 Каталоги и производство',
    'social': '🤝 Игроки и дуэли',
    'friends': '👥 Друзья КВЕРАТО',
    'friendtop': '🏆 Рейтинг друзей КВЕРАТО',
    'settings': '⚙ Настройки Telegram',
}

# target: @section for navigation, canonical core command otherwise.
BUTTONS = {
    'main': (
        ('👤 Профиль', 'profile'), ('💰 Баланс', 'balance'),
        ('🎁 Награды', 'rewards'), ('👤 Персонаж', '@profile'), ('💰 Экономика', '@economy'),
        ('🎮 Игры', '@games'), ('🤝 Игроки', '@social'),
        ('🌆 Город', '@world'),
        ('🛍 Покупки', '@shop'), ('🏆 Рейтинги', '@ratings'),
        ('📚 Каталоги', '@catalog'), ('🌠 Сезон', 'season'),
        ('📖 Помощь', 'help'), ('⚙ Настройки', '@settings'),
        ('📜 Правила', 'rules'), ('🔐 Данные', 'privacy'),
    ),
    'profile': (
        ('👤 Профиль', 'profile'), ('⭐ Уровень', 'level'),
        ('📊 Статистика', 'stats'), ('🏅 Достижения', 'achievements'),
        ('🎒 Инвентарь', 'inventory'), ('🎖 Титулы', 'titles'),
        ('🐾 Питомцы', 'pets'), ('👕 Гардероб', 'wardrobe'),
        ('⬅ Главное меню', '@main'),
    ),
    'economy': (
        ('💰 Баланс', 'balance'), ('🏦 Банк', 'bank'),
        ('🎁 Награды и таймеры', 'rewards'),
        ('💼 Профессии', 'jobs'), ('🏢 Бизнесы', 'businesses'),
        ('🏠 Недвижимость', 'home'), ('🚘 Транспорт', 'vehicle'),
        ('📦 Ресурсы', 'materials'), ('🏭 Производство', 'chains'),
        ('⬅ Главное меню', '@main'),
    ),
    'games': (
        ('🎮 Игры', 'games'), ('🏁 Гонки', 'race'),
        ('🏆 Турниры', 'tournament'), ('📊 Победы', 'topwins'),
        ('👑 Кланы', 'clan'), ('🌠 Сезон', 'season'),
        ('🎲 Контейнеры', 'boxes'), ('⬅ Главное меню', '@main'),
    ),
    'world': (
        ('🌆 Город', 'city'), ('🧭 Экспедиции', 'expedition'),
        ('👑 Кланы', 'clan'), ('🏰 Зал славы', 'hall'),
        ('🌠 Сезон', 'season'), ('🗺 Районы', 'district'),
        ('⬅ Главное меню', '@main'),
    ),
    'shop': (
        ('🛍 Магазин', 'shop'), ('🐾 Питомцы', 'pets'),
        ('🎁 Контейнеры', 'boxes'), ('👕 Гардероб', 'wardrobe'),
        ('💎 Коллекции', 'sets'), ('🏷 Титулы', 'titles'),
        ('🛒 Рынок', 'market'), ('⬅ Главное меню', '@main'),
    ),
    'ratings': (
        ('💰 ТОП монет', 'top'), ('⭐ ТОП опыта', 'topxp'),
        ('🏆 ТОП побед', 'topwins'), ('❤️ ТОП репутации', 'toprep'),
        ('🏁 ТОП гонщиков', 'racetop'), ('👑 ТОП кланов', 'clantop'),
        ('🌠 ТОП сезона', 'seasontop'), ('⬅ Главное меню', '@main'),
    ),
    'social': (
        ('👥 Друзья', '@friends'), ('🏆 Рейтинг друзей', '@friendtop'),
        ('⚔️ Мои дуэли', 'duel'), ('🤝 Моя репутация', 'rep'),
        ('📜 История дуэлей', 'duelhistory'), ('⬅ Главное меню', '@main'),
    ),
    'settings': (('⬅ Главное меню', '@main'),),
    'friends': (('🏆 Рейтинг друзей', '@friendtop'), ('⬅ Игроки и дуэли', '@social'), ('⬅ Главное меню', '@main')), 
    'catalog': (
        ('📚 Общий каталог', 'catalog'), ('💼 Профессии', 'jobs'),
        ('🏢 Бизнесы', 'businesses'), ('📦 Ресурсы', 'materials'),
        ('🛠 Крафт', 'craft'), ('🔧 Снаряжение', 'equipment'),
        ('🚚 Доставка', 'delivery'), ('📋 Заказы', 'orders'),
        ('📜 Контракты', 'contracts'), ('⬅ Главное меню', '@main'),
    ),
}

# Expedition with zero arguments shows status only (never starts a run).
SAFE_COMMANDS = frozenset(CARD_COMMANDS) | frozenset({'expedition', 'duel', 'rep', 'duelhistory', 'rules', 'privacy', 'rewards'})
MENU_ALIASES = frozenset({'menu', 'меню'})


def keyboard(section: str = 'main') -> dict:
    actions = BUTTONS.get(section, BUTTONS['main'])
    rows: list[list[dict]] = []
    for i in range(0, len(actions), 2):
        row = []
        for label, target in actions[i:i + 2]:
            if target.startswith('@'):
                name = target[1:]
                if name not in SECTION_TITLES:
                    continue
                data = 'kv:s:' + name
            else:
                if target not in SAFE_COMMANDS:
                    continue
                data = 'kv:c:' + target
            assert len(data.encode('utf-8')) <= 64
            row.append({'text': label, 'callback_data': data})
        if row:
            rows.append(row)
    return {'inline_keyboard': rows}


def callback_action(data: object) -> tuple[str, str] | None:
    if not isinstance(data, str) or len(data.encode('utf-8')) > 64:
        return None
    if data in ('kv:p:cards:0', 'kv:p:cards:1', 'kv:p:avatar:0', 'kv:p:avatar:1'):
        return ('preference', data[5:])
    if data in ('kv:t:xp', 'kv:t:wins'):
        return ('friendtop', data[5:])
    if data.startswith('kv:l:'):
        import re
        found = re.fullmatch(r'kv:l:([fio]):(0|[1-9][0-9]{0,1})', data)
        if found:
            section = {'f':'friends','i':'incoming','o':'outgoing'}[found.group(1)]
            return ('friendlist', section + ':' + str(int(found.group(2))))
        return None
    if data.startswith('kv:u:'):
        import re
        found = re.fullmatch(r'kv:u:([1-9][0-9]{0,18})', data)
        return ('friendprofile', found.group(1)) if found else None
    if data.startswith('kv:f:'):
        import re
        found = re.fullmatch(r'kv:f:([adcx]):([1-9][0-9]{0,18})', data)
        if found:
            return ('friend', {'a':'accept','d':'decline','c':'cancel','x':'remove'}[found.group(1)] + ':' + found.group(2))
        return None
    if data.startswith('kv:d:'):
        import re
        found = re.fullmatch(r'kv:d:([arc]):([1-9][0-9]{0,17})', data)
        if found:
            verbs = {'a': 'acceptduel', 'r': 'declineduel', 'c': 'cancelduel'}
            return ('duel', f'{verbs[found.group(1)]}:{found.group(2)}')
        return None
    if data.startswith('kv:s:'):
        name = data[5:]
        return ('section', name) if name in SECTION_TITLES else None
    if data.startswith('kv:c:'):
        name = data[5:]
        return ('command', name) if name in SAFE_COMMANDS else None
    # Old buttons in messages from Telegram alpha remain usable after an upgrade.
    if data.startswith('kv:'):
        name = data[3:]
        if name in SAFE_COMMANDS:
            return ('command', name)
        if name == 'menu':
            return ('section', 'main')
    return None


def callback_command(data: object) -> str | None:
    """Backwards-compatible helper; returns only whitelisted informational commands."""
    action = callback_action(data)
    return action[1] if action and action[0] == 'command' else None


def duel_status_keyboard(number: int, *, challenger: bool) -> dict:
    """Show only actions available to the current player in this chat."""
    full = duel_keyboard(number)['inline_keyboard']
    return {'inline_keyboard': [full[1]] if challenger else [full[0]]}


def duel_keyboard(number: int) -> dict:
    """Only game-core-owned duel IDs: approval and settlement happen in the core."""
    if not isinstance(number, int) or not 1 <= number <= 999999999999999999:
        raise ValueError('Invalid duel number')
    return {'inline_keyboard': [
        [{'text': '✅ Принять', 'callback_data': f'kv:d:a:{number}'},
         {'text': '❌ Отклонить', 'callback_data': f'kv:d:r:{number}'}],
        [{'text': '↩ Отменить вызов', 'callback_data': f'kv:d:c:{number}'}],
    ]}


def preferences_text(settings: dict[str, bool]) -> str:
    return ('⚙ КВЕРАТО | Настройки Telegram\n\n'
            f'🖼 Графические карточки: {"включены" if settings["cards"] else "выключены"}\n'
            f'👤 Аватар в карточках: {"включён" if settings["avatar"] else "выключен"}\n\n'
            'Выбор сохраняется после перезапуска. Настройки влияют только на твои ответы, '
            'не меняют баланс или игровой прогресс.')


def preferences_keyboard(settings: dict[str, bool]) -> dict:
    cards = settings['cards']
    avatar = settings['avatar']
    return {'inline_keyboard': [
        [{'text': '🖼 Выключить карточки' if cards else '🖼 Включить карточки',
          'callback_data': 'kv:p:cards:' + ('0' if cards else '1')}],
        [{'text': '👤 Убрать аватар' if avatar else '👤 Показывать аватар',
          'callback_data': 'kv:p:avatar:' + ('0' if avatar else '1')}],
        [{'text': '⬅ Главное меню', 'callback_data': 'kv:s:main'}],
    ]}


def menu_text(section: str = 'main') -> str:
    section = section if section in SECTION_TITLES else 'main'
    note = ('Выбирай разделы кнопками ниже. Для действий с монетами '
            'и предметами используй игровые команды.')
    if section != 'main':
        note = ('Выбери нужную информацию. Кнопки не тратят монеты и '
                'не выполняют игровые покупки.')
    if section == 'social':
        note = ('Ответь на сообщение игрока командой /player, чтобы посмотреть его публичный профиль.\n'
                'Дуэль: !дуэль 50 ответом на сообщение соперника.\n'
                'Проверить свои вызовы: /duels в группе.\n'
                'Принятие и отказ — кнопками под вызовом.\n'
                'Перевод: !перевести 100 ответом на сообщение (без кнопок, меняющих баланс).\n'
                'Друзья: /friends в личном чате, /friend ID для приглашения.\n'
                'Рейтинг друзей: /friendtop.')
    if section == 'friendtop':
        note = 'Сравни свой опыт и победы с принятыми друзьями через /friendtop.'
    if section == 'friends':
        note = 'Заявки и список друзей доступны только в личном чате: /friends'
    return f'{SECTION_TITLES[section]}\n\n{note}\n\n📖 /help — команды  •  🆔 /id — твой ID  •  ⚙ /settings'


def section_for(command: str | None) -> str:
    if command in ('profile', 'stats', 'achievements', 'inventory', 'titles', 'outfit', 'level', 'pets', 'wardrobe'):
        return 'profile'
    if command in ('duel', 'duelhistory', 'rep', 'giverep'):
        return 'social'
    if command in ('games', 'topwins', 'race', 'tournament', 'clan', 'boxes'):
        return 'games'
    if command in ('economy', 'bank', 'jobs', 'businesses', 'materials', 'home', 'vehicle', 'chains'):
        return 'economy'
    if command in ('city', 'expedition', 'season', 'hall', 'district', 'clanexpedition'):
        return 'world'
    if command in ('shop', 'sets', 'market'):
        return 'shop'
    if command in ('top', 'topxp', 'toprep', 'racetop', 'clantop', 'seasontop'):
        return 'ratings'
    if command in ('catalog', 'craft', 'equipment', 'delivery', 'orders', 'contracts'):
        return 'catalog'
    return 'main'


def split_text(text: str, limit: int = 3900) -> list[str]:
    if not text:
        return []
    chunks = []
    while len(text) > limit:
        cut = text.rfind('\n', 0, limit + 1)
        if cut < max(200, limit // 3):
            cut = limit
        else:
            cut += 1
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def parse_command(text: str, *, group: bool, bot_username: str = '') -> tuple[str | None, str]:
    """Extract command without accepting commands intended for other bots."""
    trimmed = text.strip()
    if not trimmed:
        return None, trimmed
    head = trimmed.split()[0]
    if group and not head.startswith(('/', '!', '.')):
        return None, trimmed
    clean = head.lstrip('/!.')
    if '@' in clean:
        name, recipient = clean.split('@', 1)
        if not bot_username or recipient.casefold() != bot_username.casefold():
            return None, ''
        trimmed = head.split('@', 1)[0] + trimmed[len(head):]
        clean = name
    return ALIASES.get(clean.casefold()), trimmed


def friends_keyboard(incoming: list[tuple[str, str]], outgoing: list[tuple[str, str]]) -> dict:
    """State-changing friend buttons always revalidate actor and current DB row."""
    rows: list[list[dict]] = []
    for uid, _ in incoming[:5]:
        if uid.isdigit() and len(uid) <= 19:
            rows.append([{'text': f'✅ Принять {uid}', 'callback_data': f'kv:f:a:{uid}'},
                         {'text': '❌', 'callback_data': f'kv:f:d:{uid}'}])
    for uid, _ in outgoing[:5]:
        if uid.isdigit() and len(uid) <= 19:
            rows.append([{'text': f'↩ Отменить {uid}', 'callback_data': f'kv:f:c:{uid}'}])
    rows.append([{'text': '🤝 Друзья', 'callback_data': 'kv:l:f:0'},
                 {'text': '📩 Входящие', 'callback_data': 'kv:l:i:0'},
                 {'text': '📤 Исходящие', 'callback_data': 'kv:l:o:0'}])
    rows.append([{'text': '🏆 Рейтинг друзей', 'callback_data': 'kv:t:xp'}])
    rows.append([{'text': '🔄 Обновить', 'callback_data': 'kv:s:friends'}])
    rows.append([{'text': '⬅ Игроки', 'callback_data': 'kv:s:social'}])
    return {'inline_keyboard': rows}


FRIENDS_PAGE_SIZE = 7
FRIEND_LIST_LABELS = {
    'friends': '🤝 Друзья', 'incoming': '📩 Входящие', 'outgoing': '📤 Исходящие',
}
FRIEND_LIST_CODES = {'friends':'f', 'incoming':'i', 'outgoing':'o'}


def friend_list_keyboard(view: dict, category: str, page: int) -> dict:
    """Bounded navigation that keeps changing actions out of overview-only pages."""
    if category not in FRIEND_LIST_LABELS:
        raise ValueError('Invalid friend section')
    entries = view[category]
    max_page = max(0, (len(entries) - 1) // FRIENDS_PAGE_SIZE)
    page = max(0, min(page, max_page))
    rows = []
    code = FRIEND_LIST_CODES[category]
    for uid, name in entries[page * FRIENDS_PAGE_SIZE:(page + 1) * FRIENDS_PAGE_SIZE]:
        if not uid.isdigit() or len(uid) > 19:
            continue
        if category == 'friends':
            rows.append([{'text': f'👤 {name[:32]}', 'callback_data': f'kv:u:{uid}'}])
        elif category == 'incoming':
            rows.append([{'text': f'✅ {uid}', 'callback_data': f'kv:f:a:{uid}'},
                         {'text': '❌', 'callback_data': f'kv:f:d:{uid}'}])
        else:
            rows.append([{'text': f'↩ Отменить {uid}', 'callback_data': f'kv:f:c:{uid}'}])
    pages = []
    if page:
        pages.append({'text': '⬅ Назад', 'callback_data': f'kv:l:{code}:{page-1}'})
    if page < max_page:
        pages.append({'text': 'Вперёд ➡', 'callback_data': f'kv:l:{code}:{page+1}'})
    if pages:
        rows.append(pages)
    rows.append([{'text': '👥 Все', 'callback_data': 'kv:s:friends'},
                 {'text': '🏆 Рейтинг', 'callback_data': 'kv:t:xp'}])
    rows.extend([
        [{'text': '🤝 Друзья', 'callback_data': 'kv:l:f:0'},
         {'text': '📩 Входящие', 'callback_data': 'kv:l:i:0'},
         {'text': '📤 Исходящие', 'callback_data': 'kv:l:o:0'}],
        [{'text': '⬅ Игроки', 'callback_data': 'kv:s:social'}],
    ])
    return {'inline_keyboard': rows}


def friend_list_page(view: dict, category: str, page: int) -> tuple[str, dict]:
    """Return a short, stable plain-text view and its current keyboard."""
    if category not in FRIEND_LIST_LABELS:
        raise ValueError('Invalid friend section')
    entries = view[category]
    last = max(0, (len(entries) - 1) // FRIENDS_PAGE_SIZE)
    page = max(0, min(page, last))
    lines = [f'👑 КВЕРАТО | {FRIEND_LIST_LABELS[category]}',
             f'Записей: {len(entries)} · Страница {page + 1}/{last + 1}', '']
    for uid, name in entries[page * FRIENDS_PAGE_SIZE:(page + 1) * FRIENDS_PAGE_SIZE]:
        lines.append(f'• {name} — {uid}')
    if not entries:
        lines.append('Здесь пока пусто.')
    if category == 'friends':
        lines.append('\nНажми на имя, чтобы посмотреть публичный профиль друга.')
    elif category == 'incoming':
        lines.append('\nКнопки позволяют принять или отклонить заявку.')
    else:
        lines.append('\nКнопки позволяют отменить отправленную заявку.')
    return '\n'.join(lines), friend_list_keyboard(view, category, page)


def friend_request_keyboard(sender_id: str) -> dict:
    if not sender_id.isdigit() or not 1 <= len(sender_id) <= 19:
        raise ValueError('Invalid sender ID')
    return {'inline_keyboard': [
        [{'text': '✅ Принять', 'callback_data': f'kv:f:a:{sender_id}'},
         {'text': '❌ Отклонить', 'callback_data': f'kv:f:d:{sender_id}'}],
        [{'text': '👥 Мои друзья', 'callback_data': 'kv:s:friends'}],
    ]}
