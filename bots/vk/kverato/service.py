"""Platform-agnostic commands for КВЕРАТО. No social API imports here."""
from __future__ import annotations

import math
import os
from contextlib import closing
from datetime import datetime, timezone
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from .db import Database
from .features import JOBS, record_game, unlock, suffix, extra_command
from .v04 import extra_command as v04_command, record_quest_event
from .v05 import extra_command as v05_command, expire_duels
from .v06 import extra_command as v06_command
from .v07 import admin_command, report, my_tickets, redeem, is_blocked, feature_enabled, FEATURE_FOR_CMD, MAX_WALLET
from .v08 import extra_command as v08_command, career_rank, career_progress, record_career_work
from .v09 import extra_command as v09_command, crafted_summary
from .v10 import extra_command as v10_command, expire_races, settle_expired_tournaments
from .v11 import extra_command as v11_command
from .v12 import extra_command as v12_command, inventory_summary
from .v13 import extra_command as v13_command, activity as season_activity, season_id
from .v14 import extra_command as v14_command, active_title, active_pet_work_bonus
from .v15 import extra_command as v15_command
from .v16 import extra_command as v16_command


@dataclass(frozen=True)
class Context:
    platform: str
    user_id: str
    chat_id: str
    name: str
    group: bool = False
    admin: bool = False
    target_id: str | None = None


ALIASES = {
    'start': 'start', 'старт': 'start', 'начать': 'start',
    'catalog': 'catalog', 'каталог': 'catalog',
    'help': 'help', 'помощь': 'help', 'команды': 'help', 'menu': 'help', 'меню': 'help',
    'profile': 'profile', 'профиль': 'profile', 'проф': 'profile',
    'balance': 'balance', 'баланс': 'balance',
    'level': 'level', 'уровень': 'level', 'ранг': 'level',
    'daily': 'daily', 'ежедневно': 'daily', 'бонус': 'daily',
    'work': 'work', 'работа': 'work', 'работать': 'work',
    'coin': 'coin', 'монетка': 'coin', 'орелрешка': 'coin',
    'dice': 'dice', 'кубик': 'dice',
    'rps': 'rps', 'кнб': 'rps',
    'shop': 'shop', 'магазин': 'shop',
    'buy': 'buy', 'купить': 'buy',
    'top': 'top', 'топ': 'top',
    'topxp': 'topxp', 'топуровень': 'topxp', 'топуровни': 'topxp',
    'games': 'games', 'игры': 'games',
    'economy': 'economy', 'экономика': 'economy',
    'administration': 'administration', 'админ': 'administration', 'admin': 'administration', 'модерация': 'administration',
    'transfer': 'transfer', 'перевести': 'transfer', 'передать': 'transfer',
    'bank': 'bank', 'банк': 'bank',
    'deposit': 'deposit', 'вклад': 'deposit', 'положить': 'deposit',
    'withdraw': 'withdraw', 'снять': 'withdraw',
    'jobs': 'jobs', 'профессии': 'jobs',
    'job': 'job', 'профессия': 'job',
    'stats': 'stats', 'статистика': 'stats',
    'inventory': 'inventory', 'инвентарь': 'inventory',
    'achievements': 'achievements', 'достижения': 'achievements',
    'guess': 'guess', 'угадай': 'guess',
    'quests': 'quests', 'задания': 'quests', 'квесты': 'quests',
    'claim': 'claim', 'награда': 'claim',
    'streak': 'streak', 'серия': 'streak',
    'equip': 'equip', 'надеть': 'equip',
    'slots': 'slots', 'слоты': 'slots',
    'topwins': 'topwins', 'топпобед': 'topwins',
    'duel': 'duel', 'дуэль': 'duel', 'дуэли': 'duel',
    'acceptduel': 'acceptduel', 'принять': 'acceptduel',
    'declineduel': 'declineduel', 'отклонить': 'declineduel',
    'cancelduel': 'cancelduel', 'отменитьдуэль': 'cancelduel',
    'duelhistory': 'duelhistory', 'историядуэлей': 'duelhistory',
    'rep': 'rep', 'репутация': 'rep',
    'giverep': 'giverep', 'уважить': 'giverep',
    'toprep': 'toprep', 'топреп': 'toprep',
    'clan': 'clan', 'клан': 'clan', 'кланы': 'clan',
    'clantop': 'clantop', 'топкланов': 'clantop',
    'clanmission': 'clanmission', 'кланмиссия': 'clanmission',
    'promo': 'promo', 'промокод': 'promo',
    'report': 'report', 'репорт': 'report',
    'appeal': 'appeal', 'апелляция': 'appeal',
    'tickets': 'tickets', 'моиобращения': 'tickets',
    'career': 'career', 'карьера': 'career',
    'businesses': 'businesses', 'бизнесы': 'businesses',
    'business': 'business', 'бизнес': 'business',
    'materials': 'materials', 'ресурсы': 'materials', 'склад': 'materials',
    'contracts': 'contracts', 'контракты': 'contracts', 'контракт': 'contracts',
    'home': 'home', 'дом': 'home', 'недвижимость': 'home',
    'vehicle': 'vehicle', 'транспорт': 'vehicle', 'машины': 'vehicle',
    'garage': 'garage', 'гараж': 'garage',
    'station': 'station', 'станция': 'station', 'энергия': 'station',
    'craft': 'craft', 'создать': 'craft', 'крафт': 'craft', 'мастерскаякрафт': 'craft',
    'race': 'race', 'гонка': 'race', 'гонки': 'race',
    'racestats': 'racestats', 'гонщик': 'racestats',
    'racetop': 'racetop', 'топгонщиков': 'racetop',
    'racechallenge': 'racechallenge', 'заезд': 'racechallenge',
    'tournament': 'tournament', 'турнир': 'tournament', 'турниры': 'tournament',
    'city': 'city', 'город': 'city', 'районы': 'city',
    'district': 'district', 'район': 'district',
    'expedition': 'expedition', 'экспедиция': 'expedition', 'экспедиции': 'expedition',
    'equipment': 'equipment', 'снаряжение': 'equipment',
    'collection': 'collection', 'коллекция': 'collection', 'находки': 'collection',
    'project': 'project', 'проект': 'project', 'горпроект': 'project',
    'wardrobe': 'wardrobe', 'гардероб': 'wardrobe', 'одежда': 'wardrobe',
    'outfit': 'outfit', 'облик': 'outfit',
    'giftitem': 'giftitem', 'подарить': 'giftitem',
    'market': 'market', 'рынок': 'market', 'торговля': 'market',
    'sets': 'sets', 'коллекции': 'sets',
    'season': 'season', 'сезон': 'season', 'сезоны': 'season',
    'seasonquests': 'seasonquests', 'сезонзадания': 'seasonquests',
    'seasontop': 'seasontop', 'топсезона': 'seasontop',
    'hall': 'hall', 'залславы': 'hall',
    'event': 'event', 'событие': 'event',
    'pets': 'pets', 'питомец': 'pets', 'питомцы': 'pets',
    'boxes': 'boxes', 'контейнер': 'boxes', 'контейнеры': 'boxes', 'кейсы': 'boxes',
    'titles': 'titles', 'титул': 'titles', 'титулы': 'titles',
    'refine': 'refine', 'переработка': 'refine',
    'orders': 'orders', 'заказы': 'orders', 'заказ': 'orders',
    'chains': 'chains', 'цепочки': 'chains', 'производство': 'chains',
    'delivery': 'delivery', 'рейс': 'delivery', 'доставка': 'delivery',
    'clanexpedition': 'clanexpedition', 'кланпоход': 'clanexpedition', 'кланэкспедиция': 'clanexpedition',

}
SHOP = {
    'vip': ('VIP', 1500),
    'legend': ('Легенда', 5000),
    'crown': ('Корона 👑', 10000),
    'star': ('Звезда ⭐', 3000),
    'phoenix': ('Феникс 🔥', 7500),
    'nebula': ('Туманность 🌌', 6000),
    'circuit': ('Контур ⚙️', 8500),
    'voyager': ('Странник 🧭', 9500),
    'aurora': ('Аврора 🌅', 12000),
    'prism': ('Призма 🔷', 14000),
    'comet': ('Комета ☄️', 16500),
    'atlas': ('Атлас 🏛', 20000),
}
BADGE_ORDER = {'Игрок': 0, 'VIP': 1, 'Звезда ⭐': 2, 'Легенда': 3, 'Феникс 🔥': 4, 'Корона 👑': 5}
BADGE_ORDER.update({'Туманность 🌌': 6, 'Контур ⚙️': 7, 'Странник 🧭': 8, 'Аврора 🌅': 9, 'Призма 🔷': 10, 'Комета ☄️': 11, 'Атлас 🏛': 12})


def level_from_xp(xp: int) -> int:
    return 1 + math.isqrt(xp // 100)


def duration(seconds: int) -> str:
    if seconds <= 0:
        return '0 сек.'
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    return ' '.join(f'{x} {unit}' for x, unit in ((h, 'ч'), (m, 'мин'), (s, 'сек')) if x)


class BotService:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time,
                 randbelow: Callable[[int], int] = secrets.randbelow,
                 owner_ids: set[str] | None = None) -> None:
        self.db = db
        self.clock = clock
        self.randbelow = randbelow
        raw = (os.environ.get('KVERATO_OWNER_IDS', '') if owner_ids is None
               else ','.join(owner_ids))
        self.owner_ids = {item.strip() for item in raw.split(',')
                          if re.fullmatch(r'[1-9][0-9]{0,18}', item.strip())}

    def welcome_for_member(self, ctx: Context, member_id: int) -> str | None:
        return None  # The bot does not manage VK conversation greetings.

    def _roll(self, maximum: int) -> int:
        return self.randbelow(maximum) + 1

    def _ensure(self, con, ctx: Context, now: int) -> None:
        name = re.sub(r'[\r\n\t]+', ' ', ctx.name).strip()[:60] or 'Игрок'
        con.execute('''
            INSERT INTO players (platform, user_id, display_name, created_at)
            VALUES (?, ?, ?, ?) ON CONFLICT(platform, user_id)
            DO UPDATE SET display_name = excluded.display_name
        ''', (ctx.platform, ctx.user_id, name, now))

    def _player(self, con, ctx: Context):
        return con.execute('SELECT * FROM players WHERE platform=? AND user_id=?',
                           (ctx.platform, ctx.user_id)).fetchone()

    def _award_activity(self, ctx: Context, now: int) -> None:
        with self.db.transaction() as con:
            expire_duels(con, now)
            expire_races(con, now)
            settle_expired_tournaments(con,self,now)
            self._ensure(con, ctx, now)
            con.execute('''
                UPDATE players SET xp=xp+8, last_xp_at=?
                WHERE platform=? AND user_id=? AND last_xp_at <= ?
            ''', (now, ctx.platform, ctx.user_id, now - 60))

    def handle(self, ctx: Context, content: str) -> str | None:
        """Process a message; returns text response or None (for ordinary chat)."""
        if not content or not content.strip():
            return None
        now = int(self.clock())
        words = content.strip().split()
        first = words[0]
        prefixed = first.startswith(('/', '!', '.'))
        blocked, reason, until = is_blocked(self, ctx, now)
        if prefixed:
            first = first[1:].split('@', 1)[0]
        elif ctx.group:
            # Ordinary chat text is never treated as a bot command.
            if blocked:
                return None
            self._award_activity(ctx, now)
            return None
        cmd = ALIASES.get(first.lower())
        args = words[1:]
        if blocked:
            if cmd == 'appeal':
                return report(self, ctx, args, now, 'appeal')
            if cmd == 'tickets':
                return my_tickets(self, ctx) if not ctx.group else None
            # In conversations do not publicly disclose a participant's block.
            if ctx.group:
                return None
            return (f'🚫 Доступ к КВЕРАТО ограничен. Причина: {reason}.'
                    + (' Бессрочно.' if until == 0 else ' До ' + datetime.fromtimestamp(until, timezone.utc).strftime('%d.%m.%Y %H:%M UTC') + '.')
                    + ' Для обжалования: апелляция текст (от 10 символов).')
        if cmd == 'administration':
            return admin_command(self, ctx, args, now)
        if cmd == 'report':
            return report(self, ctx, args, now, 'report')
        if cmd == 'appeal':
            return '✅ У тебя нет активной блокировки. Для сообщений команде используй «репорт текст».'
        if cmd == 'tickets':
            return my_tickets(self, ctx) if not ctx.group else None
        if cmd == 'promo':
            if not feature_enabled(self, ctx.platform, 'promos'):
                return '⏸ Активация промокодов временно приостановлена.'
            return redeem(self, ctx, args, now)
        if cmd is not None and cmd in FEATURE_FOR_CMD:
            feature = FEATURE_FOR_CMD[cmd]
            if not feature_enabled(self, ctx.platform, feature):
                return f'⏸ Раздел «{feature}» временно отключён администрацией КВЕРАТО.'
        # Legacy activity progression remains, except for blocked accounts.
        self._award_activity(ctx, now)
        if cmd is None:
            return None
        if cmd == 'start':
            return ('👑 КВЕРАТО — игровой бот!\n'
                    '🎮 Игры • 💰 Экономика • ⭐ Уровни • 👑 Кланы\n'
                    'Введи /помощь, чтобы увидеть команды.')
        if cmd == 'catalog':
            from .catalog import catalog_command
            return catalog_command(' '.join(args))
        if cmd == 'help' and content.strip().split()[0].lstrip('/!.').lower() in ('меню', 'menu'):
            # Text-only navigation replaces the old permanently visible VK keyboard.
            return ("""ГЛАВНОЕ МЕНЮ КВЕРАТО

👤 ПЕРСОНАЖ
профиль · уровень · достижения · инвентарь

💰 ЭКОНОМИКА
баланс · банк · работа · профессии · бизнесы

🛍 ПОКУПКИ И ИМУЩЕСТВО
магазин · питомцы · контейнеры · дом · гараж

🎮 РАЗВЛЕЧЕНИЯ
игры · гонка · турнир · дуэль · сезон

🌆 ГОРОД И КЛАНЫ
город · экспедиция · клан · кланпоход

🏭 ПРОИЗВОДСТВО
ресурсы · контракты · заказы · крафт

📚 каталог — все цены и предметы
📖 помощь — полный список команд

В беседах добавляй ! перед командой.""")
        if cmd == 'help':
            return ('👑 КВЕРАТО | КОМАНДЫ\n\n'
                    '📚 каталог — полные списки товаров и систем\n'
                    '👤 профиль / баланс / уровень / статистика\n'
                    '💼 работа / профессии / профессия курьер / карьера\n'
                    '🎁 бонус / задания / награда / серия\n'
                    '🏦 банк / вклад 100 / снять 100 / перевести ID 50\n'
                    '🏢 бизнесы / бизнес / бизнес купить мастерская\n'
                    '🐾 питомец / питомец каталог / питомец купить ember\n'
                    '🎁 контейнер / контейнер купить starter / контейнер открыть starter\n'
                    '🏷 титул / титул купить wanderer / титул выбрать wanderer\n'
                    '🧪 переработка / переработка fiber\n'
                    '📦 ресурсы / контракты / контракт выполнить детали\n'
                    '🏭 заказы / заказ выполнить supply_run / цепочки\n'
                    '🚚 рейс / рейс выполнить центр скутер\n'
                    '🧭 кланпоход / кланпоход идти док / кланпоход награда\n'
                    '🏠 дом / дом каталог / 🚗 гараж / транспорт каталог\n'
                    '⚡ станция / станция собрать / 🛠 крафт / создать модуль\n'
                    '🎮 игры / монетка 10 орёл / кубик 10 / слоты 10 / угадай 10 3\n'
                    '🏁 гонка купе спринт / заезд / турнир / топгонщиков\n'
                    '🌆 город / район центр / экспедиция / снаряжение\n'
                    '🎖 коллекция / проект / проект вклад детали 5\n'
                    '🛒 магазин / купить vip / инвентарь / надеть vip\n'
                    '👕 гардероб / облик / подарить ID visor\n'
                    '🛍 рынок / рынок выставить visor 850 / коллекции\n'
                    '🌠 сезон / сезон задания / сезон награды / событие / залславы\n'
                    '🏆 топ / топуровень / достижения / топпобед\n'
                    '⚔️ дуэль ID 50 / принять НОМЕР / историядуэлей\n'
                    '👑 клан помощь / топкланов / репутация / уважить ID\n'
                    '🎟 промокод КОД / репорт текст / моиобращения\n'
                    '🔐 админ — закрытая панель управления ботом\n\n'
                    'В беседе поставь ! перед игровой командой.')
        if cmd == 'games':
            return ('🎮 ИГРЫ КВЕРАТО\n'
                    '🪙 монетка 50 орёл (или решка)\n'
                    '🎲 кубик 50 (игра со ставкой)\n'
                    '✊ кнб камень / ножницы / бумага (без ставки)\n'
                    '🔢 угадай 10 3 — число от 1 до 5 (победа +40, проигрыш −10)\n'
                    '🎰 слоты 50 — 3 одинаковых: +5 ставок; 2: возврат; разные: проигрыш\n'
                    '⚔️ !дуэль ID 50 — вызов в беседе на дуэль 1 на 1\n'
                    '🏁 гонка скутер кольцо — гонки с транспортом\n'
                    '🏆 турнир / турнир записаться купе — ежедневный чемпионат\n'
                    '⚔️ !заезд ID 100 купе — гонка с игроком в беседе\n'
                    'Ставки — только виртуальные монеты, 1–5000.')
        if cmd == 'economy':
            return ('💰 ЭКОНОМИКА\n'
                    'баланс — проверить кошелёк\n'
                    'бонус — 200 монет раз в 24 часа + бонус за серию\n'
                    'задания / награда — ежедневные миссии (UTC)\n'
                    'работа — заработок раз в 30 минут + карьерный опыт\n'
                    'магазин — косметические значки\n'
                    'гардероб / облик / рынок / коллекции — одежда и торговля\n'
                    'сезон / событие / залславы — временные награды и рейтинги\n'
                    'банк / вклад 100 / снять 100 — безопасное хранение\n'
                    'профессии / профессия курьер / карьера — работа и карьерный опыт\n'
                    'бизнесы / бизнес — покупка и улучшение предприятий\n'
                    'ресурсы / контракты — склад и обычные поставки\n'
                    'заказы / цепочки — сложные производственные заказы и переработка\n'
                    'питомец / контейнер / титул / переработка — новые каталоги\n'
                    'дом / гараж / рейс — имущество и транспортные задания\n'
                    'станция / крафт — энергия и создание полезных предметов\n'
                    'перевести ID сумма — передача виртуальных монет\n'
                    'репутация / уважить ID — благодарность игроку раз в сутки UTC\n'
                    'клан помощь / клан вклад 50 / клан миссия / кланпоход — общие задания\n'
                    'В беседе можно ответить на сообщение: !перевести 50')
        feature_answer = v16_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v15_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v14_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v13_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v12_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v11_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v10_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v09_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v08_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v06_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v05_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = v04_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        feature_answer = extra_command(self, ctx, cmd, args, now)
        if feature_answer is not None:
            return feature_answer
        if cmd in ('profile', 'balance', 'level'):
            with closing(self.db.connect()) as con:
                p = self._player(con, ctx)
                active_career = career_progress(con, ctx, p['job_key'])
                enterprises = con.execute('''SELECT COUNT(*) FROM player_businesses
                    WHERE platform=? AND user_id=?''', (ctx.platform,ctx.user_id)).fetchone()[0]
                home = con.execute('''SELECT home_key FROM player_homes
                    WHERE platform=? AND user_id=?''', (ctx.platform,ctx.user_id)).fetchone()
                vehicles = con.execute('''SELECT COUNT(*) FROM player_vehicles
                    WHERE platform=? AND user_id=?''', (ctx.platform,ctx.user_id)).fetchone()[0]
                custom_title = active_title(con, ctx)
                title_row = con.execute('''SELECT title FROM season_titles WHERE platform=?
                    AND user_id=? AND equipped=1 ORDER BY season_id DESC LIMIT 1''',
                    (ctx.platform,ctx.user_id)).fetchone()
                racer = con.execute('''SELECT races,wins,rating FROM race_progress
                    WHERE platform=? AND user_id=?''',(ctx.platform,ctx.user_id)).fetchone()
            if cmd == 'level':
                level = level_from_xp(p['xp'])
                next_xp = 100 * level * level
                return f'⭐ Уровень {level}. Опыт: {p["xp"]}/{next_xp} XP.'
            if cmd == 'balance':
                return f'💰 Баланс: {p["coins"]:,} монет.'.replace(',', ' ')
            return (f'👤 {p["display_name"]}\n'
                    f'🎖 Значок: {p["badge"]}\n'
                    f'🌠 Титул: {custom_title or (title_row["title"] if title_row else "не выбран")}\n'
                    f'💰 Монеты: {p["coins"]:,}\n'
                    f'🏦 Банк: {p["bank_coins"]:,}\n'
                    f'⭐ Уровень: {level_from_xp(p["xp"])}\n'
                    f'✨ Опыт: {p["xp"]} XP\n'
                    f'🎮 Игр: {p["games_played"]} • Побед: {p["games_won"]}\n'
                    f'🤝 Репутация: {p["reputation"]}\n'
                    f'💼 Карьера: ранг {career_rank(active_career["xp"] if active_career else 0)}/5\n'
                    f'🏢 Предприятия: {enterprises}\n'
                    f'🏠 Жильё: {"есть" if home else "нет"} • 🚗 Транспорт: {vehicles}\n'
                    f'🏁 Гонки: {racer["races"] if racer else 0} • Побед: {racer["wins"] if racer else 0} • Рейтинг: {racer["rating"] if racer else 0}\n'
                    f'👕 Гардероб и облик: команда «облик»').replace(',', ' ')
        if cmd == 'daily':
            with self.db.transaction() as con:
                p = self._player(con, ctx)
                left = 24 * 3600 - (now - p['daily_at'])
                if p['daily_at'] and left > 0:
                    return f'🎁 Бонус уже получен. Возвращайся через {duration(left)}.'
                streak = p['daily_streak'] + 1 if p['daily_at'] and 86400 <= now-p['daily_at'] <= 172800 else 1
                amount = 200 + min(streak-1, 6)*25
                if p['coins'] > MAX_WALLET - amount:
                    return '❌ Кошелёк заполнен. Перенеси часть монет в банк.'
                con.execute('''UPDATE players SET coins=coins+?, daily_at=?, daily_streak=?
                              WHERE platform=? AND user_id=?''', (amount, now, streak, ctx.platform, ctx.user_id))
                gained = unlock(con, ctx, 'streak3', now) if streak >= 3 else False
            return (f'🎁 Ежедневный бонус: +200 монет! 🔥 Серия: {streak}.' + (f' Дополнительно за серию: +{amount-200} монет.' if amount > 200 else '') + (suffix(['🔥 Постоянный гость']) if gained else ''))
        if cmd == 'work':
            with self.db.transaction() as con:
                p = self._player(con, ctx)
                left = 30 * 60 - (now - p['work_at'])
                if p['work_at'] and left > 0:
                    return f'💼 Можно снова работать через {duration(left)}.'
                _, low, high = JOBS.get(p['job_key'], JOBS['courier'])
                previous = career_progress(con, ctx, p['job_key'])
                old_rank = career_rank(previous['xp'] if previous else 0)
                bonus = (old_rank - 1) * 10
                bonus += active_pet_work_bonus(con, ctx)
                salary = low + self.randbelow(high - low + 1) + bonus
                if p['coins'] > MAX_WALLET - salary:
                    return '❌ Кошелёк заполнен. Перенеси часть монет в банк.'
                con.execute('''UPDATE players SET coins=coins+?, work_at=?, work_count=work_count+1
                              WHERE platform=? AND user_id=?''', (salary, now, ctx.platform, ctx.user_id))
                rank, career_achievement = record_career_work(con,ctx,p['job_key'],now)
                record_quest_event(con, ctx, now, 'work')
                gained = unlock(con, ctx, 'worker', now)
                season_activity(con, ctx, 'work', now)
            return (f'💼 Работа выполнена! +{salary} монет. Карьерный ранг: {rank}/5 (+20 карьерного XP).'
                    + (suffix(['💼 Первая зарплата']) if gained else '')
                    + (suffix(['📈 Карьерист']) if career_achievement else ''))
        if cmd == 'transfer':
            return self._transfer(ctx, args, now)
        if cmd in ('coin', 'dice', 'guess'):
            if not args or len(args[0]) > 8 or not args[0].isascii() or not args[0].isdecimal():
                return {'coin': '🪙 Формат: /монетка 50 орёл', 'dice': '🎲 Формат: /кубик 50',
                        'guess': '🔢 Формат: /угадай 50 3 (число от 1 до 5)'}[cmd]
            bet = int(args[0])
            if not 1 <= bet <= 5000:
                return '⚠ Ставка должна быть от 1 до 5000 монет.'
            if cmd == 'coin':
                if len(args) < 2:
                    return '🪙 Укажи «орёл» или «решка»: /монетка 50 орёл'
                prediction = args[1].lower().replace('ё', 'е')
                if prediction not in ('орел', 'решка'):
                    return '🪙 Выбери «орёл» или «решка».'
                outcome = 'орел' if self.randbelow(2) == 0 else 'решка'
                win = prediction == outcome
                delta = bet if win else -bet
                details = f'🪙 Выпало: {"орёл" if outcome == "орел" else "решка"}. '
            elif cmd == 'guess':
                if len(args) != 2 or args[1] not in ('1', '2', '3', '4', '5'):
                    return '🔢 Формат: /угадай 50 3 (число от 1 до 5).'
                outcome_number = self._roll(5)
                win = int(args[1]) == outcome_number
                delta = 4 * bet if win else -bet
                details = f'🔢 Выпало число: {outcome_number}. '
            else:
                yours, theirs = self._roll(6), self._roll(6)
                win = yours > theirs
                delta = 0 if yours == theirs else (bet if win else -bet)
                details = f'🎲 Твой бросок: {yours}, бот: {theirs}. '
            with self.db.transaction() as con:
                p = self._player(con, ctx)
                if p['coins'] < bet:
                    return '❌ На балансе недостаточно монет.'
                if delta > 0 and p['coins'] > MAX_WALLET - delta:
                    return '❌ Кошелёк заполнен. Перенеси часть монет в банк.'
                con.execute('UPDATE players SET coins=coins+? WHERE platform=? AND user_id=?',
                            (delta, ctx.platform, ctx.user_id))
                unlocked = record_game(con, ctx, bool(delta > 0), now)
            if delta == 0:
                return details + '🤝 Ничья, ставка возвращена.' + suffix(unlocked)
            return details + (f'🎉 Победа! +{delta} монет.' if win else f'😔 Не повезло. −{bet} монет.') + suffix(unlocked)
        if cmd == 'rps':
            choices = ('камень', 'ножницы', 'бумага')
            if not args or args[0].lower() not in choices:
                return '✊ Формат: /кнб камень (или ножницы, бумага)'
            your_move = args[0].lower()
            bots_move = choices[self.randbelow(3)]
            wins = {('камень', 'ножницы'), ('ножницы', 'бумага'), ('бумага', 'камень')}
            won = (your_move, bots_move) in wins
            result = '🤝 Ничья' if your_move == bots_move else ('🎉 Победа' if won else '😔 Поражение')
            with self.db.transaction() as con:
                unlocked = record_game(con, ctx, won, now)
            return f'✊ Ты: {your_move}. Бот: {bots_move}. {result}! (без ставок)' + suffix(unlocked)
        if cmd == 'shop':
            # Render directly from SHOP: newly added badges must not be hidden.
            items = '\n'.join(
                f'• {badge} [{key}] — {price:,} монет'.replace(',', ' ')
                for key, (badge, price) in SHOP.items()
            )
            return ('🛍 МАГАЗИН ЗНАЧКОВ\n' + items
                    + '\nКупить: купить КЛЮЧ (например, купить aurora).\n'
                    'Значки украшают профиль, не дают прав администратора.')
        if cmd == 'buy':
            key = args[0].lower() if args else ''
            if key not in SHOP:
                return '🛍 Такого значка нет. Открой «магазин» или «каталог магазин».'
            badge, price = SHOP[key]
            with self.db.transaction() as con:
                p = self._player(con, ctx)
                owned = con.execute('''SELECT 1 FROM purchases WHERE platform=? AND user_id=? AND item_key=?''',
                                    (ctx.platform, ctx.user_id, key)).fetchone()
                if owned:
                    return '✅ Ты уже купил этот значок.'
                if p['coins'] < price:
                    return f'❌ Для покупки нужно {price:,} монет.'.replace(',', ' ')
                con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND user_id=?''',
                            (price, ctx.platform, ctx.user_id))
                con.execute('''INSERT INTO purchases(platform, user_id, item_key, purchased_at) VALUES(?,?,?,?)''',
                            (ctx.platform, ctx.user_id, key, now))
                if BADGE_ORDER[badge] > BADGE_ORDER[p['badge']]:
                    con.execute('''UPDATE players SET badge=? WHERE platform=? AND user_id=?''',
                                (badge, ctx.platform, ctx.user_id))
            return f'✅ Значок «{badge}» приобретён!'
        if cmd in ('top', 'topxp'):
            field = 'coins' if cmd == 'top' else 'xp'
            with closing(self.db.connect()) as con:
                top = con.execute(f'SELECT display_name, {field} FROM players WHERE platform=? ORDER BY {field} DESC, created_at ASC LIMIT 10',
                                  (ctx.platform,)).fetchall()
            title = '🏆 ТОП ПО МОНЕТАМ' if cmd == 'top' else '⭐ ТОП ПО ОПЫТУ'
            return title + '\n' + '\n'.join(f'{i}. {p["display_name"]}: {p[field]:,}'.replace(',', ' ') for i, p in enumerate(top, 1))
        return None

    def _transfer(self, ctx: Context, args: list[str], now: int) -> str:
        """Transfer virtual coins atomically within ONE platform (reply/mention/id)."""
        # An explicitly typed ID overrides any replied-to user. Otherwise
        # a command written as a reply could silently pay the wrong account.
        target = ctx.target_id
        amount_text = None
        if len(args) == 2 and args[0].isascii() and args[0].isdecimal():
            target, amount_text = args
        elif target:
            # "перевести 50" in a reply or "перевести [id42|name] 50"
            amount_text = next((a for a in reversed(args) if a.isascii() and a.isdecimal()), None)
        if not target or not target.isascii() or not target.isdecimal() or not amount_text:
            return '💸 Формат: перевести ID сумма. В беседе — ответь: !перевести 50.'
        if target == ctx.user_id:
            return '⛔ Нельзя переводить монеты самому себе.'
        if not amount_text.isascii() or not amount_text.isdecimal():
            return '⚠ Сумма должна быть целым числом.'
        if len(amount_text) > 6:
            return '⚠ Сумма перевода: от 1 до 100 000 монет.'
        amount = int(amount_text)
        if not 1 <= amount <= 100_000:
            return '⚠ Сумма перевода: от 1 до 100 000 монет.'
        with self.db.transaction() as con:
            # Require existing participants; do not create arbitrary ghost accounts by ID.
            registered = con.execute('''SELECT 1 FROM players
                WHERE platform=? AND user_id=?''', (ctx.platform, target)).fetchone()
            if not registered:
                return '❌ Получатель пока не зарегистрирован в КВЕРАТО (пусть напишет боту «старт»).'
            blocked = con.execute('''SELECT 1 FROM bot_access WHERE platform=? AND user_id=?
                AND (blocked_until=0 OR blocked_until>?)''', (ctx.platform, target, now)).fetchone()
            if blocked:
                return '🚫 Получателю ограничен доступ к КВЕРАТО.'
            recipient = con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                                    (ctx.platform, target)).fetchone()
            if recipient['coins'] > MAX_WALLET - amount:
                return '❌ У получателя превышен лимит кошелька.'
            success = con.execute('''UPDATE players SET coins = coins - ?
                WHERE platform=? AND user_id=? AND coins >= ?''',
                (amount, ctx.platform, ctx.user_id, amount)).rowcount
            if not success:
                return '❌ Недостаточно монет для перевода.'
            con.execute('''UPDATE players SET coins=coins+?
                WHERE platform=? AND user_id=?''', (amount, ctx.platform, target))
            con.execute('''INSERT INTO transfers
                (platform, from_user_id, to_user_id, amount, created_at)
                VALUES(?,?,?,?,?)''', (ctx.platform, ctx.user_id, target, amount, now))
        return f'✅ Переведено {amount:,} монет игроку {target}.'.replace(',', ' ')

