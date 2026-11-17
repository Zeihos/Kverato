"""КВЕРАТО v0.5: duel escrow, daily reputation, optional chat greetings.

All balances are virtual coins. Mutations use SQLite BEGIN IMMEDIATE, ensuring
concurrent accept/cancel/expire requests cannot duplicate funds.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import re

from .features import record_game, suffix
from .escrow import credit_escrow, escrow_capacity

DUEL_TTL = 10 * 60
MAX_DUEL_BET = 5000
WELCOME_MAX_LENGTH = 300


def _positive_id(value: str) -> bool:
    return bool(re.fullmatch(r'[1-9][0-9]{0,18}', value))


def _amount(value: str) -> int | None:
    if not value.isascii() or not value.isdecimal() or not (1 <= len(value) <= 8):
        return None
    number = int(value)
    return number if 1 <= number <= MAX_DUEL_BET else None


def _duel_id(args: list[str]) -> int | None:
    if len(args) != 1 or not args[0].isascii() or not args[0].isdecimal() or len(args[0]) > 14:
        return None
    result = int(args[0])
    return result if 1 <= result <= 10**14 else None


def expire_duels(con, now: int) -> int:
    """Refund expired pending challenges exactly once. Called on incoming messages."""
    expired = con.execute('''SELECT id,platform,challenger_id,stake FROM duels
        WHERE status='pending' AND expires_at <= ?''', (now,)).fetchall()
    settled = 0
    for duel in expired:
        # A stake remains reserved if both wallet and bank are full. It can
        # be refunded once the player frees space; no silent coin overflow.
        if escrow_capacity(con, duel['platform'], duel['challenger_id']) < duel['stake']:
            continue
        if credit_escrow(con, duel['platform'], duel['challenger_id'], duel['stake']) is None:
            continue
        changed = con.execute('''UPDATE duels SET status='expired', finished_at=?
            WHERE id=? AND status='pending' ''', (now, duel['id'])).rowcount
        if changed:
            settled += 1
    return settled


def _duel_command(service, ctx, cmd: str, args: list[str], now: int) -> str:
    if not ctx.group:
        return '⚔️ Дуэли доступны только в группах Telegram: !дуэль ID 50.'
    db = service.db
    if cmd == 'duel' and not args:
        with closing(db.connect()) as con:
            rows = con.execute('''SELECT id, challenger_id, opponent_id, stake, expires_at
                FROM duels WHERE platform=? AND chat_id=? AND status='pending'
                AND (challenger_id=? OR opponent_id=?) ORDER BY id DESC LIMIT 5''',
                (ctx.platform, ctx.chat_id, ctx.user_id, ctx.user_id)).fetchall()
        if not rows:
            return ('⚔️ ДУЭЛИ КВЕРАТО\n!дуэль ID 50 — вызвать игрока; можно ответить на сообщение: !дуэль 50\n'
                    '!принять НОМЕР / !отклонить НОМЕР / !отменитьдуэль НОМЕР\n'
                    'Вызов действует 10 минут. Ставка 1–5000 виртуальных монет.')
        return '⚔️ ТВОИ АКТИВНЫЕ ВЫЗОВЫ\n' + '\n'.join(
            f'#{r["id"]}: {r["challenger_id"]} → {r["opponent_id"]}, {r["stake"]} монет, осталось {max(0, r["expires_at"] - now)} сек.'
            for r in rows)
    if cmd == 'duel':
        if len(args) == 2 and _positive_id(args[0]):
            target, value = args
        elif ctx.target_id:
            target = ctx.target_id
            value = next((a for a in reversed(args) if len(a) <= 8 and a.isascii() and a.isdecimal()), '')
        else:
            target, value = ('', '')
        if not _positive_id(target) or _amount(value) is None:
            return '⚔️ Формат: !дуэль ID 50 или ответь на сообщение: !дуэль 50.'
        if target == ctx.user_id:
            return '⛔ Нельзя вызвать на дуэль самого себя.'
        bet = _amount(value)
        with db.transaction() as con:
            registered = con.execute('''SELECT 1 FROM players WHERE platform=? AND user_id=?''',
                                     (ctx.platform, target)).fetchone()
            if not registered:
                return '⚔️ Сопернику нужно сначала написать КВЕРАТО «старт».'
            occupied = con.execute('''SELECT id FROM duels WHERE platform=? AND status='pending' AND
                (challenger_id IN (?,?) OR opponent_id IN (?,?)) LIMIT 1''',
                (ctx.platform, ctx.user_id, target, ctx.user_id, target)).fetchone()
            if occupied:
                return '⌛ У одного из игроков уже есть активная дуэль. Заверши её или подожди 10 минут.'
            debited = con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND user_id=? AND coins>=?''',
                                  (bet, ctx.platform, ctx.user_id, bet)).rowcount
            if not debited:
                return '❌ Недостаточно монет на кошельке для вызова.'
            result = con.execute('''INSERT INTO duels(platform,chat_id,challenger_id,opponent_id,stake,
                status,created_at,expires_at) VALUES (?,?,?,?,?,'pending',?,?)''',
                (ctx.platform, ctx.chat_id, ctx.user_id, target, bet, now, now + DUEL_TTL))
            number = result.lastrowid
        return (f'⚔️ Дуэль #{number}: [id{ctx.user_id}|Игрок] вызывает [id{target}|соперника]!\n'
                f'💰 Ставка: {bet} монет с каждого. Твоя ставка зарезервирована.\n'
                f'Соперник: !принять {number} или !отклонить {number}. Время: 10 мин.')

    number = _duel_id(args)
    if number is None:
        return ('⚔️ Укажи номер вызова: !принять 1, !отклонить 1 '
                'или !отменитьдуэль 1. Посмотреть номер: !дуэль.')
    with db.transaction() as con:
        row = con.execute('SELECT * FROM duels WHERE id=? AND platform=? AND chat_id=?',
                          (number, ctx.platform, ctx.chat_id)).fetchone()
        if row is None:
            return '❌ Дуэль с таким номером в этой беседе не найдена.'
        if row['status'] != 'pending':
            return f'⚔️ Дуэль #{number} уже завершена ({row["status"]}).'
        challenger, opponent, bet = row['challenger_id'], row['opponent_id'], row['stake']
        if row['expires_at'] <= now:
            return '⌛ Срок дуэли истёк. Освободи место в кошельке или банке для возврата ставки.'
        if cmd == 'cancelduel':
            if ctx.user_id != challenger:
                return '⛔ Отменить вызов может только его создатель.'
            state = 'cancelled'
        elif cmd == 'declineduel':
            if ctx.user_id != opponent:
                return '⛔ Отклонить вызов может только приглашённый игрок.'
            state = 'declined'
        else:
            if ctx.user_id != opponent:
                return '⛔ Принять вызов может только приглашённый игрок.'
            target_balance = con.execute('''SELECT coins FROM players WHERE platform=? AND user_id=?''',
                                         (ctx.platform, opponent)).fetchone()
            if not target_balance or target_balance['coins'] < bet:
                return f'❌ Для принятия дуэли нужно {bet} монет в кошельке.'
            winner = challenger if service.randbelow(2) == 0 else opponent
            loser = opponent if winner == challenger else challenger
            # The opponent's stake is debited before payment; account for
            # that freed space when the opponent is the winner.
            if escrow_capacity(con, ctx.platform, winner,
                               debit=bet if winner == opponent else 0) < 2 * bet:
                return '⚠ Недостаточно места для выигрыша в кошельке и банке победителя.'
            con.execute('''UPDATE players SET coins=coins-? WHERE platform=? AND user_id=?''',
                        (bet, ctx.platform, opponent))
            payment = credit_escrow(con, ctx.platform, winner, bet * 2)
            if payment is None:
                raise RuntimeError('Duel payout invariant')
            con.execute('''UPDATE duels SET status='accepted', finished_at=?, winner_id=? WHERE id=?''',
                        (now, winner, number))
            # Record both competitors atomically in existing statistics and daily quests.
            won_ctx = replace(ctx, user_id=winner, target_id=None)
            lost_ctx = replace(ctx, user_id=loser, target_id=None)
            new = record_game(con, won_ctx, True, now)
            record_game(con, lost_ctx, False, now)
            return (f'⚔️ Дуэль #{number} завершена!\n🏆 Победитель: [id{winner}|игрок].\n'
                    f'💰 Выигрыш: +{bet} монет, проигрыш: −{bet} монет.\n'
                    f'Сумма ставок: {bet*2} виртуальных монет.'
                    + (f' В банк зачислено: {payment[1]}.' if payment[1] else '') + suffix(new))
        if escrow_capacity(con, ctx.platform, challenger) < bet:
            return '⚠ Для возврата ставки освободи место в кошельке или банке.'
        payment = credit_escrow(con, ctx.platform, challenger, bet)
        if payment is None:
            raise RuntimeError('Duel refund invariant')
        con.execute('UPDATE duels SET status=?, finished_at=? WHERE id=?', (state, now, number))
        return (f'⚔️ Дуэль #{number} {"отменена" if state=="cancelled" else "отклонена"}. '
                f'Ставка {bet} монет возвращена создателю.'
                + (f' В банк зачислено: {payment[1]}.' if payment[1] else ''))


def extra_command(service, ctx, cmd: str, args: list[str], now: int) -> str | None:
    db = service.db
    if cmd in ('duel', 'acceptduel', 'declineduel', 'cancelduel'):
        return _duel_command(service, ctx, cmd, args, now)
    if cmd == 'duelhistory':
        if not ctx.group:
            return '⚔️ История дуэлей доступна только в беседах.'
        with closing(db.connect()) as con:
            rows = con.execute('''SELECT id,challenger_id,opponent_id,stake,status,winner_id
                FROM duels WHERE platform=? AND chat_id=? AND
                (challenger_id=? OR opponent_id=?) ORDER BY id DESC LIMIT 5''',
                (ctx.platform, ctx.chat_id, ctx.user_id, ctx.user_id)).fetchall()
        if not rows:
            return '⚔️ У тебя пока нет дуэлей в этой беседе.'
        return '⚔️ ИСТОРИЯ ДУЭЛЕЙ\n' + '\n'.join(
            f'#{r["id"]} • {r["challenger_id"]} vs {r["opponent_id"]} • '
            f'{r["stake"]} монет • {r["status"]}' +
            (f' • победил {r["winner_id"]}' if r['winner_id'] else '')
            for r in rows)

    if cmd == 'rep':
        with closing(db.connect()) as con:
            row = service._player(con, ctx)
            used = con.execute('''SELECT 1 FROM reputation_events WHERE platform=? AND giver_id=? AND utc_day=?''',
                               (ctx.platform, ctx.user_id, now // 86400)).fetchone()
        return (f'🤝 Репутация: {row["reputation"]}.\n'
                f'Благодарность сегодня (UTC): {"уже отправлена" if used else "доступна"}.\n'
                'Чтобы поблагодарить: уважить ID (или !уважить ответом в беседе).')
    if cmd == 'giverep':
        target = (args[0] if len(args) == 1 and _positive_id(args[0])
                  else ctx.target_id)
        if not target or not _positive_id(target):
            return '🤝 Формат: уважить ID, либо ответь на сообщение: !уважить.'
        if target == ctx.user_id:
            return '⛔ Нельзя повысить репутацию самому себе.'
        with db.transaction() as con:
            registered = con.execute('''SELECT 1 FROM players WHERE platform=? AND user_id=?''',
                                     (ctx.platform, target)).fetchone()
            if not registered:
                return '🤝 Игрок ещё не зарегистрирован в КВЕРАТО.'
            changed = con.execute('''INSERT OR IGNORE INTO reputation_events
                (platform,giver_id,recipient_id,utc_day,created_at) VALUES (?,?,?,?,?)''',
                (ctx.platform, ctx.user_id, target, now // 86400, now)).rowcount
            if not changed:
                return '⌛ Сегодня (UTC) ты уже выразил уважение игроку. Возвращайся завтра.'
            con.execute('''UPDATE players SET reputation=reputation+1 WHERE platform=? AND user_id=?''',
                        (ctx.platform, target))
        return f'🤝 Репутация игрока [id{target}|получатель] повышена на +1!'
    if cmd == 'toprep':
        with closing(db.connect()) as con:
            rows = con.execute('''SELECT display_name, reputation FROM players WHERE platform=?
                ORDER BY reputation DESC, created_at ASC LIMIT 10''', (ctx.platform,)).fetchall()
        return '🤝 ТОП ПО РЕПУТАЦИИ\n' + '\n'.join(
            f'{i}. {r["display_name"]}: {r["reputation"]}' for i, r in enumerate(rows, 1))
    return None
