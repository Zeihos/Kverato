"""SQLite storage. Every write is transactional; safe across adapter processes."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path
from typing import Iterator


class AutoClosingConnection(sqlite3.Connection):
    """SQLite with-context closes the connection (important on Windows)."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: str | Path = 'data/kverato.sqlite3') -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None,
                                     factory=AutoClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 15000')
        connection.execute('PRAGMA foreign_keys = ON')
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as con:
            con.execute('PRAGMA journal_mode = WAL')
            con.executescript('''
                CREATE TABLE IF NOT EXISTS players (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    coins INTEGER NOT NULL DEFAULT 100 CHECK (coins >= 0),
                    xp INTEGER NOT NULL DEFAULT 0 CHECK (xp >= 0),
                    badge TEXT NOT NULL DEFAULT 'Игрок',
                    daily_at INTEGER NOT NULL DEFAULT 0,
                    work_at INTEGER NOT NULL DEFAULT 0,
                    last_xp_at INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (platform, user_id)
                );
                CREATE TABLE IF NOT EXISTS purchases (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    purchased_at INTEGER NOT NULL,
                    PRIMARY KEY(platform, user_id, item_key),
                    FOREIGN KEY(platform, user_id) REFERENCES players(platform, user_id)
                );
                CREATE TABLE IF NOT EXISTS warnings (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(platform, chat_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    from_user_id TEXT NOT NULL,
                    to_user_id TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK (amount > 0),
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS moderation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    moderator_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_roles (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(platform, chat_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS achievements (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    achievement_key TEXT NOT NULL,
                    earned_at INTEGER NOT NULL,
                    PRIMARY KEY(platform, user_id, achievement_key),
                    FOREIGN KEY(platform, user_id) REFERENCES players(platform, user_id)
                );
                CREATE TABLE IF NOT EXISTS chat_settings (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    antiflood INTEGER NOT NULL DEFAULT 0 CHECK(antiflood IN (0, 1)),
                    warn_limit INTEGER NOT NULL DEFAULT 3 CHECK(warn_limit BETWEEN 1 AND 10),
                    PRIMARY KEY(platform, chat_id)
                );
                CREATE TABLE IF NOT EXISTS quest_progress (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    utc_day INTEGER NOT NULL,
                    work_done INTEGER NOT NULL DEFAULT 0 CHECK(work_done >= 0),
                    games_done INTEGER NOT NULL DEFAULT 0 CHECK(games_done >= 0),
                    wins_done INTEGER NOT NULL DEFAULT 0 CHECK(wins_done >= 0),
                    claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
                    PRIMARY KEY(platform, user_id, utc_day),
                    FOREIGN KEY(platform, user_id) REFERENCES players(platform, user_id)
                );
                CREATE TABLE IF NOT EXISTS chat_rules (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    rules TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(platform, chat_id)
                );
                CREATE TABLE IF NOT EXISTS duels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    challenger_id TEXT NOT NULL,
                    opponent_id TEXT NOT NULL,
                    stake INTEGER NOT NULL CHECK(stake BETWEEN 1 AND 5000),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','accepted','declined','cancelled','expired')),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    finished_at INTEGER NOT NULL DEFAULT 0,
                    winner_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_duel_pending_expiry
                    ON duels(status,expires_at);
                CREATE INDEX IF NOT EXISTS idx_duel_chat
                    ON duels(platform,chat_id,status);
                CREATE TABLE IF NOT EXISTS reputation_events (
                    platform TEXT NOT NULL,
                    giver_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    utc_day INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,giver_id,utc_day)
                );
                CREATE INDEX IF NOT EXISTS idx_reputation_received
                    ON reputation_events(platform,recipient_id);
                CREATE TABLE IF NOT EXISTS chat_welcomes (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    welcome_text TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,chat_id)
                );
                CREATE TABLE IF NOT EXISTS clans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    treasury INTEGER NOT NULL DEFAULT 0 CHECK(treasury >= 0),
                    score INTEGER NOT NULL DEFAULT 0 CHECK(score >= 0),
                    has_emblem INTEGER NOT NULL DEFAULT 0 CHECK(has_emblem IN (0,1)),
                    has_hall INTEGER NOT NULL DEFAULT 0 CHECK(has_hall IN (0,1)),
                    created_at INTEGER NOT NULL,
                    UNIQUE(platform,name_key)
                );
                CREATE TABLE IF NOT EXISTS clan_members (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                    joined_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_clan_members ON clan_members(clan_id);
                CREATE TABLE IF NOT EXISTS clan_quest_claims (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    utc_day INTEGER NOT NULL,
                    clan_id INTEGER NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,utc_day)
                );
                CREATE TABLE IF NOT EXISTS clan_daily_progress (
                    clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                    utc_day INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed >= 0),
                    rewarded INTEGER NOT NULL DEFAULT 0 CHECK(rewarded IN (0,1)),
                    PRIMARY KEY(clan_id,utc_day)
                );
                CREATE TABLE IF NOT EXISTS clan_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    clan_id INTEGER NOT NULL REFERENCES clans(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    delta INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_clan_ledger ON clan_ledger(clan_id,id DESC);
                CREATE TABLE IF NOT EXISTS chat_activity (
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    hits INTEGER NOT NULL DEFAULT 0,
                    notified_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(platform, chat_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_log_chat
                    ON moderation_log(platform, chat_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_transfer_sender
                    ON transfers(platform, from_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_coins ON players(platform, coins DESC);
                CREATE INDEX IF NOT EXISTS idx_xp ON players(platform, xp DESC);
            ''')
            # management is for access to THIS bot, not VK chat moderation.
            # Legacy chat tables remain untouched so upgrading never erases data.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS bot_staff (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin','helper')),
                    appointed_by TEXT NOT NULL, appointed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS bot_access (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    blocked_until INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL, blocked_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS bot_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, actor_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    action TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS bot_audit_recent ON bot_audit(platform,id DESC);
                CREATE TABLE IF NOT EXISTS bot_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, sender_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('report','appeal')),
                    body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','closed')),
                    created_at INTEGER NOT NULL, closed_by TEXT, closed_at INTEGER, resolution TEXT
                );
                CREATE INDEX IF NOT EXISTS bot_reports_sender ON bot_reports(platform,sender_id,kind,created_at DESC);
                CREATE TABLE IF NOT EXISTS bot_promos (
                    platform TEXT NOT NULL, code TEXT NOT NULL,
                    reward INTEGER NOT NULL CHECK(reward BETWEEN 1 AND 5000),
                    max_uses INTEGER NOT NULL CHECK(max_uses BETWEEN 1 AND 1000),
                    uses INTEGER NOT NULL DEFAULT 0 CHECK(uses >= 0),
                    expires_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    created_by TEXT NOT NULL, created_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,code)
                );
                CREATE TABLE IF NOT EXISTS bot_promo_redemptions (
                    platform TEXT NOT NULL, code TEXT NOT NULL, user_id TEXT NOT NULL,
                    claimed_at INTEGER NOT NULL, PRIMARY KEY(platform,code,user_id),
                    FOREIGN KEY(platform,code) REFERENCES bot_promos(platform,code)
                );
                CREATE TABLE IF NOT EXISTS bot_flags (
                    platform TEXT NOT NULL, feature TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,feature)
                );
                CREATE TABLE IF NOT EXISTS bot_economy_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
                    actor_id TEXT NOT NULL, target_id TEXT NOT NULL,
                    delta INTEGER NOT NULL, source TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
            ''')
            # VK replay protection, independent of existing player data.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS vk_processed_events (
                    event_key TEXT PRIMARY KEY,
                    received_at INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('claimed','done','uncertain'))
                );
                CREATE INDEX IF NOT EXISTS idx_vk_processed_events_at
                    ON vk_processed_events(received_at);
            ''')
            # uses append-only / additive tables; no original player rows are touched.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS career_progress (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, job_key TEXT NOT NULL,
                    xp INTEGER NOT NULL DEFAULT 0 CHECK(xp >= 0),
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed >= 0),
                    PRIMARY KEY(platform,user_id,job_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS player_businesses (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, business_key TEXT NOT NULL,
                    tier INTEGER NOT NULL DEFAULT 1 CHECK(tier BETWEEN 1 AND 5),
                    invested INTEGER NOT NULL CHECK(invested >= 0),
                    purchased_at INTEGER NOT NULL, collected_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,business_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS material_balances (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, material_key TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK(amount BETWEEN 0 AND 1000),
                    PRIMARY KEY(platform,user_id,material_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS daily_contracts (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed BETWEEN 0 AND 3),
                    PRIMARY KEY(platform,user_id,utc_day),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
            ''')
            # new, independent tables. Existing financial and social data are untouched.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS player_homes (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    home_key TEXT NOT NULL, tier INTEGER NOT NULL DEFAULT 1 CHECK(tier BETWEEN 1 AND 3),
                    invested INTEGER NOT NULL CHECK(invested>=0),
                    decorated INTEGER NOT NULL DEFAULT 0 CHECK(decorated IN (0,1)),
                    bought_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS player_vehicles (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    vehicle_key TEXT NOT NULL,
                    tuned INTEGER NOT NULL DEFAULT 0 CHECK(tuned IN (0,1)),
                    bought_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,vehicle_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS player_energy (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    tier INTEGER NOT NULL DEFAULT 1 CHECK(tier BETWEEN 1 AND 3),
                    collected_at INTEGER NOT NULL,
                    stored INTEGER NOT NULL DEFAULT 0 CHECK(stored BETWEEN 0 AND 200),
                    PRIMARY KEY(platform,user_id),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS crafted_inventory (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, item_key TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK(amount BETWEEN 0 AND 100),
                    PRIMARY KEY(platform,user_id,item_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
            ''')
            # competitive racing and daily prize-pool tournaments.
            con.executescript(''' 
                CREATE TABLE IF NOT EXISTS race_progress (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    races INTEGER NOT NULL DEFAULT 0 CHECK(races>=0),
                    wins INTEGER NOT NULL DEFAULT 0 CHECK(wins>=0),
                    rating INTEGER NOT NULL DEFAULT 0 CHECK(rating>=0),
                    best_score INTEGER NOT NULL DEFAULT 0 CHECK(best_score>=0),
                    last_solo_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(platform,user_id),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS race_challenges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, chat_id TEXT NOT NULL,
                    challenger_id TEXT NOT NULL, opponent_id TEXT NOT NULL,
                    vehicle_key TEXT NOT NULL, tuned INTEGER NOT NULL CHECK(tuned IN (0,1)),
                    stake INTEGER NOT NULL CHECK(stake BETWEEN 1 AND 2000),
                    status TEXT NOT NULL DEFAULT 'pending',
                    winner_id TEXT,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                    finished_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_race_challenges_pending
                  ON race_challenges(platform,status,expires_at);
                CREATE TABLE IF NOT EXISTS race_tournaments (
                    platform TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    track_key TEXT NOT NULL,
                    opens_at INTEGER NOT NULL, closes_at INTEGER NOT NULL,
                    finished_at INTEGER,
                    winner_id TEXT, runner_up_id TEXT,
                    PRIMARY KEY(platform,utc_day)
                );
                CREATE TABLE IF NOT EXISTS race_tournament_entries (
                    platform TEXT NOT NULL, utc_day INTEGER NOT NULL, user_id TEXT NOT NULL,
                    vehicle_key TEXT NOT NULL, tuned INTEGER NOT NULL CHECK(tuned IN (0,1)),
                    entry_fee INTEGER NOT NULL CHECK(entry_fee>=0),
                    joined_at INTEGER NOT NULL, score INTEGER,
                    prize INTEGER NOT NULL DEFAULT 0 CHECK(prize>=0),
                    PRIMARY KEY(platform,utc_day,user_id),
                    FOREIGN KEY(platform,utc_day) REFERENCES race_tournaments(platform,utc_day)
                );
            ''')
            # exploration and joint city projects. Additive migration only.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS expedition_daily (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    starts INTEGER NOT NULL DEFAULT 0 CHECK(starts BETWEEN 0 AND 3),
                    PRIMARY KEY(platform,user_id,utc_day)
                );
                CREATE TABLE IF NOT EXISTS expedition_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    district TEXT NOT NULL, event_id INTEGER NOT NULL CHECK(event_id BETWEEN 0 AND 2),
                    status TEXT NOT NULL CHECK(status IN ('active','finished','expired','abandoned')),
                    choice TEXT, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                    finished_at INTEGER, reward_material TEXT, reward_amount INTEGER, reward_relic TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_active_one
                    ON expedition_runs(platform,user_id) WHERE status='active';
                CREATE INDEX IF NOT EXISTS idx_exp_user
                    ON expedition_runs(platform,user_id,id DESC);
                CREATE TABLE IF NOT EXISTS explorer_gear (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 3),
                    PRIMARY KEY(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS explorer_progress (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed>=0),
                    finds INTEGER NOT NULL DEFAULT 0 CHECK(finds>=0),
                    PRIMARY KEY(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS explorer_relics (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, relic_key TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK(amount BETWEEN 0 AND 30),
                    PRIMARY KEY(platform,user_id,relic_key)
                );
                CREATE TABLE IF NOT EXISTS city_project_contributions (
                    platform TEXT NOT NULL, utc_day INTEGER NOT NULL, user_id TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0 CHECK(points BETWEEN 1 AND 20),
                    claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
                    PRIMARY KEY(platform,utc_day,user_id)
                );
            ''')
            # wardrobes, transfers and escrow. Additive migration.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS wardrobe_items (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, item_key TEXT NOT NULL,
                    amount INTEGER NOT NULL CHECK(amount BETWEEN 0 AND 50),
                    PRIMARY KEY(platform,user_id,item_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS wardrobe_equipped (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, slot TEXT NOT NULL,
                    item_key TEXT NOT NULL, PRIMARY KEY(platform,user_id,slot),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS wardrobe_gifts_daily (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0 CHECK(sent BETWEEN 0 AND 3),
                    PRIMARY KEY(platform,user_id,utc_day)
                );
                CREATE TABLE IF NOT EXISTS wardrobe_listings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
                    seller_id TEXT NOT NULL, item_key TEXT NOT NULL,
                    price INTEGER NOT NULL CHECK(price BETWEEN 10 AND 100000),
                    status TEXT NOT NULL CHECK(status IN ('open','sold','cancelled')),
                    created_at INTEGER NOT NULL, closed_at INTEGER, buyer_id TEXT,
                    FOREIGN KEY(platform,seller_id) REFERENCES players(platform,user_id)
                );
                CREATE INDEX IF NOT EXISTS wardrobe_market_active
                    ON wardrobe_listings(platform,status,id DESC);
                CREATE INDEX IF NOT EXISTS wardrobe_market_seller
                    ON wardrobe_listings(platform,seller_id,status);
                CREATE TABLE IF NOT EXISTS wardrobe_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
                    actor_id TEXT NOT NULL, counterparty_id TEXT NOT NULL,
                    item_key TEXT NOT NULL, kind TEXT NOT NULL,
                    amount INTEGER NOT NULL, coins INTEGER NOT NULL,
                    created_at INTEGER NOT NULL, listing_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS wardrobe_ledger_user
                    ON wardrobe_ledger(platform,actor_id,id DESC);
                CREATE TABLE IF NOT EXISTS wardrobe_set_claims (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, set_key TEXT NOT NULL,
                    claimed_at INTEGER NOT NULL, PRIMARY KEY(platform,user_id,set_key)
                );
            ''')
            # season progression and frozen historical rankings.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS season_scores (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, season_id INTEGER NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0 CHECK(points>=0),
                    tokens INTEGER NOT NULL DEFAULT 0 CHECK(tokens>=0),
                    earned_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,season_id),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_season_scores
                    ON season_scores(platform,season_id,points DESC);
                CREATE TABLE IF NOT EXISTS season_activity (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, season_id INTEGER NOT NULL,
                    utc_day INTEGER NOT NULL, kind TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,utc_day,kind)
                );
                CREATE TABLE IF NOT EXISTS season_daily_claims (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    season_id INTEGER NOT NULL, claimed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,utc_day)
                );
                CREATE TABLE IF NOT EXISTS season_tier_claims (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, season_id INTEGER NOT NULL,
                    tier INTEGER NOT NULL CHECK(tier BETWEEN 1 AND 4), claimed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,season_id,tier)
                );
                CREATE TABLE IF NOT EXISTS season_titles (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, season_id INTEGER NOT NULL,
                    title TEXT NOT NULL, equipped INTEGER NOT NULL DEFAULT 0 CHECK(equipped IN (0,1)),
                    PRIMARY KEY(platform,user_id,season_id)
                );
                CREATE TABLE IF NOT EXISTS season_collectibles (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, season_id INTEGER NOT NULL,
                    item_key TEXT NOT NULL, bought_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,season_id,item_key)
                );
                CREATE TABLE IF NOT EXISTS season_finalized (
                    platform TEXT NOT NULL, season_id INTEGER NOT NULL,
                    PRIMARY KEY(platform,season_id)
                );
                CREATE TABLE IF NOT EXISTS season_hall (
                    platform TEXT NOT NULL, season_id INTEGER NOT NULL, rank INTEGER NOT NULL,
                    user_id TEXT NOT NULL, points INTEGER NOT NULL,
                    PRIMARY KEY(platform,season_id,rank),
                    UNIQUE(platform,season_id,user_id)
                );
                CREATE TABLE IF NOT EXISTS season_podium_claims (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, season_id INTEGER NOT NULL,
                    claimed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,season_id)
                );
            ''')
            # additive virtual shop content. Existing saves are not rewritten.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS pet_owned (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    pet_key TEXT NOT NULL, purchased_at INTEGER NOT NULL,
                    bond INTEGER NOT NULL DEFAULT 0 CHECK(bond BETWEEN 0 AND 10),
                    last_fed_day INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY(platform,user_id,pet_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS pet_selected (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, pet_key TEXT NOT NULL,
                    PRIMARY KEY(platform,user_id),
                    FOREIGN KEY(platform,user_id,pet_key) REFERENCES pet_owned(platform,user_id,pet_key)
                );
                CREATE TABLE IF NOT EXISTS box_items (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, box_key TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 0 CHECK(amount BETWEEN 0 AND 30),
                    PRIMARY KEY(platform,user_id,box_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS box_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
                    user_id TEXT NOT NULL, box_key TEXT NOT NULL,
                    material_key TEXT NOT NULL, amount INTEGER NOT NULL,
                    opened_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS title_owned (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, title_key TEXT NOT NULL,
                    bought_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,title_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS title_selected (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, title_key TEXT NOT NULL,
                    PRIMARY KEY(platform,user_id),
                    FOREIGN KEY(platform,user_id,title_key) REFERENCES title_owned(platform,user_id,title_key)
                );
            ''')
            # immutable history for multi-resource contracts; no existing rows altered.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS industry_order_log (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL,
                    utc_day INTEGER NOT NULL, order_key TEXT NOT NULL,
                    reward INTEGER NOT NULL CHECK(reward > 0),
                    job_key TEXT NOT NULL, completed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,utc_day,order_key),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_industry_order_log_user
                    ON industry_order_log(platform,user_id,completed_at);
            ''')
            # independent daily transport and clan activity ledgers.
            con.executescript('''
                CREATE TABLE IF NOT EXISTS transport_deliveries (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    route_key TEXT NOT NULL, vehicle_key TEXT NOT NULL,
                    reward INTEGER NOT NULL CHECK(reward>=0), completed_at INTEGER NOT NULL,
                    PRIMARY KEY(platform,user_id,utc_day),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id)
                );
                CREATE TABLE IF NOT EXISTS clan_expeditions (
                    platform TEXT NOT NULL, user_id TEXT NOT NULL, utc_day INTEGER NOT NULL,
                    clan_id INTEGER NOT NULL, route_key TEXT NOT NULL,
                    points INTEGER NOT NULL CHECK(points>0), created_at INTEGER NOT NULL,
                    claimed INTEGER NOT NULL DEFAULT 0 CHECK(claimed IN (0,1)),
                    PRIMARY KEY(platform,user_id,utc_day),
                    FOREIGN KEY(platform,user_id) REFERENCES players(platform,user_id),
                    FOREIGN KEY(clan_id) REFERENCES clans(id)
                );
                CREATE INDEX IF NOT EXISTS idx_clan_expedition_daily
                    ON clan_expeditions(platform,clan_id,utc_day);
            ''')
            # A separate ledger safely upgrades existing v1.0 databases.
            con.execute('''
                CREATE TABLE IF NOT EXISTS weekly_rewards (
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    claimed_at INTEGER NOT NULL CHECK(claimed_at >= 0),
                    PRIMARY KEY(platform, user_id),
                    FOREIGN KEY(platform, user_id)
                        REFERENCES players(platform, user_id) ON DELETE CASCADE
                )
            ''')
            # Idempotent migration: add columns without replacing old player rows.
            columns = {row['name'] for row in con.execute('PRAGMA table_info(players)')}
            for name, definition in (
                ('bank_coins', 'INTEGER NOT NULL DEFAULT 0 CHECK(bank_coins >= 0)'),
                ('job_key', "TEXT NOT NULL DEFAULT 'courier'"),
                ('work_count', 'INTEGER NOT NULL DEFAULT 0'),
                ('games_played', 'INTEGER NOT NULL DEFAULT 0'),
                ('games_won', 'INTEGER NOT NULL DEFAULT 0'),
                ('daily_streak', 'INTEGER NOT NULL DEFAULT 0 CHECK(daily_streak >= 0)'),
                ('reputation', 'INTEGER NOT NULL DEFAULT 0 CHECK(reputation >= 0)'),
            ):
                if name not in columns:
                    con.execute(f'ALTER TABLE players ADD COLUMN {name} {definition}')

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
