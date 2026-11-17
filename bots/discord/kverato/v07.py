"""v0.7: private, auditable VK bot administration (not VK conversation moderation).

Permissions come from an explicit owner ID allowlist and database-assigned staff,
never from the VK chat administrator flag. All money/rights writes are atomic.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import re

from .features import ACHIEVEMENTS
from .v04 import EQUIP

MAX_WALLET = 10**12
FEATURES = {'games', 'transfers', 'clans', 'jobs', 'shop', 'promos', 'businesses', 'property', 'vehicles', 'energy', 'craft', 'racing', 'tournaments', 'exploration', 'city_projects', 'wardrobe', 'market', 'seasons', 'events', 'pets', 'boxes', 'titles'}
FEATURE_FOR_CMD = {
    **{k: 'games' for k in ('coin', 'dice', 'rps', 'guess', 'slots',
                           'duel', 'acceptduel', 'declineduel', 'cancelduel')},
    'transfer': 'transfers', 'clan': 'clans', 'clantop': 'clans',
    'clanmission': 'clans', 'work': 'jobs', 'jobs': 'jobs', 'job': 'jobs',
    'shop': 'shop', 'buy': 'shop', 'promo': 'promos',
    'career': 'jobs', 'business': 'businesses', 'businesses': 'businesses',
    'materials': 'businesses', 'contracts': 'businesses',
    'orders': 'businesses', 'chains': 'businesses',
    'home': 'property', 'vehicle': 'vehicles', 'garage': 'vehicles',
    'station': 'energy', 'craft': 'craft',
    'delivery': 'vehicles', 'clanexpedition': 'clans',
    'race': 'racing', 'racestats': 'racing', 'racetop': 'racing',
    'racechallenge': 'racing', 'tournament': 'tournaments',
    'expedition': 'exploration', 'equipment': 'exploration', 'collection': 'exploration',
    'city': 'exploration', 'district': 'exploration', 'project': 'city_projects',
    'wardrobe': 'wardrobe', 'outfit': 'wardrobe', 'giftitem': 'wardrobe',
    'sets': 'wardrobe', 'market': 'market',
    'season': 'seasons', 'seasonquests': 'seasons', 'seasontop': 'seasons', 'hall': 'seasons',
    'event': 'events', 'pets': 'pets', 'boxes': 'boxes', 'titles': 'titles', 'refine': 'craft',
}
BADGES = {key: title for key, title in EQUIP.items() if key in ('vip', 'legend', 'crown', 'star', 'phoenix')}
VALID_ID = re.compile(r'[1-9][0-9]{0,18}\Z')
VALID_PROMO = re.compile(r'[A-Z0-9_-]{4,24}\Z')


def clean_text(parts: list[str], maximum: int = 160) -> str:
    return re.sub(r'[\x00-\x1f\x7f]+', ' ', ' '.join(parts)).strip()[:maximum]


def parse_positive(value: str, max_value: int) -> int | None:
    if not re.fullmatch(r'[0-9]{1,15}', value):
        return None
    num = int(value)
    return num if 1 <= num <= max_value else None


def role(service, ctx) -> str | None:
    if ctx.platform not in ('vk', 'telegram', 'discord'):
        return None
    if ctx.user_id in service.owner_ids:
        return 'owner'
    with closing(service.db.connect()) as con:
        row = con.execute('SELECT role FROM bot_staff WHERE platform=? AND user_id=?',
                          (ctx.platform, ctx.user_id)).fetchone()
    return row['role'] if row else None


def is_blocked(service, ctx, now: int) -> tuple[bool, str, int]:
    if ctx.platform in ('vk', 'telegram', 'discord') and ctx.user_id in service.owner_ids:
        return False, '', 0
    with closing(service.db.connect()) as con:
        row = con.execute('SELECT blocked_until,reason FROM bot_access WHERE platform=? AND user_id=?',
                          (ctx.platform, ctx.user_id)).fetchone()
    if row and (row['blocked_until'] == 0 or row['blocked_until'] > now):
        return True, row['reason'], row['blocked_until']
    return False, '', 0


def feature_enabled(service, platform: str, feature: str) -> bool:
    with closing(service.db.connect()) as con:
        row = con.execute('SELECT enabled FROM bot_flags WHERE platform=? AND feature=?',
                          (platform, feature)).fetchone()
    return bool(row['enabled']) if row else True


def audit(con, ctx, target: str, action: str, detail: str, now: int) -> None:
    con.execute('INSERT INTO bot_audit(platform,actor_id,target_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                (ctx.platform, ctx.user_id, target, action, detail, now))


def report(service, ctx, args: list[str], now: int, kind: str) -> str:
    if ctx.group:
        return '✉️ Обращение отправляется только в личных сообщениях КВЕРАТО.'
    body = clean_text(args, 500)
    if len(body) < 10:
        return '✉️ Опиши ситуацию подробнее (10–500 символов).'
    with service.db.transaction() as con:
        if kind == 'appeal':
            existing = con.execute('''SELECT 1 FROM bot_reports WHERE platform=? AND sender_id=?
                AND kind='appeal' AND status='open' LIMIT 1''', (ctx.platform, ctx.user_id)).fetchone()
            if existing:
                return '✉️ Твоя апелляция уже рассматривается.'
        last = con.execute('''SELECT created_at FROM bot_reports WHERE platform=? AND sender_id=? AND kind=?
            ORDER BY id DESC LIMIT 1''', (ctx.platform, ctx.user_id, kind)).fetchone()
        cooldown = 86400 if kind == 'appeal' else 3600
        if last and now - last['created_at'] < cooldown:
            return '⏳ Новое обращение этого типа можно отправить позже.'
        cur = con.execute('''INSERT INTO bot_reports(platform,sender_id,kind,body,created_at)
            VALUES(?,?,?,?,?)''', (ctx.platform, ctx.user_id, kind, body, now))
        ident = cur.lastrowid
    return f'✅ Обращение №{ident} принято. Ответ/решение будет доступно в меню «мои обращения».'


def tickets(service, ctx, admin_role, args: list[str], now: int) -> str:
    sub = args[0].lower() if args else 'обращения'
    if sub == 'обращения':
        with closing(service.db.connect()) as con:
            rows = con.execute('''SELECT id,kind,sender_id,created_at FROM bot_reports
                WHERE platform=? AND status='open' ORDER BY id ASC LIMIT 10''', (ctx.platform,)).fetchall()
        return ('✉️ Открытые обращения:\n' + '\n'.join(
            f'№{r["id"]} {r["kind"]} — пользователь {r["sender_id"]}' for r in rows)) if rows else '✉️ Нет открытых обращений.'
    if sub == 'обращение' and len(args) == 2:
        ident = parse_positive(args[1], 10**12)
        if ident is None:
            return 'Формат: админ обращение НОМЕР'
        with closing(service.db.connect()) as con:
            row = con.execute('SELECT * FROM bot_reports WHERE platform=? AND id=?',
                              (ctx.platform, ident)).fetchone()
        return (f'✉️ №{ident} | {row["kind"]} | {row["status"]}\n'
                f'От: {row["sender_id"]}\n{row["body"]}') if row else 'Обращение не найдено.'
    if sub == 'закрыть' and len(args) >= 3:
        if admin_role == 'helper':
            return '⛔ Помощник может просматривать обращения, но не закрывать их.'
        ident = parse_positive(args[1], 10**12)
        reason = clean_text(args[2:])
        if ident is None or len(reason) < 3:
            return 'Формат: админ закрыть НОМЕР причина'
        with service.db.transaction() as con:
            changed = con.execute('''UPDATE bot_reports SET status='closed',closed_by=?,closed_at=?,resolution=?
                WHERE platform=? AND id=? AND status='open' ''',
                (ctx.user_id, now, reason, ctx.platform, ident)).rowcount
            if changed:
                audit(con, ctx, str(ident), 'report:close', reason, now)
        return '✅ Обращение закрыто.' if changed else 'Обращение не найдено или уже закрыто.'
    return '✉️ админ обращения / админ обращение НОМЕР / админ закрыть НОМЕР причина'


def my_tickets(service, ctx) -> str:
    with closing(service.db.connect()) as con:
        rows = con.execute('''SELECT id,kind,status,resolution FROM bot_reports
            WHERE platform=? AND sender_id=? ORDER BY id DESC LIMIT 5''',
            (ctx.platform, ctx.user_id)).fetchall()
    return '✉️ МОИ ОБРАЩЕНИЯ\n' + ('\n'.join(
        f'№{r["id"]}: {r["kind"]} — {r["status"]}' + (f' | Решение: {r["resolution"]}' if r['resolution'] else '')
        for r in rows) if rows else 'Пока пусто.')


def redeem(service, ctx, args: list[str], now: int) -> str:
    if ctx.group:
        return '🎁 Промокоды можно вводить только в личных сообщениях.'
    if len(args) != 1 or not VALID_PROMO.fullmatch(args[0].upper()):
        return '🎁 Формат: промокод КОД'
    code = args[0].upper()
    with service.db.transaction() as con:
        entry = con.execute('''SELECT reward,max_uses,uses,expires_at,active FROM bot_promos
            WHERE platform=? AND code=?''', (ctx.platform, code)).fetchone()
        if not entry or not entry['active'] or entry['expires_at'] <= now:
            return '❌ Промокод недействителен или срок истёк.'
        if entry['uses'] >= entry['max_uses']:
            return '❌ Лимит использований промокода исчерпан.'
        claimed = con.execute('''SELECT 1 FROM bot_promo_redemptions WHERE platform=? AND code=? AND user_id=?''',
                              (ctx.platform, code, ctx.user_id)).fetchone()
        if claimed:
            return '🎁 Ты уже активировал этот промокод.'
        service._ensure(con, ctx, now)
        updated = con.execute('UPDATE players SET coins=coins+? WHERE platform=? AND user_id=? AND coins<=?',
                              (entry['reward'], ctx.platform, ctx.user_id, MAX_WALLET-entry['reward'])).rowcount
        if not updated:
            return '⚠ Достигнут лимит игрового кошелька.'
        con.execute('''INSERT INTO bot_promo_redemptions(platform,code,user_id,claimed_at)
            VALUES(?,?,?,?)''', (ctx.platform, code, ctx.user_id, now))
        con.execute('UPDATE bot_promos SET uses=uses+1 WHERE platform=? AND code=?', (ctx.platform, code))
        con.execute('''INSERT INTO bot_economy_ledger(platform,actor_id,target_id,delta,source,created_at)
            VALUES(?,?,?,?,?,?)''', (ctx.platform, 'system', ctx.user_id, entry['reward'], 'promo:'+code, now))
    return f'🎁 Промокод активирован! +{entry["reward"]} монет.'


def admin_command(service, ctx, args: list[str], now: int) -> str:
    if ctx.group or ctx.platform not in ('vk', 'telegram', 'discord'):
        return '🔐 Панель КВЕРАТО доступна только в личных сообщениях боту.'
    who = role(service, ctx)
    if not who:
        return '⛔ Нет доступа к управлению КВЕРАТО.'
    if not args:
        return (f'🔐 КВЕРАТО | Администрация ({who})\n'
                'админ статус ID / админ журнал / админ обращения\n'
                'админ обращение НОМЕР / админ закрыть НОМЕР причина\n'
                'админ блок ID часы причина / админ разблок ID\n'
                'админ монеты ID +N|-N|=N причина\n'
                'админ опыт ID +N|-N причина\n'
                'админ значок ID ключ / админ снятьзначок ID ключ\n'
                'админ достижение ID ключ / админ снятьдостижение ID ключ\n'
                'Только владелец: админ сотрудник ID admin|helper|off,\n'
                'админ функция exploration|city_projects|racing|tournaments|... on|off,\n'
                'админ промо CODE сумма активации часы, админ закрытьпромо CODE.\n'
                'Все операции фиксируются в журнале.')
    op = args[0].lower()
    if op in ('обращения', 'обращение', 'закрыть'):
        return tickets(service, ctx, who, args, now)
    if op in ('блок','монеты','опыт','значок','снятьзначок','достижение','снятьдостижение','сотрудник','функция','промо','закрытьпромо') and len(args) == 1:
        help_messages = {
            'блок':'админ блок ID 24 причина', 'монеты':'админ монеты ID +100 причина',
            'опыт':'админ опыт ID +100 причина', 'значок':'админ значок ID vip',
            'снятьзначок':'админ снятьзначок ID vip', 'достижение':'админ достижение ID worker',
            'снятьдостижение':'админ снятьдостижение ID worker',
            'сотрудник':'админ сотрудник ID admin|helper|off',
            'функция':'админ функция exploration|city_projects|racing|tournaments|... on|off',
            'промо':'админ промо CODE 100 20 24', 'закрытьпромо':'админ закрытьпромо CODE',
        }
        return 'Формат: ' + help_messages[op]
    if op == 'журнал':
        with closing(service.db.connect()) as con:
            rows = con.execute('''SELECT actor_id,target_id,action,detail FROM bot_audit
                WHERE platform=? ORDER BY id DESC LIMIT 10''', (ctx.platform,)).fetchall()
        return '📋 ЖУРНАЛ\n' + ('\n'.join(
            f'{r["action"]}: {r["actor_id"]} → {r["target_id"]} | {r["detail"]}' for r in rows) if rows else 'пусто')
    if op == 'статус' and len(args) == 2:
        target = args[1]
        if not VALID_ID.fullmatch(target):
            return 'Формат: админ статус ID'
        with closing(service.db.connect()) as con:
            p = con.execute('SELECT coins,xp,badge FROM players WHERE platform=? AND user_id=?',
                            (ctx.platform, target)).fetchone()
            ban = con.execute('SELECT blocked_until FROM bot_access WHERE platform=? AND user_id=?',
                              (ctx.platform, target)).fetchone()
        if not p:
            return 'Игрок не зарегистрирован в КВЕРАТО.'
        blocked = bool(ban and (not ban['blocked_until'] or ban['blocked_until'] > now))
        return (f'👤 {target} | монеты {p["coins"]} | XP {p["xp"]} | значок {p["badge"]}\n'
                f'Доступ: {"ЗАБЛОКИРОВАН" if blocked else "разрешён"}')
    if who == 'helper':
        return '⛔ Помощник может смотреть статусы, журнал и обращения, но не изменять данные.'
    if op in ('блок', 'разблок'):
        if len(args) < 2 or not VALID_ID.fullmatch(args[1]):
            return 'Формат: админ блок ID часы причина / админ разблок ID'
        target = args[1]
        if target in service.owner_ids:
            return '⛔ Нельзя блокировать владельца КВЕРАТО.'
        if who != 'owner':
            with closing(service.db.connect()) as con:
                other = con.execute('SELECT 1 FROM bot_staff WHERE platform=? AND user_id=?',
                                    (ctx.platform, target)).fetchone()
            if other:
                return '⛔ Управление блокировками сотрудников доступно только владельцу.'
        if op == 'блок':
            hours = parse_positive(args[2], 720) if len(args) >= 3 else None
            if len(args) < 4 or not (hours or args[2] == '0'):
                return 'Формат: админ блок ID 24 причина (0 = бессрочно, 1–720 часов)'
            reason = clean_text(args[3:])
            if len(reason) < 3:
                return 'Укажи причину (минимум 3 символа).'
            until = now + hours * 3600 if hours else 0
            with service.db.transaction() as con:
                registered = con.execute('SELECT 1 FROM players WHERE platform=? AND user_id=?',
                                         (ctx.platform, target)).fetchone()
                if not registered:
                    return 'Игрок не зарегистрирован.'
                con.execute('''INSERT INTO bot_access(platform,user_id,blocked_until,reason,blocked_by,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(platform,user_id) DO UPDATE SET
                    blocked_until=excluded.blocked_until,reason=excluded.reason,
                    blocked_by=excluded.blocked_by,updated_at=excluded.updated_at''',
                    (ctx.platform, target, until, reason, ctx.user_id, now))
                audit(con, ctx, target, 'access:block', f'{until}: {reason}', now)
            return f'🚫 Доступ игрока {target} заблокирован. Причина: {reason}'
        with service.db.transaction() as con:
            changed = con.execute('DELETE FROM bot_access WHERE platform=? AND user_id=?',
                                  (ctx.platform, target)).rowcount
            if changed:
                audit(con, ctx, target, 'access:unblock', 'manual', now)
        return '✅ Доступ восстановлен.' if changed else 'Игрок не заблокирован.'
    if op == 'монеты' and len(args) >= 4:
        target, change = args[1:3]
        if who != 'owner' and target == ctx.user_id:
            return '⛔ Администратор не может изменять собственный баланс.'
        reason = clean_text(args[3:])
        if not VALID_ID.fullmatch(target) or len(reason) < 3 or not re.fullmatch(r'[+=-][0-9]{1,12}', change):
            return 'Формат: админ монеты ID +100|-100|=100 причина'
        amount = int(change[1:]); action = change[0]
        if who != 'owner' and (action == '=' or amount > 100000):
            return '⛔ Изменение суммы превышает полномочия администратора.'
        if action != '=' and not 1 <= amount <= (1000000 if who == 'owner' else 100000):
            return '⚠ Размер одной корректировки: от 1 до допустимого лимита.'
        if action == '=' and (who != 'owner' or amount > MAX_WALLET):
            return '⛔ Установить баланс может только владелец (до 10¹²).'
        with service.db.transaction() as con:
            p = con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                            (ctx.platform, target)).fetchone()
            if not p:
                return 'Игрок не зарегистрирован.'
            old = p['coins']; new = amount if action == '=' else old + (amount if action == '+' else -amount)
            if not 0 <= new <= MAX_WALLET:
                return '⚠ Недостаточно средств или превышен предел кошелька.'
            con.execute('UPDATE players SET coins=? WHERE platform=? AND user_id=?',
                        (new, ctx.platform, target))
            con.execute('''INSERT INTO bot_economy_ledger(platform,actor_id,target_id,delta,source,created_at)
                VALUES(?,?,?,?,?,?)''', (ctx.platform, ctx.user_id, target, new-old, 'admin:'+reason, now))
            audit(con, ctx, target, 'wallet:set', f'{old} → {new}; {reason}', now)
        return f'✅ Баланс {target}: {old} → {new} монет.'
    if op == 'опыт' and len(args) >= 4:
        target, change = args[1:3]
        if who != 'owner' and target == ctx.user_id:
            return '⛔ Администратор не может изменять собственный опыт.'
        reason = clean_text(args[3:])
        if not VALID_ID.fullmatch(target) or len(reason) < 3 or not re.fullmatch(r'[+-][0-9]{1,9}', change):
            return 'Формат: админ опыт ID +100|-100 причина'
        amount = int(change[1:]); limit = 100000 if who == 'owner' else 10000
        if not 1 <= amount <= limit:
            return '⚠ Превышен лимит изменения опыта.'
        with service.db.transaction() as con:
            p = con.execute('SELECT xp FROM players WHERE platform=? AND user_id=?',
                            (ctx.platform, target)).fetchone()
            if not p:
                return 'Игрок не зарегистрирован.'
            new = p['xp'] + (amount if change[0] == '+' else -amount)
            if not 0 <= new <= MAX_WALLET:
                return '⚠ Нельзя списать больше опыта, чем есть.'
            con.execute('UPDATE players SET xp=? WHERE platform=? AND user_id=?',
                        (new, ctx.platform, target))
            audit(con, ctx, target, 'xp:set', f'{p["xp"]} → {new}; {reason}', now)
        return f'✅ Опыт игрока {target}: {new} XP.'
    if op in ('значок', 'снятьзначок', 'достижение', 'снятьдостижение') and len(args) == 3:
        target,key = args[1],args[2].lower()
        if who != 'owner' and target == ctx.user_id:
            return '⛔ Администратор не может выдавать награды самому себе.'
        is_badge = 'значок' in op
        if not VALID_ID.fullmatch(target) or key not in (BADGES if is_badge else ACHIEVEMENTS):
            return '⚠ Неизвестный игрок или ключ награды.'
        table = 'purchases' if is_badge else 'achievements'
        column = 'item_key' if is_badge else 'achievement_key'
        stamp = 'purchased_at' if is_badge else 'earned_at'
        revoke = op.startswith('снять')
        with service.db.transaction() as con:
            p = con.execute('SELECT badge FROM players WHERE platform=? AND user_id=?',
                            (ctx.platform, target)).fetchone()
            if not p:
                return 'Игрок не зарегистрирован.'
            if revoke:
                changed = con.execute(f'DELETE FROM {table} WHERE platform=? AND user_id=? AND {column}=?',
                                      (ctx.platform, target, key)).rowcount
                if is_badge and changed and p['badge'] == BADGES[key]:
                    con.execute('UPDATE players SET badge=? WHERE platform=? AND user_id=?',
                                ('Игрок', ctx.platform, target))
            else:
                changed = con.execute(f'''INSERT OR IGNORE INTO {table}
                    (platform,user_id,{column},{stamp}) VALUES(?,?,?,?)''',
                    (ctx.platform, target, key, now)).rowcount
            if changed:
                audit(con, ctx, target, op, key, now)
        return '✅ Награда изменена.' if changed else 'ℹ️ Награда уже в указанном состоянии.'
    if who != 'owner':
        return '⛔ Эта команда доступна только владельцу КВЕРАТО.'
    if op == 'сотрудник' and len(args) == 3:
        target, rank = args[1], args[2].lower()
        if not VALID_ID.fullmatch(target) or rank not in ('admin', 'helper', 'off'):
            return 'Формат: админ сотрудник ID admin|helper|off'
        if target in service.owner_ids:
            return '⛔ Роль владельца задаётся только в .env.'
        with service.db.transaction() as con:
            if not con.execute('SELECT 1 FROM players WHERE platform=? AND user_id=?',
                               (ctx.platform, target)).fetchone():
                return 'Игрок не зарегистрирован.'
            if rank == 'off':
                con.execute('DELETE FROM bot_staff WHERE platform=? AND user_id=?',
                            (ctx.platform, target))
            else:
                con.execute('''INSERT INTO bot_staff(platform,user_id,role,appointed_by,appointed_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(platform,user_id) DO UPDATE SET role=excluded.role,
                    appointed_by=excluded.appointed_by,appointed_at=excluded.appointed_at''',
                    (ctx.platform, target, rank, ctx.user_id, now))
            audit(con, ctx, target, 'staff:set', rank, now)
        return f'✅ Права {target}: {rank}.'
    if op == 'функция' and len(args) == 3:
        feature, setting = args[1].lower(), args[2].lower()
        if feature not in FEATURES or setting not in ('on','off'):
            return 'Формат: админ функция <раздел> on|off. Доступные разделы: ' + ', '.join(sorted(FEATURES))
        enabled = int(setting == 'on')
        with service.db.transaction() as con:
            con.execute('''INSERT INTO bot_flags(platform,feature,enabled,updated_by,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(platform,feature) DO UPDATE SET enabled=excluded.enabled,
                updated_by=excluded.updated_by,updated_at=excluded.updated_at''',
                (ctx.platform, feature, enabled, ctx.user_id, now))
            audit(con, ctx, feature, 'feature:set', setting, now)
        return f'✅ Функция {feature}: {setting}.'
    if op == 'промо' and len(args) == 5:
        code = args[1].upper()
        reward = parse_positive(args[2], 5000)
        uses = parse_positive(args[3], 1000)
        hours = parse_positive(args[4], 720)
        if not VALID_PROMO.fullmatch(code) or not all((reward, uses, hours)):
            return 'Формат: админ промо CODE 100 20 24 (до 5000 монет, 1000 использований, 720 часов)'
        with service.db.transaction() as con:
            existing = con.execute('SELECT 1 FROM bot_promos WHERE platform=? AND code=?',
                                   (ctx.platform, code)).fetchone()
            if existing:
                return '⚠ Промокод с таким названием уже существует.'
            con.execute('''INSERT INTO bot_promos(platform,code,reward,max_uses,expires_at,created_by,created_at)
                VALUES(?,?,?,?,?,?,?)''', (ctx.platform, code, reward, uses, now+hours*3600, ctx.user_id, now))
            audit(con, ctx, code, 'promo:create', f'{reward} x {uses}; {hours}ч', now)
        return f'✅ Промокод {code} создан: {reward} монет × {uses} использований.'
    if op == 'закрытьпромо' and len(args) == 2:
        code = args[1].upper()
        with service.db.transaction() as con:
            changed = con.execute('UPDATE bot_promos SET active=0 WHERE platform=? AND code=? AND active=1',
                                  (ctx.platform, code)).rowcount
            if changed:
                audit(con, ctx, code, 'promo:disable', 'manual', now)
        return '✅ Промокод отключён.' if changed else 'ℹ️ Промокод не найден или отключён.'
    return '⚠ Неверная административная команда. Напиши «админ» для списка.'
