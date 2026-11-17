"""Settle expiring game stakes periodically even when Discord chat is quiet."""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from .v05 import expire_duels
from .v10 import expire_races, settle_expired_tournaments

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceHealth:
    """Session-local scheduler health, never modifies economic transactions."""
    runs: int = 0
    last_success: int | None = None
    last_failure: int | None = None
    failure_type: str = ''
    last_settled: dict[str, int] = field(default_factory=dict)

    def record_success(self, results: dict[str, int]) -> None:
        self.runs += 1
        self.last_success = int(time.time())
        self.failure_type = ''
        self.last_settled = dict(results)

    def record_failure(self, exc: Exception) -> None:
        self.last_failure = int(time.time())
        self.failure_type = type(exc).__name__

    def describe(self, now: int | None = None) -> str:
        if not self.last_success:
            return 'ещё не выполнялось'
        age = max(0, (int(time.time()) if now is None else now) - self.last_success)
        if self.failure_type and self.last_failure is not None and self.last_failure >= self.last_success:
            return f'ошибка {self.failure_type} (последний успех {age} сек. назад)'
        return f'работает · проверок {self.runs} · последняя {age} сек. назад'


def settle_due(service) -> dict[str, int]:
    now = int(service.clock())
    with service.db.transaction() as con:
        return {
            'duels': expire_duels(con, now),
            'races': expire_races(con, now),
            'tournaments': settle_expired_tournaments(con, service, now),
        }


async def maintenance(service, store, friends=None, interval: int = 60, health: MaintenanceHealth | None = None):
    tick = 0
    while True:
        try:
            result = await asyncio.to_thread(settle_due, service)
            if any(result.values()):
                logger.info('Discord settlements duels=%s races=%s tournaments=%s',
                            result['duels'], result['races'], result['tournaments'])
            if tick % 1440 == 0:
                await asyncio.to_thread(store.prune)
                if friends is not None:
                    await asyncio.to_thread(friends.prune)
            if health is not None:
                health.record_success(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if health is not None:
                health.record_failure(exc)
            logger.warning('Discord background maintenance failed type=%s', type(exc).__name__)
        tick += 1
        await asyncio.sleep(interval)
