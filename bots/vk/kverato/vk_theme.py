"""KVERATO's consistent VK-only plain-text presentation.

VK community messages are plain text (Markdown bold/HTML are not supported).
This module transforms *only* the message sent to VK and never changes game
logic, balances, command output returned by BotService, or SQLite records.
"""
from __future__ import annotations

import re

RULE = '━━━━━━━━━━━━━━━━━━━━'
SOFT_RULE = '────────────────────'
LIMIT = 3800  # leave margin under VK messages.send's 4096-character limit

# Both legacy aliases and normalized command names are supported.  No menu
# keyboard is created here; this is exclusively a text presentation layer.
TOPICS = {
    'start': ('👑', 'ДОБРО ПОЖАЛОВАТЬ'),
    'help': ('📖', 'ПОМОЩЬ И КОМАНДЫ'),
    'catalog': ('📚', 'КАТАЛОГ'),
    'profile': ('👤', 'ПРОФИЛЬ'), 'stats': ('📊', 'СТАТИСТИКА'),
    'balance': ('💰', 'МОИ ФИНАНСЫ'), 'bank': ('🏦', 'БАНК'),
    'deposit': ('🏦', 'ПОПОЛНЕНИЕ БАНКА'), 'withdraw': ('🏦', 'СНЯТИЕ СО СЧЁТА'),
    'transfer': ('💸', 'ПЕРЕВОД МОНЕТ'), 'level': ('⭐', 'УРОВЕНЬ'),
    'daily': ('🎁', 'ЕЖЕДНЕВНЫЙ БОНУС'), 'weekly': ('🗓', 'ЕЖЕНЕДЕЛЬНАЯ НАГРАДА'),
    'rewards': ('⏳', 'НАГРАДЫ И ТАЙМЕРЫ'),
    'goal': ('🎯', 'ЦЕЛЬ НАКОПЛЕНИЯ'), 'streak': ('🔥', 'СЕРИЯ БОНУСОВ'),
    'quests': ('📜', 'ЗАДАНИЯ'), 'claim': ('🎁', 'ПОЛУЧЕНИЕ НАГРАДЫ'),
    'work': ('💼', 'РАБОТА'), 'jobs': ('💼', 'ПРОФЕССИИ'), 'job': ('💼', 'ПРОФЕССИЯ'),
    'career': ('📈', 'КАРЬЕРА'), 'economy': ('💰', 'ЭКОНОМИКА'),
    'games': ('🎮', 'ИГРЫ'), 'coin': ('🪙', 'МОНЕТКА'),
    'dice': ('🎲', 'КУБИК'), 'slots': ('🎰', 'СЛОТЫ'),
    'rps': ('✊', 'КАМЕНЬ · НОЖНИЦЫ · БУМАГА'), 'guess': ('🔢', 'УГАДАЙ ЧИСЛО'),
    'shop': ('🛍', 'МАГАЗИН'), 'buy': ('🛍', 'ПОКУПКА'),
    'inventory': ('🎒', 'ИНВЕНТАРЬ'), 'equip': ('🎖', 'ЗНАЧКИ'),
    'achievements': ('🏆', 'ДОСТИЖЕНИЯ'), 'top': ('🏆', 'РЕЙТИНГ'),
    'topxp': ('🏆', 'РЕЙТИНГ УРОВНЕЙ'), 'topwins': ('🏆', 'РЕЙТИНГ ПОБЕД'),
    'duel': ('⚔️', 'ДУЭЛИ'), 'acceptduel': ('⚔️', 'ДУЭЛЬ'),
    'declineduel': ('⚔️', 'ДУЭЛЬ'), 'cancelduel': ('⚔️', 'ДУЭЛЬ'),
    'duelhistory': ('📜', 'ИСТОРИЯ ДУЭЛЕЙ'),
    'rep': ('🤝', 'РЕПУТАЦИЯ'), 'giverep': ('🤝', 'БЛАГОДАРНОСТЬ'),
    'toprep': ('🏆', 'РЕЙТИНГ РЕПУТАЦИИ'),
    'clan': ('👑', 'КЛАН'), 'clantop': ('🏆', 'РЕЙТИНГ КЛАНОВ'),
    'clanmission': ('📜', 'КЛАНОВОЕ ЗАДАНИЕ'),
    'clanexpedition': ('🧭', 'КЛАНОВЫЙ ПОХОД'),
    'administration': ('🔐', 'УПРАВЛЕНИЕ КВЕРАТО'),
    'report': ('✉️', 'ОБРАЩЕНИЕ'), 'tickets': ('✉️', 'МОИ ОБРАЩЕНИЯ'),
    'appeal': ('✉️', 'АПЕЛЛЯЦИЯ'), 'promo': ('🎟', 'ПРОМОКОД'),
    'business': ('🏢', 'ПРЕДПРИЯТИЕ'), 'businesses': ('🏢', 'ПРЕДПРИЯТИЯ'),
    'contracts': ('📦', 'КОНТРАКТЫ'), 'materials': ('📦', 'РЕСУРСЫ'),
    'orders': ('🏭', 'ПРОИЗВОДСТВЕННЫЕ ЗАКАЗЫ'),
    'chains': ('⚙️', 'ПРОИЗВОДСТВЕННЫЕ ЦЕПОЧКИ'),
    'home': ('🏠', 'НЕДВИЖИМОСТЬ'), 'vehicle': ('🚗', 'ТРАНСПОРТ'),
    'garage': ('🚗', 'ГАРАЖ'), 'station': ('⚡', 'ЭНЕРГОСТАНЦИЯ'),
    'craft': ('🛠', 'МАСТЕРСКАЯ'),
    'race': ('🏁', 'ГОНКИ'), 'racestats': ('🏎', 'ГОНОЧНЫЙ ПРОФИЛЬ'),
    'racetop': ('🏆', 'РЕЙТИНГ ГОНЩИКОВ'),
    'racechallenge': ('⚔️', 'PVP-ЗАЕЗДЫ'), 'tournament': ('🏆', 'ТУРНИР'),
    'delivery': ('🚚', 'ТРАНСПОРТНЫЕ РЕЙСЫ'),
    'city': ('🌆', 'ГОРОД КВЕРАТО'), 'district': ('🌆', 'РАЙОН'),
    'expedition': ('🧭', 'ЭКСПЕДИЦИЯ'), 'equipment': ('🧰', 'СНАРЯЖЕНИЕ'),
    'collection': ('🎖', 'РЕЛИКВИИ'), 'project': ('🏗', 'ГОРОДСКОЙ ПРОЕКТ'),
    'wardrobe': ('👕', 'ГАРДЕРОБ'), 'outfit': ('🎭', 'МОЙ ОБЛИК'),
    'giftitem': ('🎁', 'ПОДАРОК'), 'market': ('🛍', 'ТОРГОВАЯ ПЛОЩАДКА'),
    'sets': ('🎖', 'КОЛЛЕКЦИИ'),
    'season': ('🌠', 'СЕЗОН'), 'seasonquests': ('📜', 'СЕЗОННЫЕ ЗАДАНИЯ'),
    'seasontop': ('🏆', 'РЕЙТИНГ СЕЗОНА'), 'hall': ('🏛', 'ЗАЛ СЛАВЫ'),
    'event': ('🌌', 'СЕЗОННОЕ СОБЫТИЕ'),
    'pets': ('🐾', 'ПИТОМЦЫ'), 'boxes': ('🎁', 'КОНТЕЙНЕРЫ'),
    'titles': ('🏷', 'ТИТУЛЫ'), 'refine': ('🧪', 'ПЕРЕРАБОТКА'),
}

HINTS = {
    'start': '«меню» — разделы игры  •  «помощь» — команды',
    'help': '«меню» — краткая навигация',
    'catalog': '«каталог» — все категории',
    'profile': '«достижения» — награды  •  «баланс» — финансы',
    'balance': '«банк» — накопления  •  «работа» — заработок',
    'games': '«меню» — все разделы',
    'economy': '«каталог» — цены и предметы',
    'season': '«сезон задания» — цели  •  «сезон награды» — призы',
    'shop': '«инвентарь» — мои предметы',
    'business': '«ресурсы» — склад  •  «заказы» — поставки',
    'city': '«экспедиция» — приключения',
    'administration': '«админ журнал» — история действий',
}

ERROR_STARTS = ('❌', '⛔', '🚫', '⚠', '🔒', '❗', '⏳', '⌛')
SUCCESS_STARTS = ('✅', '🎉', '🎊', '💸', '🏆')


def _normalize(text: str) -> str:
    return '\n'.join(line.rstrip() for line in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n')).strip()


def _profile_body(text: str) -> str:
    """Organize the known profile fields without interpreting any player data."""
    lines = text.splitlines()
    groups = [
        ('💰 ФИНАНСЫ', ('💰 Монеты:', '🏦 Банк:')),
        ('✨ ПРОГРЕСС', ('⭐ Уровень:', '✨ Опыт:', '🎮 Игр:', '🤝 Репутация:', '💼 Карьера:')),
        ('🏙 ИМУЩЕСТВО И АКТИВНОСТЬ', ('🏢 Предприятия:', '🏠 Жильё:', '🏁 Гонки:', '👕 Гардероб')),
    ]
    if not any(line.startswith('💰 Монеты:') for line in lines):
        return text
    lead = []
    collected = set()
    for line in lines:
        if any(line.startswith(prefix) for _, prefixes in groups for prefix in prefixes):
            continue
        lead.append(line)
    result = list(lead)
    for heading, prefixes in groups:
        rows = []
        for line in lines:
            if any(line.startswith(prefix) for prefix in prefixes):
                rows.append(line)
                collected.add(line)
        if rows:
            result.extend(['', f'── {heading} ──', *rows])
    return '\n'.join(result).strip()


def _split_lines(body: str, max_chars: int) -> list[str]:
    """Do not truncate catalogs: split at line boundaries, including long lines."""
    lines: list[str] = []
    for line in body.split('\n'):
        if len(line) <= max_chars:
            lines.append(line)
            continue
        while len(line) > max_chars:
            pos = line.rfind(' ', 0, max_chars)
            if pos < max_chars // 2:
                pos = max_chars
            lines.append(line[:pos].rstrip())
            line = line[pos:].lstrip()
        lines.append(line)
    pieces = []
    current = ''
    for line in lines:
        new = line if not current else current + '\n' + line
        if len(new) > max_chars and current:
            pieces.append(current.strip())
            current = line
        else:
            current = new
    if current.strip():
        pieces.append(current.strip())
    return pieces or ['']


def render_vk_messages(raw: str, command: str | None, *, group: bool = False) -> list[str]:
    """Render every service reply in a consistent VK-readable format.

    A caller sends the resulting chunks in order. Group replies use shorter
    headings and no persistent navigation footer to avoid spamming the chat.
    """
    content = _normalize(raw)
    if not content:
        return []
    command = (command or '').lower()
    emoji, title = TOPICS.get(command, ('👑', 'ИГРОВОЙ БОТ'))
    # Explicit failures get status-focused headings; body remains intact.
    if content.startswith(ERROR_STARTS):
        emoji, title = '⚠️', 'ОБРАТИ ВНИМАНИЕ'
    elif content.startswith(SUCCESS_STARTS) and '\n' not in content:
        emoji, title = '✅', 'ДЕЙСТВИЕ ВЫПОЛНЕНО'
    if command == 'profile':
        content = _profile_body(content)
    heading = f'👑 КВЕРАТО  /  {emoji} {title}\n{RULE}'
    # For all messages we keep original textual semantics, VK user mentions,
    # commands, IDs, and numeric amounts unchanged.
    hint = HINTS.get(command)
    if not hint and command in ('daily', 'work', 'quests', 'claim'):
        hint = '«профиль» — прогресс  •  «меню» — разделы'
    suffix = '' if group or not hint else f'\n\n{SOFT_RULE}\n↳ {hint}'
    max_body = LIMIT - len(heading) - len(suffix) - 60
    chunks = _split_lines(content, max_body)
    total = len(chunks)
    results = []
    for i, chunk in enumerate(chunks, 1):
        part_heading = heading if total == 1 else f'{heading}\n📄 Часть {i}/{total}'
        part_footer = suffix if i == total else ''
        formatted = f'{part_heading}\n\n{chunk}{part_footer}'
        if len(formatted) > LIMIT:
            raise AssertionError('VK message length overflow')
        results.append(formatted)
    return results
