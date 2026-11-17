"""VK community Long Poll adapter, with clean text-only replies.

API calls are limited to VK permissions granted to the community token.
No tokens are logged, and unknown chat messages are silently ignored.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import aiohttp
from weakref import WeakValueDictionary

from vkbottle.bot import Bot, Message

from kverato.service import ALIASES, BotService, Context
from kverato.delivery import VKDeliveryGuard
from kverato.traffic import BoundedCache, VKRequestLimiter
from kverato.vk_parsing import vk_mentioned_user
from kverato.vk_ui import REMOVE_KEYBOARD_JSON, resolve_vk_input
from kverato.vk_theme import render_vk_messages
from kverato.image_cards import is_safe_image_request, prepare_card_data, render_card_pages
from kverato.vk_image_delivery import VKCardUploadError, upload_vk_card

logger = logging.getLogger(__name__)


def run(token: str, service: BotService) -> None:
    bot = Bot(token=token)
    name_cache = BoundedCache(4096)
    avatar_urls = BoundedCache(4096)
    avatar_cache = BoundedCache(64)  # bounded bytes, only for image requests
    delivery = VKDeliveryGuard(service.db)
    delivery.prune()
    rate_limiter = VKRequestLimiter()
    # SQLite operations are synchronous. Run them outside VKBottle's event loop,
    # but keep commands from the same player in arrival order. Weak references
    # release idle locks instead of retaining one lock per historical user.
    player_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
    processing_slots = asyncio.Semaphore(8)
    # Rendering large cards is CPU/RAM intensive; do not allow arbitrary
    # parallel image generations even for distinct players.
    image_slots = asyncio.Semaphore(2)

    @bot.on.message()
    async def handle_message(message: Message) -> None:
        if not message.from_id or message.from_id <= 0:
            return
        group = message.peer_id >= 2_000_000_000
        if not message.text:
            return
        text = message.text.strip()
        if not text:
            return
        input_text, _ = resolve_vk_input(text)
        first = input_text.split()[0] if input_text else ''
        command = ALIASES.get(first.lstrip('!/.').split('@', 1)[0].lower())
        if message.from_id not in name_cache:
            try:
                users = await bot.api.users.get(user_ids=[message.from_id], fields=["photo_200"])
                avatar_urls[message.from_id] = (getattr(users[0], 'photo_200', None) if users else None)
                name_cache[message.from_id] = (
                    f'{users[0].first_name} {users[0].last_name}'.strip()
                    if users else f'Игрок {message.from_id}'
                )
            except Exception:
                name_cache[message.from_id] = f'Игрок {message.from_id}'
                avatar_urls[message.from_id] = None
        replied = getattr(message, 'reply_message', None)
        replied_id = getattr(replied, 'from_id', 0) if replied else 0
        target = (str(replied_id) if replied_id and replied_id > 0
                  else vk_mentioned_user(text))
        ctx = Context(
            platform='vk',
            user_id=str(message.from_id),
            chat_id=str(message.peer_id),
            name=name_cache[message.from_id],
            group=group,
            admin=False,  # VK chat admin is never bot staff.
            target_id=target,
        )
        event_keys = delivery.keys(message.peer_id,
                                   getattr(message, 'conversation_message_id', None),
                                   getattr(message, 'id', None))
        if not event_keys:
            # Never execute money-changing actions without a replay-safe event ID.
            logger.warning('Skipped VK message with no stable event ID in peer=%s', message.peer_id)
            return
        claimed = False
        finished = False
        try:
            if not delivery.claim_aliases(event_keys):
                return  # VK Long Poll redelivery: never repeat monetary commands.
            claimed = True
            allowed, notify = rate_limiter.allow(int(message.from_id))
            if not allowed:
                delivery.finish_aliases(event_keys, success=True)
                finished = True
                if notify and not group:
                    await message.answer(render_vk_messages('⏳ Слишком много команд. Подожди несколько секунд.', None)[0], keyboard=REMOVE_KEYBOARD_JSON)
                return
            player_lock = player_locks.get(int(message.from_id))
            if player_lock is None:
                player_lock = asyncio.Lock()
                player_locks[int(message.from_id)] = player_lock
            async with player_lock:
                async with processing_slots:
                    answer = await asyncio.to_thread(service.handle, ctx, input_text)
            # Commit completion before sending. A VK send failure must not
            # cause financial commands to be processed again.
            delivery.finish_aliases(event_keys, success=True)
            finished = True
            if not answer:
                return
            # VK used to attach a persistent keyboard to every PM.  Sending
            # an empty keyboard explicitly removes the previously installed
            # rows (including the seasonal keyboard the user complained about).
            # It is sent with each private reply to heal existing dialogs.
            # For informational requests, send dynamic image cards instead of
            # static example illustrations or plain-text tables. Rendering
            # happens AFTER the main command, never instead of access checks.
            # Commands with arguments can modify state, and remain plain text.
            # On image failure the original exact answer is preserved.
            import os
            if (os.getenv("KVERATO_IMAGE_CARDS", "1") != "0" and
                    is_safe_image_request(command, input_text)):
                delivered_pages = 0
                image_stage = 'prepare'
                try:
                    async with image_slots:
                        avatar = avatar_cache.get(message.from_id)
                        if message.from_id not in avatar_cache:
                            avatar = None
                            url = avatar_urls.get(message.from_id)
                            if isinstance(url, str) and url.startswith('https://'):
                                hostname = (urlparse(url).hostname or '').lower()
                                if hostname in ('vk.com','userapi.com','vkuserphoto.ru') or hostname.endswith(('.vkuserphoto.ru','.userapi.com')):
                                    try:
                                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=7)) as sess:
                                            async with sess.get(url,allow_redirects=False) as photo_response:
                                                photo_response.raise_for_status()
                                                if photo_response.headers.get('Content-Length') and int(photo_response.headers['Content-Length'])>750_000:
                                                    raise ValueError('VK avatar image too large')
                                                avatar = await photo_response.content.read(750_001)
                                                if len(avatar)>750_000:
                                                    raise ValueError('VK avatar image too large')
                                    except Exception:
                                        logger.debug('Avatar unavailable for peer=%s; using placeholder', message.peer_id)
                                        avatar=None
                            avatar_cache[message.from_id] = avatar
                        image_stage = 'data'
                        data = await asyncio.to_thread(
                            prepare_card_data, service, ctx, command, answer, avatar)
                        if data is not None:
                            image_stage = 'render'
                            pages = await asyncio.to_thread(render_card_pages, data)
                    # Release the CPU/RAM render semaphore before network I/O.
                    # Otherwise two slow VK uploads prevent ALL other players
                    # from having their cards rendered for up to 30s/page.
                    if data is not None and 1 <= len(pages) <= 4:
                        for page in pages:
                            image_stage = 'vk_photo_upload'
                            attachment = await asyncio.wait_for(
                                upload_vk_card(bot.api, message.peer_id, page), timeout=45)
                            if attachment.startswith('doc'):
                                logger.info('VK card document fallback peer=%s command=%s',
                                            message.peer_id, command)
                            image_stage = 'vk_message_send'
                            if group:
                                await asyncio.wait_for(message.answer(attachment=attachment), timeout=20)
                            else:
                                await asyncio.wait_for(message.answer(
                                    attachment=attachment, keyboard=REMOVE_KEYBOARD_JSON), timeout=20)
                            delivered_pages += 1
                        return
                except Exception as exc:
                    # VK upload URLs can contain secrets; don't print exception
                    # strings or tracebacks, which may contain signed URLs.
                    first_error = exc.photo_error if isinstance(exc, VKCardUploadError) else None
                    logger.warning('VK image unavailable stage=%s type=%s code=%s photo_stage=%s photo_type=%s photo_code=%s peer=%s command=%s pages=%s',
                                   exc.stage if isinstance(exc, VKCardUploadError) else image_stage,
                                   exc.exception_type if isinstance(exc, VKCardUploadError) else type(exc).__name__,
                                   exc.api_code if isinstance(exc, VKCardUploadError) else None,
                                   first_error.stage if first_error else None,
                                   first_error.exception_type if first_error else None,
                                   first_error.api_code if first_error else None,
                                   message.peer_id, command, delivered_pages)
                    # An API send timeout is *ambiguous*: VK may have already
                    # accepted this image, so never follow it with a duplicate
                    # text response or another send request. The player can
                    # request the informational screen again if necessary.
                    if image_stage == 'vk_message_send' and isinstance(exc, TimeoutError):
                        logger.warning('VK image send acknowledgement uncertain peer=%s command=%s pages=%s',
                                       message.peer_id, command, delivered_pages)
                        return
                    if delivered_pages:
                        # Do not duplicate delivered pages with the full textual
                        # response. Never rerun the underlying game command.
                        try:
                            notice = '⚠ Не удалось отправить остальные страницы. Повтори информационную команду позже.'
                            if group:
                                await message.answer(notice)
                            else:
                                await message.answer(notice, keyboard=REMOVE_KEYBOARD_JSON)
                        except Exception:
                            logger.warning('VK page notice failed peer=%s', message.peer_id)
                        return
            parts = render_vk_messages(answer, command, group=group)
            for part in parts:
                if group:
                    await message.answer(part)
                else:
                    await message.answer(part, keyboard=REMOVE_KEYBOARD_JSON)
        except Exception as exc:
            if claimed and not finished:
                try:
                    delivery.finish_aliases(event_keys, success=False)
                except Exception:
                    logger.exception('Could not mark VK event as uncertain')
            # Upload and API exceptions can contain signed links; report only
            # class names, never raw third-party exception strings.
            logger.error('VK handler failed peer=%s type=%s finished=%s',
                         message.peer_id, type(exc).__name__, finished)
            if not finished:
                # If only VK delivery failed, do NOT tell the user their
                # financial command failed: its DB transaction may be done.
                try:
                    await message.answer(render_vk_messages('⚠ Произошла ошибка. Если команда меняла баланс, сначала проверь его — не повторяй платёж вслепую.', None, group=group)[0], **({} if group else {'keyboard': REMOVE_KEYBOARD_JSON}))
                except Exception:
                    logger.warning('Failed sending VK error message peer=%s', message.peer_id)

    bot.run()
