"""Bounded settlement of virtual stakes already held in escrow.

Escrow refunds and prizes must not bypass the same 10**12 limits as regular
purchases and rewards. If the wallet is full, excess goes to the bank, without
creating or losing coins. Call only while holding Database.transaction().
"""
from __future__ import annotations

from .v07 import MAX_WALLET


def escrow_capacity(con, platform: str, user_id: str, *, debit: int = 0) -> int:
    """Remaining wallet + bank capacity after a planned wallet debit."""
    row = con.execute('''SELECT coins, bank_coins FROM players
                         WHERE platform=? AND user_id=?''', (platform, user_id)).fetchone()
    if not row or debit < 0 or row['coins'] < debit:
        return 0
    return max(0, MAX_WALLET - (row['coins'] - debit)) + max(0, MAX_WALLET - row['bank_coins'])


def credit_escrow(con, platform: str, user_id: str, amount: int) -> tuple[int, int] | None:
    """Credit an already-reserved prize atomically; return wallet/bank amounts."""
    if amount < 0:
        raise ValueError('Escrow amount cannot be negative')
    row = con.execute('''SELECT coins, bank_coins FROM players
                         WHERE platform=? AND user_id=?''', (platform, user_id)).fetchone()
    if row is None:
        return None
    wallet_room = max(0, MAX_WALLET - row['coins'])
    bank_room = max(0, MAX_WALLET - row['bank_coins'])
    if amount > wallet_room + bank_room:
        return None
    to_wallet = min(amount, wallet_room)
    to_bank = amount - to_wallet
    con.execute('''UPDATE players SET coins=coins+?, bank_coins=bank_coins+?
                   WHERE platform=? AND user_id=?''', (to_wallet, to_bank, platform, user_id))
    return to_wallet, to_bank
