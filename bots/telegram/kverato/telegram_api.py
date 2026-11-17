"""Small, explicit Telegram Bot API transport. Never log token-bearing URLs."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Any
import aiohttp

log = logging.getLogger(__name__)


class TelegramAPIError(RuntimeError):
    def __init__(self, method: str, code: int, description: str = '', *,
                 retry_after: int | None = None):
        self.method, self.code = method, code
        # The server's flood-control hint is safe to expose (never its text).
        self.retry_after = min(retry_after, 3600) if type(retry_after) is int and retry_after > 0 else None
        # Telegram descriptions may contain content supplied by third parties;
        # neither a token, request body nor a signed URL belongs in a log.
        super().__init__(f'Telegram API {method} returned HTTP/API {code}')


class TelegramUncertainDelivery(RuntimeError):
    """Network failed after attempted message send; outcome may be delivered."""


class TelegramAPI:
    def __init__(self, token: str, *, base_url: str = 'https://api.telegram.org',
                 session: aiohttp.ClientSession | None = None):
        self.token = token
        self.base_url = base_url.rstrip('/')
        self.session = session
        self.owned_session = session is None

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=55))
        return self

    async def __aexit__(self, *_):
        if self.owned_session and self.session is not None:
            await self.session.close()

    async def call(self, method: str, params: dict[str, Any] | None = None,
                   *, image: tuple[bytes, str, str] | None = None,
                   timeout: float = 25) -> Any:
        if self.session is None:
            raise RuntimeError('Telegram API session must be opened before use')
        url = f'{self.base_url}/bot{self.token}/{method}'
        payload = dict(params or {})
        try:
            if image is None:
                async with self.session.post(url, json=payload,
                                             timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                    return await self._decode(method, response)
            data, filename, mime = image
            form = aiohttp.FormData()
            for k, v in payload.items():
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                form.add_field(k, str(v))
            form.add_field('photo' if method == 'sendPhoto' else 'document',
                           data, filename=filename, content_type=mime)
            async with self.session.post(url, data=form,
                                         timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                return await self._decode(method, response)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # Never retry a send automatically! The Telegram server might have
            # accepted the message before a connection died.
            if method in ('sendPhoto','sendDocument','sendMessage','editMessageText','editMessageReplyMarkup'):
                raise TelegramUncertainDelivery(f'{method} acknowledgement uncertain') from None
            raise TelegramAPIError(method, 0) from None

    @staticmethod
    async def _decode(method: str, response: aiohttp.ClientResponse) -> Any:
        try:
            result = await response.json(content_type=None)
        except (ValueError, aiohttp.ClientError):
            raise TelegramAPIError(method, response.status) from None
        if not isinstance(result, dict) or not result.get('ok') or response.status >= 400:
            code = (result.get('error_code') if isinstance(result, dict) else None)
            params = result.get('parameters') if isinstance(result, dict) else None
            retry = params.get('retry_after') if isinstance(params, dict) else None
            # Do not log Telegram error descriptions or the request URL.
            raise TelegramAPIError(method, int(code) if type(code) is int else response.status,
                                   retry_after=retry)
        return result.get('result')

    async def get_user_avatar(self, user_id: int) -> bytes | None:
        """Best-effort profile photo, fixed Telegram host and bounded response.

        Never include token-bearing file URLs or API error details in logs.
        """
        try:
            photos = await self.call('getUserProfilePhotos', {'user_id': user_id, 'limit': 1})
            groups = photos.get('photos') if isinstance(photos, dict) else None
            if not isinstance(groups, list) or not groups or not isinstance(groups[0], list):
                return None
            sizes = [x for x in groups[0] if isinstance(x, dict) and isinstance(x.get('file_id'), str)]
            if not sizes:
                return None
            biggest = max(sizes, key=lambda x: int(x.get('file_size') or 0))
            info = await self.call('getFile', {'file_id': biggest['file_id']})
            path = info.get('file_path') if isinstance(info, dict) else None
            if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}', path)
                    or '..' in path.split('/') or not path.startswith('photos/')):
                return None
            if self.session is None:
                return None
            url = f'{self.base_url}/file/bot{self.token}/{path}'
            async with self.session.get(url, allow_redirects=False,
                                        timeout=aiohttp.ClientTimeout(total=8)) as response:
                if response.status != 200:
                    return None
                if int(response.headers.get('Content-Length') or 0) > 750_000:
                    return None
                content = await response.content.read(750_001)
                return content if len(content) <= 750_000 else None
        except (TelegramAPIError, aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    async def get_me(self):
        return await self.call('getMe')

    async def get_webhook_info(self):
        return await self.call('getWebhookInfo')

    async def get_updates(self, offset: int | None):
        payload = {'timeout': 25, 'limit': 50, 'allowed_updates': ['message','callback_query']}
        if offset is not None:
            payload['offset'] = offset
        return await self.call('getUpdates', payload, timeout=40)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str, *, markup=None):
        payload = {'chat_id': chat_id, 'message_id': message_id, 'text': text}
        if markup:
            payload['reply_markup'] = markup
        return await self.call('editMessageText', payload)

    async def edit_message_reply_markup(self, chat_id: int, message_id: int):
        """Clear an old challenge's buttons after its state is settled."""
        return await self.call('editMessageReplyMarkup', {'chat_id': chat_id,
                                                          'message_id': message_id,
                                                          'reply_markup': {'inline_keyboard': []}})

    async def send_message(self, chat_id: int, text: str, *, markup=None, thread_id=None):
        payload = {'chat_id': chat_id, 'text': text}
        if markup:
            payload['reply_markup'] = markup
        if thread_id:
            payload['message_thread_id'] = thread_id
        return await self.call('sendMessage', payload)

    async def send_photo(self, chat_id: int, raw: bytes, *, markup=None, thread_id=None):
        payload = {'chat_id': chat_id}
        if markup:
            payload['reply_markup'] = markup
        if thread_id:
            payload['message_thread_id'] = thread_id
        return await self.call('sendPhoto', payload,
                               image=(raw, 'kverato.png', 'image/png'), timeout=45)

    async def send_document(self, chat_id: int, raw: bytes, *, markup=None, thread_id=None):
        payload = {'chat_id': chat_id}
        if markup:
            payload['reply_markup'] = markup
        if thread_id:
            payload['message_thread_id'] = thread_id
        return await self.call('sendDocument', payload,
                               image=(raw, 'kverato.png', 'image/png'), timeout=45)

    async def answer_callback(self, query_id: str, text: str = ''):
        return await self.call('answerCallbackQuery', {'callback_query_id': query_id, 'text': text})

    async def set_commands(self):
        names = [('start','Начать игру'), ('id','Мой Telegram ID'), ('menu','Главное меню'),
                 ('settings','Мои настройки'), ('cards','Карточки: on/off'),
                 ('avatar','Аватар: on/off'),
                 ('profile','Профиль игрока'), ('player','Публичный профиль игрока'),
                 ('duels','Активные дуэли в группе'), ('friends','Мои друзья и заявки'),
                 ('friend','Пригласить в друзья'), ('friendtop','Рейтинг друзей'),
                 ('balance','Баланс'),
                 ('daily','Ежедневный бонус'), ('weekly','Награда раз в 7 дней'), ('work','Работа'),
                 ('games','Игры'), ('economy','Экономика'),
                 ('shop','Магазин'), ('clan','Кланы'),
                 ('season','Сезон'), ('catalog','Каталог'),
                 ('help','Список команд'), ('rules','Правила игры'),
                 ('privacy','Данные игрока'), ('ping','Проверка работы'), ('version','Версия КВЕРАТО')]
        return await self.call('setMyCommands', {'commands': [
            {'command': command, 'description': description} for command, description in names
        ]})
