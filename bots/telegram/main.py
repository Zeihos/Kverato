"""KVERATO Telegram edition. Run with: python main.py"""
from __future__ import annotations
import asyncio
from contextlib import suppress
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from kverato.db import Database
from kverato.operations import daily_prestart_backup, verify_sqlite
from kverato.service import BotService
from kverato.telegram_api import TelegramAPI, TelegramAPIError
from kverato.telegram_app import TelegramApp
from kverato.telegram_lock import AlreadyRunning, telegram_instance_lock
from kverato.telegram_maintenance import periodic_maintenance
from kverato.version import VERSION

ROOT = Path(__file__).resolve().parent


async def poll(api: TelegramAPI, app: TelegramApp) -> None:
    offset = None
    errors = 0
    while True:
        try:
            updates = await api.get_updates(offset)
            errors = 0
            if not isinstance(updates, list):
                raise RuntimeError('Telegram getUpdates returned non-list')
            for update in updates:
                if isinstance(update, dict) and type(update.get('update_id')) is int:
                    # Process sequentially to preserve both per-chat arrival
                    # ordering and expensive economic actions' order.
                    await app.process_update(update)
                    offset = max(offset or 0, update['update_id'] + 1)
        except asyncio.CancelledError:
            raise
        except TelegramAPIError as exc:
            errors += 1
            # 409 typically means another getUpdates instance or webhook.
            logging.error('TG polling API problem method=%s code=%s', exc.method, exc.code)
            if exc.code in (401, 403, 409):
                raise RuntimeError(f'Telegram polling cannot continue (API {exc.code})') from None
            # Respect Telegram's explicit 429 flood-control delay. Never
            # replay already-executed game commands as a "retry" here.
            delay = min(3600, max(1, exc.retry_after)) if exc.code == 429 and exc.retry_after else min(60, 2 ** min(errors, 5))
            await asyncio.sleep(delay)
        except Exception as exc:
            errors += 1
            logging.error('TG polling problem type=%s', type(exc).__name__)
            await asyncio.sleep(min(60, 2 ** min(errors, 5)))


async def main_async(token: str, service: BotService) -> None:
    async with TelegramAPI(token) as api:
        me = await api.get_me()
        if not isinstance(me, dict) or not me.get('is_bot'):
            raise RuntimeError('Telegram token is not a valid bot token')
        hook = await api.get_webhook_info()
        if isinstance(hook, dict) and hook.get('url'):
            raise RuntimeError('Telegram webhook already configured; disable it before polling')
        username = str(me.get('username') or '')
        try:
            await api.set_commands()
        except TelegramAPIError as exc:
            logging.warning('Could not register Telegram menu commands: code=%s', exc.code)
        print(f'[КВЕРАТО TG v{VERSION}] Telegram connected: @{username}', flush=True)
        app = TelegramApp(service, api, bot_username=username)
        maint = asyncio.create_task(periodic_maintenance(service, app.guard, friends=app.friends), name='tg-maintenance')
        try:
            await poll(api, app)
        finally:
            maint.cancel()
            with suppress(asyncio.CancelledError):
                await maint


def main() -> int:
    load_dotenv(ROOT / '.env')
    token = os.getenv('TELEGRAM_TOKEN', '').strip()
    if not re.fullmatch(r'[0-9]{6,12}:[A-Za-z0-9_-]{25,}', token):
        print('[ERROR] Укажи TELEGRAM_TOKEN от @BotFather в файле .env.', file=sys.stderr)
        return 2
    owners = {s.strip() for s in os.getenv('TELEGRAM_OWNER_IDS', '').split(',')
              if re.fullmatch(r'[1-9][0-9]{0,18}', s.strip())}
    dbfile = Path(os.getenv('KVERATO_DB_PATH', 'data/kverato_telegram.sqlite3'))
    if not dbfile.is_absolute():
        dbfile = ROOT / dbfile
    logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
    try:
        with telegram_instance_lock(dbfile):
            if os.getenv('KVERATO_AUTO_BACKUP', '1') != '0':
                last = daily_prestart_backup(dbfile, ROOT / 'backups')
                if last:
                    print(f'[КВЕРАТО] Резервная копия: {last.name}', flush=True)
            # Verify the original *after* the verified snapshot and before schema
            # migrations/polling. This prevents booting on damaged player data.
            if dbfile.is_file():
                ok, why = verify_sqlite(dbfile)
                if not ok:
                    raise RuntimeError('Existing player database failed integrity check: ' + why)
            service = BotService(Database(dbfile), owner_ids=owners)
            print(f'[КВЕРАТО TG v{VERSION}] Игровое ядро КВЕРАТО · База: {dbfile}', flush=True)
            if not owners:
                print('[WARN] TELEGRAM_OWNER_IDS не указан: внутреннее администрирование недоступно.', flush=True)
            asyncio.run(main_async(token, service))
    except KeyboardInterrupt:
        print('[КВЕРАТО TG] Остановлен оператором.', flush=True)
        return 0
    except AlreadyRunning as exc:
        print(f'[ERROR] {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        # Never print exception details that might contain the bot token.
        print(f'[ERROR] Telegram startup failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
