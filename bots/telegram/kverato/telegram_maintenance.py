"""Periodic settlement for expiring Telegram game stakes.

The game core still owns every transaction and its exactly-once balance logic.
This runner only invokes existing expiry routines, regardless of chat activity.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .v05 import expire_duels
from .v10 import expire_races, settle_expired_tournaments

log = logging.getLogger(__name__)


def settle_due_gameplay(service, *, now: int | None = None) -> dict[str, int]:
    """Expire stakes atomically in an IMMEDIATE SQLite transaction."""
    timestamp = int(service.clock() if now is None else now)
    with service.db.transaction() as con:
        result = {
            'duels': expire_duels(con, timestamp),
            'races': expire_races(con, timestamp),
            'tournaments': settle_expired_tournaments(con, service, timestamp),
        }
    return result


async def periodic_maintenance(service, guard, *, interval: float = 60.0,
                               sleep=asyncio.sleep, friends=None) -> None:
    """Runs until cancelled, never silently stops after an isolated DB error."""
    if interval <= 0:
        raise ValueError('maintenance interval must be positive')
    last_prune = 0.0
    while True:
        try:
            settled = await asyncio.to_thread(settle_due_gameplay, service)
            if any(settled.values()):
                log.info('Telegram settlements duels=%s races=%s tournaments=%s',
                         settled['duels'], settled['races'], settled['tournaments'])
            if time.monotonic() - last_prune >= 86400 or last_prune == 0:
                await asyncio.to_thread(guard.prune)
                if friends is not None:
                    await asyncio.to_thread(friends.prune)
                last_prune = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Do not accidentally expose tokens or user input from exceptions.
            log.warning('TG maintenance failed type=%s', type(exc).__name__)
        await sleep(interval)
