"""KVERATO Telegram: native interaction layer for the shared game engine."""
from __future__ import annotations
import asyncio
import logging
import os
import re
import time
from contextlib import closing
from weakref import WeakValueDictionary

from .image_cards import is_safe_image_request, prepare_card_data, render_card_pages
from .service import BotService, Context
from .telegram_api import TelegramAPIError, TelegramUncertainDelivery
from .telegram_delivery import TelegramDeliveryGuard
from .telegram_health import status_snapshot, format_status
from .telegram_friends import TelegramFriends, valid_uid
from .telegram_format import format_text
from .telegram_preferences import TelegramPreferences
from .telegram_ui import (callback_action, duel_keyboard, duel_status_keyboard, friends_keyboard, friend_request_keyboard, friend_list_page, keyboard, menu_text, preferences_keyboard,
                          preferences_text, MENU_ALIASES, parse_command, section_for, split_text)
from .traffic import UserRateLimiter, BoundedCache

log = logging.getLogger(__name__)


class TelegramApp:
    def __init__(self, service: BotService, api, *, bot_username: str = ''):
        self.service = service
        self.api = api
        self.bot_username = bot_username
        self.started_at = time.monotonic()
        self.guard = TelegramDeliveryGuard(service.db)
        self.preferences = TelegramPreferences(service.db)
        self.friends = TelegramFriends(service.db)
        self.guard.prune()
        self.limiter = UserRateLimiter(limit=18, window=10)
        self.player_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
        self.slots = asyncio.Semaphore(8)
        self.image_slots = asyncio.Semaphore(2)
        self.avatar_cache = BoundedCache(64)
        self.image_cards = os.getenv('KVERATO_IMAGE_CARDS', '1') != '0'

    async def process_update(self, update: dict) -> None:
        update_id = update.get('update_id')
        if type(update_id) is not int or not self.guard.claim(update_id):
            return
        finished = False
        try:
            if isinstance(update.get('message'), dict):
                await self._message(update['message'])
            elif isinstance(update.get('callback_query'), dict):
                await self._callback(update['callback_query'])
            finished = True
        except Exception as exc:
            # Third-party HTTP errors may embed bot tokens in request URLs.
            log.error('Telegram update failed type=%s', type(exc).__name__)
        finally:
            self.guard.finish(update_id, finished)

    async def _message(self, message: dict) -> None:
        user, chat = message.get('from'), message.get('chat')
        text = message.get('text')
        if not isinstance(user, dict) or not isinstance(chat, dict) or user.get('is_bot'):
            return
        if not isinstance(text, str) or len(text) > 4096 or not text.strip():
            return
        if chat.get('type') not in ('private','group','supergroup'):
            return
        if type(message.get('message_id')) is not int:
            return
        group = chat.get('type') != 'private'
        command, content = parse_command(text, group=group, bot_username=self.bot_username)
        if not content:
            return
        uid, chat_id = user.get('id'), chat.get('id')
        if type(uid) is not int or type(chat_id) is not int or uid <= 0:
            return
        head = content.split()[0].lstrip('/!.').split('@', 1)[0].casefold()
        # In groups, special commands also require an explicit prefix.
        special = not group or text.lstrip().startswith(('/', '!', '.'))
        # Telegram deep links from group chats use /start settings.
        if not group and head in ('start', 'старт') and content.split()[1:] == ['settings']:
            if not self.limiter.allow(uid)[0]:
                return
            await self._show_preferences(chat_id, uid, message.get('message_thread_id'))
            return
        if special and head in ('settings', 'настройки', 'cards', 'карточки', 'avatar', 'аватар'):
            uid, chat_id = user.get('id'), chat.get('id')
            if type(uid) is not int or type(chat_id) is not int or uid <= 0:
                return
            if not self.limiter.allow(uid)[0]:
                return
            if group or uid != chat_id:
                markup = ({'inline_keyboard': [[{'text': '⚙ Открыть личные настройки',
                    'url': 'https://t.me/' + self.bot_username + '?start=settings'}]]}
                    if self.bot_username else None)
                try:
                    await self.api.send_message(chat_id,
                        '⚙ Личные настройки КВЕРАТО доступны в диалоге с ботом.',
                        markup=markup, thread_id=message.get('message_thread_id'))
                except (TelegramAPIError, TelegramUncertainDelivery):
                    pass
                return
            if head in ('cards','карточки','avatar','аватар'):
                bits = content.split()
                if len(bits) == 2 and bits[1].casefold() in ('on','off','вкл','выкл'):
                    key = 'cards' if head in ('cards', 'карточки') else 'avatar'
                    enabled = bits[1].casefold() in ('on','вкл')
                    self.preferences.set(uid, key, enabled)
                    if key == 'avatar' and not enabled:
                        self.avatar_cache.pop(uid, None)
            await self._show_preferences(chat_id, uid, message.get('message_thread_id'))
            return
        if special and head in ('status', 'состояние'):
            if not self.limiter.allow(uid)[0]:
                return
            # Operational counters may only be requested by an explicitly
            # configured Telegram owner in their own private chat.
            if group or uid != chat_id or str(uid) not in self.service.owner_ids:
                if not group:
                    await self._safe_text(chat_id, '🔒 Диагностика доступна только владельцу бота.', None,
                                          message.get('message_thread_id'))
                return
            from .version import VERSION
            try:
                status = await asyncio.to_thread(status_snapshot, self.service.db,
                                                 time.monotonic() - self.started_at)
                await self._safe_text(chat_id, format_status(status, VERSION), None,
                                      message.get('message_thread_id'))
            except Exception as exc:
                log.warning('TG diagnostics failed type=%s', type(exc).__name__)
                await self._safe_text(chat_id, '⚠ Не удалось проверить состояние базы. Проверь журнал запуска.',
                                      None, message.get('message_thread_id'))
            return
        if special and head in ('rules', 'правила'):
            if not self.limiter.allow(uid)[0]:
                return
            await self._safe_text(chat_id, self._rules_text(), None,
                                  message.get('message_thread_id'))
            return
        if special and head in ('privacy', 'приватность', 'данные'):
            if not self.limiter.allow(uid)[0]:
                return
            await self._safe_text(chat_id, self._privacy_text(), None,
                                  message.get('message_thread_id'))
            return
        if special and head in ('help', 'помощь', 'команды'):
            if not self.limiter.allow(uid)[0]:
                return
            await self._safe_text(chat_id, self._help_text(group), 'help',
                                  message.get('message_thread_id'))
            return
        if special and head in ('id', 'айди', 'мойid'):
            if not self.limiter.allow(uid)[0]:
                return
            await self._safe_text(chat_id, f'🆔 Твой Telegram ID: {uid}', None,
                                  message.get('message_thread_id'))
            return
        if special and head in MENU_ALIASES:
            if not self.limiter.allow(uid)[0]:
                return
            await self._show_section(chat_id, 'main', message.get('message_thread_id'))
            return
        if special and head in ('ping', 'пинг'):
            if not self.limiter.allow(uid)[0]:
                return
            await self._safe_text(chat_id, '✅ КВЕРАТО Telegram работает.', None,
                                  message.get('message_thread_id'))
            return
        if special and head in ('version', 'версия'):
            if not self.limiter.allow(uid)[0]:
                return
            from .version import VERSION
            await self._safe_text(chat_id, f'👑 КВЕРАТО Telegram v{VERSION}', None,
                                  message.get('message_thread_id'))
            return
        reply_user = (message.get('reply_to_message') or {}).get('from') or {}
        target_id = str(reply_user.get('id')) if type(reply_user.get('id')) is int and reply_user['id'] > 0 else None
        if not target_id:
            # text_mention is provided by Telegram with a verified numeric ID;
            # plain @username is NOT safe because names can change.
            target_id = self._text_mentioned_id(message, text)
        if special and head in ('friends', 'друзья'):
            if not self.limiter.allow(uid)[0]:
                return
            args = content.split()[1:]
            categories = {'all':'overview','все':'overview',
                          'friends':'friends','друзья':'friends',
                          'incoming':'incoming','входящие':'incoming',
                          'outgoing':'outgoing','исходящие':'outgoing'}
            category = categories.get(args[0].casefold(), '') if args else 'overview'
            if not category or len(args) > 2 or (len(args) == 2 and
                    (not args[1].isascii() or not args[1].isdigit()
                     or not 1 <= int(args[1]) <= 100)):
                await self._safe_text(chat_id,
                    '👥 Формат: /friends, /friends friends, /friends incoming 2, '
                    '/friends outgoing 1.', None, message.get('message_thread_id'))
                return
            page = int(args[1])-1 if len(args)==2 else 0
            await self._show_friends(chat_id, uid, group, message.get('message_thread_id'),
                                     category=category, page=page)
            return
        if special and head in ('friendtop', 'топдрузей'):
            if not self.limiter.allow(uid)[0]:
                return
            parts = content.split()
            metric = 'wins' if len(parts) == 2 and parts[1].casefold() in ('wins', 'победы') else 'xp'
            if len(parts) > 2 or (len(parts) == 2 and parts[1].casefold() not in ('xp','опыт','wins','победы')):
                await self._safe_text(chat_id, '🏆 Формат: /friendtop или /friendtop wins', None,
                                      message.get('message_thread_id'))
                return
            await self._show_friendtop(chat_id, uid, group, message.get('message_thread_id'), metric=metric)
            return
        if special and head in ('friend', 'друг'):
            if not self.limiter.allow(uid)[0]:
                return
            await self._friend_command(uid, chat_id, content, group, message.get('message_thread_id'))
            return
        if special and head in ('duels', 'мои-дуэли') and len(content.split()) == 1:
            if not self.limiter.allow(uid)[0]:
                return
            await self._show_my_duel(uid, chat_id, group, message.get('message_thread_id'))
            return
        if special and head in ('player', 'игрок', 'who', 'кто'):
            if not self.limiter.allow(uid)[0]:
                return
            bits = content.split()
            target = (bits[1] if len(bits) == 2 else
                      target_id if len(bits) == 1 and target_id else
                      str(uid) if len(bits) == 1 else '')
            if not re.fullmatch(r'[1-9][0-9]{0,18}', target):
                await self._safe_text(chat_id, '👥 Формат: /player ID или /player ответом на сообщение игрока.',
                                      None, message.get('message_thread_id'))
                return
            await self._public_player(chat_id, target, message.get('message_thread_id'))
            return
        await self._execute(uid, chat_id, user, content, command,
                            group=group, target_id=target_id,
                            thread_id=message.get('message_thread_id'))

    @staticmethod
    def _rules_text() -> str:
        return ('📜 КВЕРАТО | Правила игры\n\n'
                '• Не используй ошибки или автоматизацию для получения нечестных преимуществ.\n'
                '• Запрещены обман игроков, злоупотребления и массовый спам.\n'
                '• Внутриигровая валюта и предметы — игровые, не реальные деньги.\n'
                '• Уважай других участников. Администрация может ограничивать доступ за нарушения.\n'
                '• Для команд, меняющих баланс, проверяй сумму и получателя перед отправкой.\n\n'
                '🆘 Команды: /help  •  ⚙ Настройки: /settings')

    @staticmethod
    def _privacy_text() -> str:
        return ('🔐 КВЕРАТО | Данные игрока\n\n'
                'Для работы Telegram-версии хранятся Telegram ID, отображаемое имя, '
                'игровой прогресс, покупки, заявки в друзья, настройки и технические '
                'идентификаторы обработанных событий.\n\n'
                'Данные сохраняются в базе SQLite на сервере бота. '
                'КВЕРАТО не запрашивает пароли Telegram или коды входа.\n\n'
                'По вопросам своих данных обращайся к администрации проекта.\n'
                '📜 Правила: /rules')

    @staticmethod
    def _help_text(group: bool) -> str:
        prefix = 'В группе используй / или ! перед командой.' if group else 'В личном чате можно писать команды и по-русски.'
        return ('👑 КВЕРАТО | Помощь Telegram\n\n'
                '/start — начать игру\n'
                '/menu — меню с кнопками\n'
                '/profile — профиль и карточка\n'
                '/balance — баланс\n'
                '/daily — ежедневная награда\n'
                '/weekly — награда раз в 7 дней\n'
                '/rewards — таймеры наград и работы\n'
                '/work — работа\n'
                '/games — игры\n'
                '/shop — магазин\n'
                '/friends — друзья и заявки (личный чат)\n'
                '/friend ID — пригласить друга\n'
                '/friendtop — рейтинг друзей\n'
                '/player ID — публичный профиль\n'
                '/duels — мои вызовы в группе\n'
                '/settings — настройки карточек\n'
                '/id — Telegram ID\n'
                '/version — версия бота\n'
                '/rules — правила проекта\n'
                '/privacy — данные игрока\n\n'
                '⚔️ Дуэль: !дуэль 50 ответом на сообщение соперника.\n'
                '💸 Перевод: !перевести 100 ответом на сообщение игрока.\n'
                '👑 Администрирование — команда «админ» только владельцу в личном чате.\n\n'
                + prefix)

    @staticmethod
    def _friend_response(code: str) -> str:
        return {
            'created': '✅ Заявка в друзья отправлена. У другого игрока она появится в /friends.',
            'already': '👥 Вы уже друзья.',
            'sent': '⌛ Твоя заявка уже ожидает решения.',
            'incoming': '📩 У тебя уже есть входящая заявка от этого игрока. Открой /friends.',
            'unknown': '👥 Игрок недоступен: оба участника должны сначала начать игру в КВЕРАТО.',
            'unavailable': '👥 Игрок недоступен для приглашений.',
            'self': '👥 Нельзя добавить самого себя.',
            'invalid': '⚠ Укажи действительный числовой Telegram ID.',
            'cooldown': '⌛ После отказа или удаления повторная заявка доступна через 24 часа.',
            'full': '👥 У одного из игроков достигнут лимит друзей (100).',
            'limit': '⏳ Слишком много незавершённых заявок.',
            'daily_limit': '⏳ Дневной лимит приглашений (8) исчерпан.',
            'missing': '⌛ Эта заявка уже завершена или недоступна.',
            'expired': '⌛ Срок действия заявки истёк. Нужна новая заявка.',
            'accepted': '🎉 Теперь вы друзья в КВЕРАТО!',
            'declined': '❌ Заявка отклонена.',
            'cancelled': '↩ Заявка отменена.',
            'removed': '👥 Игрок удалён из друзей.',
            'blocked': '🚫 Игрок заблокирован для новых заявок.',
            'unblocked': '✅ Игрок разблокирован.',
        }.get(code, '⚠ Операция недоступна.')

    async def _friend_command(self, uid: int, chat_id: int, content: str,
                              group: bool, thread_id: int | None) -> None:
        if group or chat_id != uid:
            await self._safe_text(chat_id, '👥 Друзья доступны только в личном чате с ботом: /friends.', None, thread_id)
            return
        parts = content.split()
        if len(parts) == 1:
            await self._show_friends(chat_id, uid, False, thread_id)
            return
        action = 'request'
        if len(parts) == 2:
            target = valid_uid(parts[1])
        elif len(parts) == 3:
            action = {'add':'request', 'invite':'request', 'добавить':'request',
                      'accept':'accept', 'принять':'accept', 'decline':'decline',
                      'reject':'decline', 'отклонить':'decline', 'cancel':'cancel',
                      'отменить':'cancel', 'remove':'remove', 'удалить':'remove',
                      'block':'block', 'блок':'block', 'unblock':'unblock',
                      'разблокировать':'unblock'}.get(parts[1].casefold(), '')
            target = valid_uid(parts[2])
        else:
            action, target = '', None
        if not action or not target:
            await self._safe_text(chat_id, '👥 Формат: /friend ID, /friend accept ID, '
                                  '/friend decline ID, /friend cancel ID, /friend remove ID, '
                                  '/friend block ID, /friend unblock ID.', None, thread_id)
            return
        if action == 'request':
            result = await asyncio.to_thread(self.friends.request, uid, target)
        elif action in ('block','unblock'):
            result = await asyncio.to_thread(self.friends.block, uid, target, enabled=action=='block')
        else:
            result = await asyncio.to_thread(self.friends.transition, uid, target, action)
        await self._safe_text(chat_id, self._friend_response(result.code), None, thread_id)
        if result.code == 'created':
            # Best-effort notification; Telegram may forbid unsolicited bot DMs.
            # The request remains discoverable with /friends regardless.
            try:
                await self.api.send_message(int(target),
                    f'👥 Игрок {uid} пригласил тебя в друзья КВЕРАТО.\n'
                    'Принять или отклонить можно кнопками либо через /friends.',
                    markup=friend_request_keyboard(str(uid)))
            except (TelegramAPIError, TelegramUncertainDelivery):
                pass

    async def _show_friends(self, chat_id: int, uid: int, group: bool,
                            thread_id: int | None, *, message: dict | None = None,
                            category: str = 'overview', page: int = 0) -> None:
        if group or uid != chat_id:
            await self._safe_text(chat_id, '👥 Список друзей доступен только в личном чате: /friends.', None, thread_id)
            return
        view = await asyncio.to_thread(self.friends.overview, uid)
        lines = ['👥 КВЕРАТО | Друзья', '']
        for title, section_key, empty in [('🤝 Друзья','friends','пока нет'),
                                       ('📩 Входящие','incoming','нет заявок'),
                                       ('📤 Исходящие','outgoing','нет заявок')]:
            records=view[section_key]
            lines.append(f'{title}: {len(records)}')
            lines.extend(f'• {name} — {ident}' for ident,name in records[:10])
            if not records: lines.append(f'• {empty}')
            if len(records)>10: lines.append(f'• ... ещё {len(records)-10}')
            lines.append('')
        lines.append('Полный список: кнопки 🤝 📩 📤 или /friends incoming 2.\n'
                     'Пригласить: /friend ID\nУдалить: /friend remove ID\n'
                     'Заблокировать: /friend block ID')
        txt='\n'.join(lines)
        markup=friends_keyboard(view['incoming'],view['outgoing'])
        if category != 'overview':
            txt, markup = friend_list_page(view, category, page)
        if message and isinstance(message.get('text'),str) and type(message.get('message_id')) is int:
            try:
                await self.api.edit_message_text(chat_id,message['message_id'],txt,markup=markup)
                return
            except TelegramAPIError as exc:
                if exc.code != 400 or message.get('text') == txt:
                    return
            except TelegramUncertainDelivery:
                return
        try:
            await self.api.send_message(chat_id,txt,markup=markup,thread_id=thread_id)
        except (TelegramAPIError,TelegramUncertainDelivery) as exc:
            log.warning('TG friends delivery failed type=%s',type(exc).__name__)

    async def _show_friendtop(self, chat_id: int, uid: int, group: bool,
                              thread_id: int | None, *, metric: str = 'xp',
                              message: dict | None = None) -> None:
        if group or uid != chat_id:
            await self._safe_text(chat_id, '🏆 Рейтинг друзей доступен в личном чате: /friendtop.', None, thread_id)
            return
        ranking = await asyncio.to_thread(self.friends.leaderboard, uid, metric=metric)
        if not ranking:
            await self._safe_text(chat_id, '🏆 Сначала зарегистрируйся командой /start.', None, thread_id)
            return
        title = '⭐ Опыт' if metric == 'xp' else '🏆 Победы'
        lines = [f'👑 КВЕРАТО | Рейтинг друзей — {title}', '',
                 'Только ты и твои подтверждённые друзья:', '']
        for rank, (member_id, name, xp, wins) in enumerate(ranking[:20], 1):
            score = xp if metric == 'xp' else wins
            suffix = ' (ты)' if member_id == str(uid) else ''
            lines.append(f'{rank}. {name}{suffix}: {score:,}'.replace(',', ' '))
        if len(ranking) > 20:
            lines.append(f'... ещё {len(ranking)-20} участников')
            for rank, (member_id, name, xp, wins) in enumerate(ranking[20:], 21):
                if member_id == str(uid):
                    score = xp if metric == 'xp' else wins
                    lines.append(f'Твоё место: {rank}. {name}: {score:,}'.replace(',', ' '))
                    break
        lines += ['', 'Здесь нет чужих балансов, покупок и личных настроек.']
        txt = '\n'.join(lines)
        markup = {'inline_keyboard': [[
            {'text': '⭐ По опыту', 'callback_data': 'kv:t:xp'},
            {'text': '🏆 По победам', 'callback_data': 'kv:t:wins'}],
            [{'text': '👥 Друзья', 'callback_data': 'kv:s:friends'},
             {'text': '⬅ Игроки', 'callback_data': 'kv:s:social'}]]}
        if message and isinstance(message.get('text'),str) and type(message.get('message_id')) is int:
            try:
                await self.api.edit_message_text(chat_id, message['message_id'], txt, markup=markup)
                return
            except TelegramAPIError as exc:
                if exc.code != 400 or message.get('text') == txt:
                    return
            except TelegramUncertainDelivery:
                return
        try:
            await self.api.send_message(chat_id, txt, markup=markup, thread_id=thread_id)
        except (TelegramAPIError,TelegramUncertainDelivery) as exc:
            log.warning('TG friend ranking delivery failed type=%s',type(exc).__name__)

    async def _show_my_duel(self, uid: int, chat_id: int, group: bool,
                            thread_id: int | None) -> None:
        """Native, read-only view for a participant; never creates players."""
        if not group:
            await self._safe_text(chat_id, '⚔️ Дуэли доступны в группах Telegram.\n'
                                  'Ответь на сообщение соперника: !дуэль 50', 'duel', thread_id)
            return
        with closing(self.service.db.connect()) as con:
            rows = con.execute("""SELECT id, challenger_id, opponent_id, stake, expires_at
                FROM duels WHERE platform='telegram' AND chat_id=? AND status='pending'
                AND expires_at > ? AND (challenger_id=? OR opponent_id=?)
                ORDER BY id DESC LIMIT 1""",
                (str(chat_id), int(time.time()), str(uid), str(uid))).fetchall()
        if not rows:
            await self._safe_text(chat_id, '⚔️ У тебя нет активных дуэлей в этой группе.\n'
                                  'Для вызова ответь на сообщение игрока: !дуэль 50', 'duel', thread_id)
            return
        row = rows[0]
        own = row['challenger_id'] == str(uid)
        role = 'создатель' if own else 'приглашённый игрок'
        text = (f'⚔️ Дуэль #{row["id"]}\n'
                f'Твоя роль: {role}\n'
                f'Ставка: {row["stake"]} монет\n'
                f'Осталось: {max(0, int(row["expires_at"]) - int(time.time()))} сек.\n'
                'Ставка резервируется до завершения вызова.')
        markup = duel_status_keyboard(int(row['id']), challenger=own)
        await self._safe_text(chat_id, text, 'duel', thread_id, override_markup=markup)

    async def _public_player(self, chat_id: int, target: str, thread_id: int | None) -> None:
        "Public game-only data, no player creation or economic side effects."
        with closing(self.service.db.connect()) as con:
            row = con.execute('SELECT display_name, xp, games_won, reputation, badge '
                              'FROM players WHERE platform=? AND user_id=?',
                              ('telegram', target)).fetchone()
        if row is None:
            msg = '👥 Игрок не зарегистрирован в КВЕРАТО Telegram.'
        else:
            name = str(row['display_name']).replace('\r', ' ').replace('\n', ' ')[:60]
            msg = (f'👥 Публичный профиль: {name}\n'
                   f'🆔 Telegram ID: {target}\n'
                   f'⭐ Опыт: {row["xp"]:,}\n'
                   f'🏆 Победы: {row["games_won"]:,}\n'
                   f'🤝 Репутация: {row["reputation"]:,}\n'
                   f'🎖 Статус: {str(row["badge"])[:48]}').replace(',', ' ')
        await self._safe_text(chat_id, msg, None, thread_id)

    @staticmethod
    def _text_mentioned_id(message: dict, text: str) -> str | None:
        # Telegram entity offsets are in UTF-16 units; inspect only entities
        # *after* the first command token, not arbitrary mentions in captions.
        head_end = len(text.split(maxsplit=1)[0].encode('utf-16-le')) // 2
        for entity in message.get('entities') or []:
            if (isinstance(entity, dict) and entity.get('type') == 'text_mention'
                    and type(entity.get('offset')) is int and entity['offset'] > head_end):
                who = entity.get('user') or {}
                ident = who.get('id')
                if type(ident) is int and ident > 0:
                    return str(ident)
        return None

    async def _callback(self, query: dict) -> None:
        qid = query.get('id')
        if not isinstance(qid, str):
            return
        action = callback_action(query.get('data'))
        msg = query.get('message') or {}
        chat = msg.get('chat') or {}
        user = query.get('from') or {}
        if not action or chat.get('type') not in ('private', 'group', 'supergroup'):
            await self._ack(qid, 'Кнопка устарела')
            return
        uid, chat_id = user.get('id'), chat.get('id')
        if type(uid) is not int or type(chat_id) is not int or uid <= 0:
            await self._ack(qid, 'Недоступно')
            return
        if action[0] == 'friendlist':
            if chat.get('type') != 'private' or uid != chat_id:
                await self._ack(qid, 'Друзья — только в личном чате')
                return
            if not self.limiter.allow(uid)[0]:
                await self._ack(qid, 'Слишком много запросов')
                return
            category, page = action[1].split(':', 1)
            await self._ack(qid)
            await self._show_friends(chat_id, uid, False, msg.get('message_thread_id'),
                                     message=msg, category=category, page=int(page))
            return
        if action[0] == 'friendprofile':
            if chat.get('type') != 'private' or uid != chat_id:
                await self._ack(qid, 'Профиль друга — только в личном чате')
                return
            if not self.limiter.allow(uid)[0]:
                await self._ack(qid, 'Слишком много запросов')
                return
            other = action[1]
            if not await asyncio.to_thread(self.friends.is_friend, uid, other):
                await self._ack(qid, 'Игрок больше не в твоём списке друзей')
                return
            await self._ack(qid)
            await self._public_player(chat_id, other, msg.get('message_thread_id'))
            return
        if action[0] == 'friendtop':
            if chat.get('type') != 'private' or uid != chat_id:
                await self._ack(qid, 'Рейтинг друзей — только в личном чате')
                return
            if not self.limiter.allow(uid)[0]:
                await self._ack(qid, 'Слишком много запросов')
                return
            await self._ack(qid)
            await self._show_friendtop(chat_id, uid, False, msg.get('message_thread_id'),
                                       metric=action[1], message=msg)
            return
        if action[0] == 'friend':
            if chat.get('type') != 'private' or uid != chat_id:
                await self._ack(qid, 'Заявки доступны только в личном чате')
                return
            if not self.limiter.allow(uid)[0]:
                await self._ack(qid, 'Слишком много запросов')
                return
            kind, other = action[1].split(':', 1)
            await self._ack(qid)
            result = await asyncio.to_thread(self.friends.transition, uid, other, kind)
            await self._show_friends(chat_id, uid, False, msg.get('message_thread_id'), message=msg)
            if result.code not in ('accepted','declined','cancelled','removed'):
                await self._safe_text(chat_id, self._friend_response(result.code), None,
                                      msg.get('message_thread_id'))
            return
        if action[0] == 'duel':
            # Callback data is not authorization. Bind challenge ID to the actual
            # Telegram chat, original participants, and current DB state.
            if chat.get('type') not in ('group', 'supergroup'):
                await self._ack(qid, 'Дуэли доступны только в группах')
                return
            kind, number_str = action[1].split(':', 1)
            number = int(number_str)
            with closing(self.service.db.connect()) as con:
                row = con.execute("SELECT challenger_id,opponent_id,status,expires_at FROM duels "
                                  "WHERE id=? AND platform='telegram' AND chat_id=?",
                                  (number, str(chat_id))).fetchone()
            if row is None or row['status'] != 'pending':
                await self._ack(qid, 'Этот вызов уже завершён или недоступен')
                return
            if int(row['expires_at']) <= int(time.time()):
                await self._ack(qid, 'Время вызова истекло')
                if type(msg.get('message_id')) is int:
                    try:
                        await self.api.edit_message_reply_markup(chat_id, msg['message_id'])
                    except (TelegramAPIError, TelegramUncertainDelivery):
                        pass
                return
            expected = row['challenger_id'] if kind == 'cancelduel' else row['opponent_id']
            if str(uid) != expected:
                await self._ack(qid, 'Эта кнопка предназначена другому игроку')
                return
            await self._ack(qid)
            if not self.limiter.allow(uid)[0]:
                return
            await self._execute(uid, chat_id, user, '!' + kind + ' ' + number_str,
                                kind, group=True, target_id=None,
                                thread_id=msg.get('message_thread_id'), already_rate_limited=True)
            # Core performs payment/refund and state transitions atomically.
            # Clear buttons only if it committed a terminal result.
            with closing(self.service.db.connect()) as con:
                after = con.execute("SELECT status FROM duels WHERE id=? AND platform='telegram' AND chat_id=?",
                                    (number, str(chat_id))).fetchone()
            if after is not None and after['status'] != 'pending' and type(msg.get('message_id')) is int:
                try:
                    await self.api.edit_message_reply_markup(chat_id, msg['message_id'])
                except (TelegramAPIError, TelegramUncertainDelivery):
                    pass
            return
        # A settings button is allowed to change display preferences only for
        # the owner of the private chat. It cannot alter the game economy.
        if action[0] == 'preference':
            if chat.get('type') != 'private' or uid != chat_id:
                await self._ack(qid, 'Настройки — только в личном чате')
                return
            if not self.limiter.allow(uid)[0]:
                await self._ack(qid, 'Слишком много запросов')
                return
            await self._ack(qid)
            key, enabled = action[1].split(':', 1)
            self.preferences.set(uid, key, enabled == '1')
            if key == 'avatar' and enabled == '0':
                self.avatar_cache.pop(uid, None)
            await self._show_preferences(chat_id, uid, msg.get('message_thread_id'), message=msg)
            return
        if action in (('section', 'settings'), ('section', 'friends'), ('section', 'friendtop')) and (chat.get('type') != 'private' or uid != chat_id):
            await self._ack(qid, 'Настройки — только в личном чате')
            return
        # Callback query acknowledgement must not be held until rendering.
        await self._ack(qid)
        thread_id = msg.get('message_thread_id')
        if action[0] == 'section':
            if not self.limiter.allow(uid)[0]:
                return
            if action[1] == 'friends':
                await self._show_friends(chat_id, uid, chat.get('type') != 'private', thread_id, message=msg)
            elif action[1] == 'friendtop':
                await self._show_friendtop(chat_id, uid, chat.get('type') != 'private',
                                           thread_id, message=msg)
            elif action[1] == 'settings':
                if chat.get('type') != 'private' or uid != chat_id:
                    return
                await self._show_preferences(chat_id, uid, thread_id, message=msg)
            else:
                await self._show_section(chat_id, action[1], thread_id, message=msg)
            return
        group = chat.get('type') != 'private'
        # The Help button must open Telegram instructions, not legacy VK help.
        if action[0] == 'command' and action[1] in ('help', 'rules', 'privacy'):
            if not self.limiter.allow(uid)[0]:
                return
            content = {'help': self._help_text(group),
                       'rules': self._rules_text(),
                       'privacy': self._privacy_text()}[action[1]]
            await self._safe_text(chat_id, content, None, thread_id)
            return
        content = ('!' if group else '') + action[1]
        command, content = parse_command(content, group=group,
                                         bot_username=self.bot_username)
        await self._execute(uid, chat_id, user, content, command, group=group,
                            target_id=None, thread_id=thread_id)

    async def _show_preferences(self, chat_id: int, uid: int, thread_id: int | None,
                                *, message: dict | None = None) -> None:
        settings = self.preferences.get(uid)
        txt, markup = preferences_text(settings), preferences_keyboard(settings)
        if message and isinstance(message.get('text'), str) and type(message.get('message_id')) is int:
            try:
                await self.api.edit_message_text(chat_id, message['message_id'], txt, markup=markup)
                return
            except TelegramAPIError as exc:
                if exc.code != 400:
                    log.warning('TG settings edit rejected code=%s', exc.code)
                    return
                # A 400 can also mean 'message is not modified'. In that case
                # do not create a duplicate settings message.
                if message.get('text') == txt:
                    return
            except TelegramUncertainDelivery:
                log.warning('TG settings edit acknowledgement uncertain')
                return
        try:
            await self.api.send_message(chat_id, txt, markup=markup, thread_id=thread_id)
        except (TelegramAPIError, TelegramUncertainDelivery) as exc:
            log.warning('TG settings delivery failed type=%s', type(exc).__name__)

    async def _show_section(self, chat_id: int, section: str, thread_id: int | None,
                            *, message: dict | None = None) -> None:
        text = menu_text(section)
        # Text menus can be edited in place, avoiding flood from repeated taps.
        # A button attached to a photo cannot be edited into a text message.
        if (message and isinstance(message.get('text'), str)
                and type(message.get('message_id')) is int):
            try:
                await self.api.edit_message_text(chat_id, message['message_id'],
                                                 text, markup=keyboard(section))
                return
            except TelegramAPIError as exc:
                # For 'not modified' the existing text is already the desired
                # view. Other definite 400s (e.g. old uneditable messages)
                # get a fresh menu; the edit was explicitly rejected.
                if exc.code != 400:
                    log.warning('TG menu edit rejected code=%s', exc.code)
                    return
                if message.get('text') == text:
                    return
            except TelegramUncertainDelivery:
                log.warning('TG menu edit acknowledgement uncertain')
                return
        try:
            await self.api.send_message(chat_id, text, markup=keyboard(section),
                                        thread_id=thread_id)
        except (TelegramAPIError, TelegramUncertainDelivery) as exc:
            log.warning('TG menu delivery failed type=%s', type(exc).__name__)

    async def _send_rejected_retry(self, send, *args, **kwargs):
        """Retry a *definitively rejected* Telegram 429 once (delivery only).

        Never retries network failures or game-service commands. Bounded wait
        avoids stalling polling for an unreasonably long flood hint.
        """
        try:
            return await send(*args, **kwargs)
        except TelegramAPIError as exc:
            if exc.code != 429 or not exc.retry_after or exc.retry_after > 30:
                raise
            log.info('TG flood control method=%s wait=%s', exc.method, exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            return await send(*args, **kwargs)

    async def _ack(self, qid: str, text: str = '') -> None:
        try:
            await self.api.answer_callback(qid, text)
        except (TelegramAPIError, TelegramUncertainDelivery):
            pass

    async def _execute(self, uid: int, chat_id: int, user: dict, content: str,
                       command: str | None, *, group: bool, target_id: str | None,
                       thread_id: int | None, already_rate_limited: bool = False) -> None:
        # TG chat admins do not automatically receive bot staff permissions.
        allowed, notify = (True, False) if already_rate_limited else self.limiter.allow(uid)
        if not allowed:
            if notify and not group:
                await self._safe_text(chat_id, '⏳ Слишком много команд. Подожди несколько секунд.', None, thread_id)
            return
        name = ' '.join(str(user.get(k) or '') for k in ('first_name','last_name')).strip()[:60] or f'Игрок {uid}'
        ctx = Context('telegram', str(uid), str(chat_id), name, group=group,
                      admin=False, target_id=target_id)
        lock = self.player_locks.get(uid)
        if lock is None:
            lock = asyncio.Lock()
            self.player_locks[uid] = lock
        async with lock:
            async with self.slots:
                answer = await asyncio.to_thread(self.service.handle, ctx, content)
        if not answer:
            return
        if group and command == 'duel' and not content.strip().split()[1:]:
            await self._show_my_duel(uid, chat_id, group, thread_id)
            return
        # Only display-only commands, with no dangerous arguments, qualify for
        # cards. Every mutation stays text-based, with exact engine response.
        # Do not query settings SQLite for ordinary actions or every group chat
        # message. Only the display-only image path needs these preferences.
        cards_candidate = self.image_cards and is_safe_image_request(command, content)
        prefs = self.preferences.get(uid) if cards_candidate else None
        if prefs and prefs['cards']:
            try:
                async with self.image_slots:
                    avatar = self.avatar_cache.get(uid) if prefs['avatar'] else None
                    if prefs['avatar'] and uid not in self.avatar_cache:
                        loader = getattr(self.api, 'get_user_avatar', None)
                        avatar = await loader(uid) if loader is not None else None
                        self.avatar_cache[uid] = avatar
                    data = await asyncio.to_thread(prepare_card_data,
                                                   self.service, ctx, command, answer, avatar)
                    pages = await asyncio.to_thread(render_card_pages, data) if data is not None else []
                if 1 <= len(pages) <= 4:
                    await self._send_cards(chat_id, pages, command, thread_id)
                    return
            except TelegramCardMediaRejected:
                log.info('TG photo and document rejected; using text command=%s', command)
            except TelegramUncertainDelivery:
                # A timeout can mean the photo already reached Telegram.
                # Do not retry or send full text to avoid duplicate messages.
                log.warning('TG image acknowledgement uncertain command=%s', command)
                return
            except TelegramAPIError as exc:
                log.warning('TG card send failed method=%s code=%s command=%s',
                            exc.method, exc.code, command)
                # _send_cards handles known media rejections; other errors are
                # terminal here rather than replaying a potentially sent page.
                return
            except Exception as exc:
                log.warning('TG card rendering failed type=%s command=%s', type(exc).__name__, command)
        duel_markup = None
        if group and command == 'duel':
            found = re.match(r'^⚔️ Дуэль #([1-9][0-9]{0,17}):', answer)
            if found:
                duel_markup = duel_keyboard(int(found.group(1)))
        await self._safe_text(chat_id, answer, command, thread_id, override_markup=duel_markup)

    async def _send_cards(self, chat_id: int, pages: list[bytes], command: str | None,
                          thread_id: int | None) -> None:
        sent = 0
        for index, raw in enumerate(pages):
            nav = keyboard(section_for(command)) if index == len(pages)-1 else None
            try:
                await self._send_rejected_retry(self.api.send_photo, chat_id, raw, markup=nav, thread_id=thread_id)
                sent += 1
            except TelegramAPIError as exc:
                if exc.code != 400:
                    raise
                # Server explicitly rejected the photo; document fallback is
                # appropriate for a definite 400, NOT for network timeouts.
                try:
                    await self._send_rejected_retry(self.api.send_document, chat_id, raw, markup=nav, thread_id=thread_id)
                    log.info('TG card document fallback command=%s', command)
                    sent += 1
                except TelegramAPIError as doc_err:
                    if doc_err.code != 400:
                        raise
                    if sent:
                        await self._safe_text(chat_id, '⚠ Не удалось отправить остальные страницы.',
                                              None, thread_id)
                        return
                    raise TelegramCardMediaRejected from None

    async def _safe_text(self, chat_id: int, answer: str, command: str | None,
                         thread_id: int | None, *, override_markup: dict | None = None) -> None:
        parts = split_text(format_text(answer))
        for i, part in enumerate(parts):
            try:
                await self._send_rejected_retry(self.api.send_message, chat_id, part,
                                            markup=(override_markup or keyboard(section_for(command)))
                                            if i == len(parts)-1 else None,
                                            thread_id=thread_id)
            except (TelegramAPIError, TelegramUncertainDelivery) as exc:
                log.warning('TG text delivery failed type=%s', type(exc).__name__)
                return


class TelegramCardMediaRejected(RuntimeError):
    """Telegram definitely rejected both photo and document; text is safe."""
