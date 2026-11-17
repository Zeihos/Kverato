""" deterministic rules for vehicle racing and peer tournaments.

Every change is within a SQLite BEGIN IMMEDIATE transaction. All races use
virtual coins only, no monetary cash-out. No background scheduler required:
expired challenges and closed registrations settle on incoming bot activity.
"""
from __future__ import annotations

from contextlib import closing
from types import SimpleNamespace
import re

from .features import record_game
from .escrow import escrow_capacity, credit_escrow
from .v05 import _positive_id
from .v07 import MAX_WALLET
from .v09 import VEHICLES, VEHICLE_NAMES, _coins

RACE_ENTRY = 100
SOLO_COOLDOWN = 3600
RACE_BET_MAX = 2000
RACE_TTL = 10 * 60
TOURNAMENT_ENTRY = 150
TOURNAMENT_LIMIT = 8
TOURNAMENT_SIGNUP = 3600
# speed / grip / offroad; the same vehicle is good on different tracks.
VEHICLE_POWER = {'scooter': (60, 78, 34), 'coupe': (93, 58, 42), 'rover': (70, 73, 97)}
VEHICLE_POWER.update({'moped': (65, 75, 32), 'hatch': (69, 71, 43), 'sedan': (77, 73, 48), 'wagon': (76, 72, 54), 'rally': (88, 90, 72), 'bike': (100, 86, 38), 'pickup': (72, 67, 83), 'muscle': (108, 65, 43), 'suv': (77, 79, 81), 'sport': (120, 99, 40), 'buggy': (87, 82, 115), 'electric': (103, 104, 54), 'super': (133, 111, 47), 'hyper': (150, 117, 52)})
TRACKS = {
    'спринт': (0, 'Городской спринт'),
    'кольцо': (1, 'Кольцевая трасса'),
    'периметр': (2, 'Периметр'),
}
TRACK_ROTATION = ('спринт', 'кольцо', 'периметр')


def _owned(con, ctx, key: str):
    return con.execute('''SELECT * FROM player_vehicles WHERE platform=? AND user_id=?
        AND vehicle_key=?''', (ctx.platform, ctx.user_id, key)).fetchone()


def _power(key: str, tuned: int, track: str, roll: int) -> int:
    # Tuning is a real but bounded competitive advantage.
    return VEHICLE_POWER[key][TRACKS[track][0]] + (12 if tuned else 0) + roll


def _progress(con, platform: str, user_id: str, score: int, won: bool, now: int,
              *, solo: bool = False) -> None:
    con.execute('''INSERT INTO race_progress(platform,user_id,races,wins,rating,best_score,last_solo_at)
        VALUES(?,?,1,?,?,?,?)
        ON CONFLICT(platform,user_id) DO UPDATE SET
        races=races+1, wins=wins+excluded.wins,
        rating=MAX(0,rating+?), best_score=MAX(best_score,excluded.best_score),
        last_solo_at=CASE WHEN ? THEN ? ELSE last_solo_at END''',
        (platform,user_id,int(won),25 if won else 0,score,now if solo else 0,
         25 if won else -7,int(solo),now))


def _owner_blocked(con, platform: str, uid: str, now: int) -> bool:
    row=con.execute('SELECT blocked_until FROM bot_access WHERE platform=? AND user_id=?',
                    (platform,uid)).fetchone()
    return bool(row and (row['blocked_until']==0 or row['blocked_until']>now))


def expire_races(con, now: int) -> int:
    """Exactly-once reimbursement for expired pending PVP stakes."""
    rows=con.execute('''SELECT * FROM race_challenges
        WHERE status='pending' AND expires_at<=?''', (now,)).fetchall()
    settled=0
    for row in rows:
        if escrow_capacity(con,row['platform'],row['challenger_id']) < row['stake']:
            continue
        if credit_escrow(con,row['platform'],row['challenger_id'],row['stake']) is None:
            continue
        if con.execute('''UPDATE race_challenges SET status='expired',finished_at=?
                        WHERE id=? AND status='pending' ''',(now,row['id'])).rowcount:
            settled+=1
    return settled


def _int_bet(s: str) -> int | None:
    if not re.fullmatch(r'[0-9]{1,8}',s):
        return None
    value=int(s)
    return value if 1<=value<=RACE_BET_MAX else None


def _positive_number(s: str) -> int | None:
    if not re.fullmatch(r'[0-9]{1,12}',s):
        return None
    value=int(s)
    return value if value>0 else None


def stats_command(service, ctx, cmd: str) -> str:
    with closing(service.db.connect()) as con:
        if cmd=='racetop':
            rows=con.execute('''SELECT p.display_name,s.wins,s.rating FROM race_progress s
                JOIN players p ON p.platform=s.platform AND p.user_id=s.user_id
                WHERE s.platform=? ORDER BY s.rating DESC,s.wins DESC,p.created_at ASC LIMIT 10''',
                (ctx.platform,)).fetchall()
            return '🏎 ТОП ГОНЩИКОВ\n'+('\n'.join(f'{i}. {r["display_name"]}: {r["rating"]} рейтинга, {r["wins"]} побед' for i,r in enumerate(rows,1)) if rows else 'Пока нет участников.')
        row=con.execute('SELECT * FROM race_progress WHERE platform=? AND user_id=?',
                        (ctx.platform,ctx.user_id)).fetchone()
    return ('🏁 ТВОЯ КАРЬЕРА ГОНЩИКА\n'
            f'Заездов: {row["races"] if row else 0}\n'
            f'Побед: {row["wins"] if row else 0}\n'
            f'Рейтинг: {row["rating"] if row else 0}\n'
            f'Лучший результат: {row["best_score"] if row else 0}\n'
            'Гонка скутер спринт / турнир / топгонщиков.')


def race_command(service, ctx, args: list[str], now: int) -> str:
    if not args:
        return ('🏁 ГОНКИ КВЕРАТО\n'
                'гонка скутер спринт — одиночная гонка (100 монет; раз в час)\n'
                'Трассы: спринт / кольцо / периметр.\n'
                f'На выбор {len(VEHICLES)} моделей. Список: транспорт каталог.\n'
                'Тюнинг: +12 к результату. Победа: 180 монет призом, ничья: возврат 100.\n'
                'В беседе: !заезд ID 100 купе / !заезд принять НОМЕР скутер.')
    if len(args)!=2:
        return '⚠ Формат: гонка МАШИНА спринт|кольцо|периметр. Все модели: транспорт каталог.'
    key=VEHICLE_NAMES.get(args[0].casefold())
    track=args[1].casefold()
    if key not in VEHICLE_POWER or track not in TRACKS:
        return '⚠ Выбери свою машину и трассу: спринт / кольцо / периметр.'
    with service.db.transaction() as con:
        car=_owned(con,ctx,key)
        if car is None:
            return '❌ Этого транспорта нет в твоём гараже. Сначала купи его.'
        stat=con.execute('''SELECT last_solo_at FROM race_progress WHERE platform=? AND user_id=?''',
                         (ctx.platform,ctx.user_id)).fetchone()
        if stat and stat['last_solo_at'] and now-stat['last_solo_at']<SOLO_COOLDOWN:
            return f'⌛ Следующая одиночная гонка через {SOLO_COOLDOWN-(now-stat["last_solo_at"])} сек.'
        player=con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                           (ctx.platform,ctx.user_id)).fetchone()
        if player['coins']<RACE_ENTRY:
            return '❌ Для участия нужно 100 монет.'
        own=_power(key,car['tuned'],track,service.randbelow(41))
        bot=72+service.randbelow(41)
        won=own>bot
        payout=180 if won else (100 if own==bot else 0)
        delta=payout-RACE_ENTRY
        if player['coins']+delta>MAX_WALLET:
            return '⚠ Кошелёк переполнен, полученная награда не поместится.'
        if delta and not _coins(con,ctx,delta,now,'solo-race'):
            raise RuntimeError('Race balance invariant')
        _progress(con,ctx.platform,ctx.user_id,own,won,now,solo=True)
        record_game(con,ctx,won,now)
        from .v13 import activity as season_activity
        season_activity(con,ctx,'race',now)
    result='🏆 Победа! Чистый выигрыш +80 монет.' if won else ('🤝 Ничья! Монеты возвращены.' if own==bot else '🏁 Поражение. −100 монет.')
    return f'🏁 {TRACKS[track][1]} | {VEHICLES[key].title}\nТвой результат: {own} · Соперник: {bot}\n{result}'


def challenge_command(service,ctx,args:list[str],now:int)->str:
    if not ctx.group:
        return '⚔️ Заезды один на один доступны в беседах VK: !заезд ID 100 купе.'
    if not args:
        with closing(service.db.connect()) as con:
            rows=con.execute('''SELECT id,challenger_id,opponent_id,stake FROM race_challenges
                WHERE platform=? AND chat_id=? AND status='pending' AND
                (challenger_id=? OR opponent_id=?) ORDER BY id DESC LIMIT 5''',
                (ctx.platform,ctx.chat_id,ctx.user_id,ctx.user_id)).fetchall()
        return ('🏎 АКТИВНЫЕ ЗАЕЗДЫ\n'+('\n'.join(f'#{r["id"]} {r["challenger_id"]} → {r["opponent_id"]} · {r["stake"]} монет' for r in rows) if rows else 'Нет активных вызовов.')+
                '\n!заезд ID 100 купе · !заезд принять НОМЕР МАШИНА · !заезд отменить НОМЕР')
    action=args[0].casefold()
    if action in ('принять','отклонить','отменить'):
        if len(args) not in (2,3) or (action=='принять') != (len(args)==3):
            return '⚠ Формат: !заезд принять НОМЕР скутер или !заезд отклонить/отменить НОМЕР.'
        num=_positive_number(args[1]);key=VEHICLE_NAMES.get(args[2].casefold()) if action=='принять' else None
        if num is None or (action=='принять' and key not in VEHICLE_POWER):
            return '⚠ Укажи номер вызова и свою машину.'
        with service.db.transaction() as con:
            row=con.execute('''SELECT * FROM race_challenges WHERE id=? AND platform=? AND chat_id=?''',
                            (num,ctx.platform,ctx.chat_id)).fetchone()
            if not row:
                return '❌ Вызов не найден в этой беседе.'
            if row['status']!='pending':
                return '⌛ Этот вызов уже завершён.'
            if row['expires_at']<=now:
                return '⌛ Срок вызова закончился. Ставка вернётся при проверке.'
            if action=='отменить' and ctx.user_id!=row['challenger_id']:
                return '⛔ Отменить заезд может только его создатель.'
            if action in ('принять','отклонить') and ctx.user_id!=row['opponent_id']:
                return '⛔ Ответить может только приглашённый соперник.'
            if action!='принять':
                if escrow_capacity(con,ctx.platform,row['challenger_id']) < row['stake']:
                    return '⚠ Для возврата ставки освободи место в кошельке или банке.'
                payment=credit_escrow(con,ctx.platform,row['challenger_id'],row['stake'])
                if payment is None:
                    raise RuntimeError('Race refund invariant')
                con.execute('''UPDATE race_challenges SET status=?,finished_at=? WHERE id=?''',
                            ('cancelled' if action=='отменить' else 'declined',now,num))
                return (f'✅ Заезд #{num} отменён, {row["stake"]} монет возвращены создателю.'
                        + (f' В банк зачислено: {payment[1]}.' if payment[1] else ''))
            car=_owned(con,ctx,key)
            if car is None:
                return '❌ У тебя нет выбранного транспорта.'
            other=con.execute('SELECT coins FROM players WHERE platform=? AND user_id=?',
                              (ctx.platform,ctx.user_id)).fetchone()
            if not other or other['coins']<row['stake']:
                return '❌ Для принятия не хватает монет.'
            # Both contenders use a sprint track and a bounded random roll.
            a=_power(row['vehicle_key'],row['tuned'],'спринт',service.randbelow(41))
            b=_power(key,car['tuned'],'спринт',service.randbelow(41))
            if a==b:  # no infinite pending ties; reroll once via fixed tie-break.
                a+=1
            winner=row['challenger_id'] if a>b else ctx.user_id
            prize=2*row['stake']
            if escrow_capacity(con,ctx.platform,winner,
                               debit=row['stake'] if winner==ctx.user_id else 0)<prize:
                return '⚠ Недостаточно места для выигрыша в кошельке и банке победителя.'
            if not _coins(con,ctx,-row['stake'],now,'pvp-race-entry'):
                raise RuntimeError('Race entry debit failure')
            payment=credit_escrow(con,ctx.platform,winner,prize)
            if payment is None:
                raise RuntimeError('Race payout invariant')
            con.execute('''UPDATE race_challenges SET status='finished',winner_id=?,finished_at=? WHERE id=?''',
                        (winner,now,num))
            _progress(con,ctx.platform,row['challenger_id'],a,winner==row['challenger_id'],now)
            _progress(con,ctx.platform,ctx.user_id,b,winner==ctx.user_id,now)
            # Record the both players' game stats without account swapping.
            from dataclasses import replace
            record_game(con,replace(ctx,user_id=row['challenger_id']),winner==row['challenger_id'],now)
            record_game(con,ctx,winner==ctx.user_id,now)
        return (f'🏁 Заезд #{num}! {a} : {b}\n🏆 Победил [id{winner}|игрок]! Приз: {prize} монет.'
                + (f' В банк: {payment[1]}.' if payment[1] else ''))

    # Opening an escrowed challenge.
    if len(args)==3 and _positive_id(args[0]):
        target,stake_str,car_str=args
    elif ctx.target_id:
        target=ctx.target_id
        challenge_args=args[1:] if len(args)==3 and args[0].startswith('[id') else args
        stake_str=challenge_args[0] if len(challenge_args)==2 else ''
        car_str=challenge_args[1] if len(challenge_args)==2 else ''
    else:
        target,stake_str,car_str=args if len(args)==3 else ('','','')
    bet=_int_bet(stake_str)
    key=VEHICLE_NAMES.get(car_str.casefold())
    if not _positive_id(target) or bet is None or key not in VEHICLE_POWER:
        return '⚠ Формат: !заезд ID 100 купе (или ответ: !заезд 100 купе).'
    if target==ctx.user_id:
        return '⛔ Нельзя вызвать самого себя.'
    with service.db.transaction() as con:
        car=_owned(con,ctx,key)
        if car is None:
            return '❌ У тебя нет выбранного транспорта.'
        if not con.execute('SELECT 1 FROM players WHERE platform=? AND user_id=?',
                           (ctx.platform,target)).fetchone():
            return '❌ Соперник ещё не зарегистрирован в боте.'
        if _owner_blocked(con,ctx.platform,target,now):
            return '⛔ Соперник не может участвовать в заезде.'
        busy=con.execute('''SELECT 1 FROM race_challenges WHERE platform=? AND status='pending'
            AND (challenger_id IN (?,?) OR opponent_id IN (?,?)) LIMIT 1''',
            (ctx.platform,ctx.user_id,target,ctx.user_id,target)).fetchone()
        if busy:
            return '⌛ У одного из игроков уже есть активный заезд.'
        if not _coins(con,ctx,-bet,now,'pvp-race-hold'):
            return '❌ Не хватает монет на ставку.'
        new=con.execute('''INSERT INTO race_challenges(platform,chat_id,challenger_id,opponent_id,
            vehicle_key,tuned,stake,status,created_at,expires_at)
            VALUES(?,?,?,?,?,?,?,'pending',?,?)''',
            (ctx.platform,ctx.chat_id,ctx.user_id,target,key,int(car['tuned']),bet,now,now+RACE_TTL))
        number=new.lastrowid
    return (f'🏁 Заезд #{number}: [id{ctx.user_id}|Игрок] вызывает [id{target}|соперника]!\n'
            f'{VEHICLES[key].title} · Ставка {bet} монет зарезервирована.\n'
            f'Соперник: !заезд принять {number} МАШИНА (транспорт каталог). Время: 10 минут.')


def _tournament_day(now:int)->int:
    return now//86400


def _settle_tournament(con,service,platform,day,now)->str:
    row=con.execute('SELECT * FROM race_tournaments WHERE platform=? AND utc_day=?',
                    (platform,day)).fetchone()
    if not row or row['status']!='open':
        return 'Турнир уже завершён.'
    contenders=con.execute('''SELECT * FROM race_tournament_entries WHERE platform=? AND utc_day=?
        ORDER BY joined_at,user_id''',(platform,day)).fetchall()
    if len(contenders)<2:
        if any(escrow_capacity(con,platform,x['user_id']) < x['entry_fee'] for x in contenders):
            return '⚠ Для возврата взноса нужно освободить место в кошельке или банке.'
        for x in contenders:
            if credit_escrow(con,platform,x['user_id'],x['entry_fee']) is None:
                raise RuntimeError('Tournament refund invariant')
            con.execute('''UPDATE race_tournament_entries SET prize=? WHERE platform=? AND utc_day=? AND user_id=?''',
                        (x['entry_fee'],platform,day,x['user_id']))
        con.execute('''UPDATE race_tournaments SET status='cancelled',finished_at=?
                      WHERE platform=? AND utc_day=?''',(now,platform,day))
        return '📅 Турнир отменён: участников меньше двух. Взнос возвращён.'
    ranking=[]
    for x in contenders:
        score=_power(x['vehicle_key'],x['tuned'],row['track_key'],service.randbelow(41))
        ranking.append((score,x['user_id'],x))
    ranking.sort(key=lambda x:(-x[0],x[1]))
    pool=sum(x['entry_fee'] for x in contenders)
    prizes=(pool*70//100,pool-pool*70//100)
    # Verify payout limits before any irreversible tournament state write.
    for (score,uid,x),prize in zip(ranking,prizes):
        if escrow_capacity(con,platform,uid)<prize:
            return '⚠ У победителя заполнены кошелёк и банк. Турнир пока не завершён.'
    for score,uid,x in ranking:
        con.execute('''UPDATE race_tournament_entries SET score=? WHERE platform=? AND utc_day=? AND user_id=?''',
                    (score,platform,day,uid))
    for (score,uid,x),prize in zip(ranking,prizes):
        if credit_escrow(con,platform,uid,prize) is None:
            raise RuntimeError('Tournament payout invariant')
        con.execute('''UPDATE race_tournament_entries SET prize=? WHERE platform=? AND utc_day=? AND user_id=?''',
                    (prize,platform,day,uid))
    for score,uid,x in ranking:
        _progress(con,platform,uid,score,uid==ranking[0][1],now)
        record_game(con,SimpleNamespace(platform=platform,user_id=uid),uid==ranking[0][1],now)
    con.execute('''UPDATE race_tournaments SET status='finished',finished_at=?,winner_id=?,runner_up_id=?
        WHERE platform=? AND utc_day=?''',(now,ranking[0][1],ranking[1][1],platform,day))
    return (f'🏆 Турнир завершён! Трасса: {TRACKS[row["track_key"]][1]}\n'
            f'🥇 [id{ranking[0][1]}|Победитель] — {ranking[0][0]} очков, +{prizes[0]} монет\n'
            f'🥈 [id{ranking[1][1]}|Второе место] — {ranking[1][0]} очков, +{prizes[1]} монет\n'
            f'Участников: {len(contenders)}. Фонд: {pool} монет (из вступительных взносов).')


def tournament_command(service,ctx,args:list[str],now:int)->str:
    day=_tournament_day(now)
    action=args[0].casefold() if args else ''
    if action not in ('','записаться','провести','итоги'):
        return '🏆 Команды: турнир / турнир записаться купе / турнир провести / турнир итоги.'
    with service.db.transaction() as con:
        row=con.execute('SELECT * FROM race_tournaments WHERE platform=? AND utc_day=?',
                        (ctx.platform,day)).fetchone()
        if action=='записаться':
            if len(args)!=2:
                return '⚠ Формат: турнир записаться купе.'
            key=VEHICLE_NAMES.get(args[1].casefold())
            if key not in VEHICLE_POWER:
                return '⚠ Неизвестная машина. Посмотри «транспорт каталог».'
            car=_owned(con,ctx,key)
            if car is None:
                return '❌ У тебя нет этого транспорта.'
            if row and row['status']!='open':
                return '🏆 Турнир дня уже завершился. Следующий — завтра (UTC).'
            if row and row['closes_at']<=now:
                return '⌛ Регистрация закрыта. Введи «турнир провести» для итогов.'
            if con.execute('''SELECT 1 FROM race_tournament_entries WHERE platform=? AND utc_day=? AND user_id=?''',
                           (ctx.platform,day,ctx.user_id)).fetchone():
                return '✅ Ты уже записан на сегодняшний турнир.'
            count=con.execute('SELECT COUNT(*) FROM race_tournament_entries WHERE platform=? AND utc_day=?',
                              (ctx.platform,day)).fetchone()[0]
            if count>=TOURNAMENT_LIMIT:
                return '⌛ Все 8 мест уже заняты.'
            if not _coins(con,ctx,-TOURNAMENT_ENTRY,now,'tournament-entry'):
                return '❌ Для записи нужно 150 монет.'
            if row is None:
                track=TRACK_ROTATION[day%3]
                con.execute('''INSERT INTO race_tournaments(platform,utc_day,track_key,opens_at,closes_at)
                    VALUES(?,?,?,?,?)''',(ctx.platform,day,track,now,min(now+TOURNAMENT_SIGNUP,(day+1)*86400)))
            con.execute('''INSERT INTO race_tournament_entries
                (platform,utc_day,user_id,vehicle_key,tuned,entry_fee,joined_at)
                VALUES(?,?,?,?,?,?,?)''',(ctx.platform,day,ctx.user_id,key,int(car['tuned']),TOURNAMENT_ENTRY,now))
            if count+1==TOURNAMENT_LIMIT:
                return '✅ Ты записан! Все 8 мест заняты.\n'+_settle_tournament(con,service,ctx.platform,day,now)
            return f'✅ Регистрация: {count+1}/{TOURNAMENT_LIMIT}. Взнос 150 монет зарезервирован. Результаты после завершения регистрации.'
        if not row:
            return '🏆 Сегодня турнир ещё не открыт. Начни: турнир записаться скутер.'
        count=con.execute('SELECT COUNT(*) FROM race_tournament_entries WHERE platform=? AND utc_day=?',
                          (ctx.platform,day)).fetchone()[0]
        if action=='провести':
            if row['status']!='open':
                return '🏆 Турнир уже завершён. Команда: турнир итоги.'
            if row['closes_at']>now and count<TOURNAMENT_LIMIT:
                return f'⌛ Регистрация ещё открыта {row["closes_at"]-now} сек. ({count}/8 участников).'
            return _settle_tournament(con,service,ctx.platform,day,now)
        if row['status']=='open' and row['closes_at']<=now:
            outcome=_settle_tournament(con,service,ctx.platform,day,now)
            if action=='итоги':
                return outcome
            row=con.execute('SELECT * FROM race_tournaments WHERE platform=? AND utc_day=?',
                            (ctx.platform,day)).fetchone()
        if row['status']=='finished':
            return (f'🏆 Сегодняшний турнир завершён. Участников: {count}.\n'
                    f'🥇 [id{row["winner_id"]}|Победитель] · 🥈 [id{row["runner_up_id"]}|Второе место].')
        if row['status']=='cancelled':
            return '🏆 Турнир отменён из-за недостатка участников. Взносы возвращены.'
        return (f'🏆 КВЕРАТО | ТУРНИР\nТрасса: {TRACKS[row["track_key"]][1]}\n'
                f'Участники: {count}/8. Взнос: 150 виртуальных монет.\n'
                f'Регистрация ещё {max(0,row["closes_at"]-now)} сек.\n'
                'турнир записаться купе / турнир провести / турнир итоги.\n'
                'В случае одиночной заявки взнос возвращается.')


def settle_expired_tournaments(con,service,now:int)->int:
    """Settle open tournaments from any day when the bot handles a message."""
    rows=con.execute("""SELECT platform,utc_day FROM race_tournaments
        WHERE status='open' AND closes_at<=? ORDER BY closes_at LIMIT 20""",(now,)).fetchall()
    for x in rows:
        _settle_tournament(con,service,x['platform'],x['utc_day'],now)
    return len(rows)


def extra_command(service,ctx,cmd:str,args:list[str],now:int)->str|None:
    if cmd=='race':
        return race_command(service,ctx,args,now)
    if cmd=='racestats' or cmd=='racetop':
        return stats_command(service,ctx,cmd)
    if cmd=='racechallenge':
        return challenge_command(service,ctx,args,now)
    if cmd=='tournament':
        return tournament_command(service,ctx,args,now)
    return None
