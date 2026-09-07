import aiosqlite
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from config import config

logger = logging.getLogger(__name__)

# Ensure data directory exists
Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)


def init_db_sync(db_path: str = config.DB_PATH):
    """
    Synchronous DB initialization for startup tables (RTB v2.0).
    Clean schema strictly containing 4 tables: channels, signals, trades, settings.
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. Channels Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT UNIQUE NOT NULL,
        title TEXT,
        weight REAL DEFAULT 1.5,
        is_active INTEGER DEFAULT 1,
        total_signals INTEGER DEFAULT 0,
        winning_signals INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 2. Signals Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type TEXT NOT NULL DEFAULT 'CHANNEL', -- USER, CHANNEL
        source_name TEXT NOT NULL,
        direction TEXT NOT NULL,                     -- BUY, SELL, NEUTRAL
        zone_min REAL NOT NULL,
        zone_max REAL NOT NULL,
        sl REAL NOT NULL,
        tp_targets TEXT,                            -- JSON array e.g. "[2650.0, 2655.0]"
        raw_text TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # 3. Trades Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket INTEGER,
        direction TEXT NOT NULL,
        entry_price REAL NOT NULL,
        sl_price REAL NOT NULL,
        tp_price REAL NOT NULL,
        risk_percent REAL NOT NULL DEFAULT 1.0,
        risk_usd REAL NOT NULL,
        lot_size REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',        -- OPEN, CLOSED_TP, CLOSED_SL, CLOSED_BE
        pnl_usd REAL DEFAULT 0.0,
        pnl_r REAL DEFAULT 0.0,
        close_reason TEXT,
        sources_json TEXT,                          -- JSON list of sources
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP
    );
    """)

    # 4. Settings Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY NOT NULL,
        value TEXT NOT NULL,
        description TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Populate default settings if not exists
    default_settings = [
        ("risk_percent", str(config.RISK_PER_TRADE_PERCENT), "Risk percentage per trade (1.0% - 5.0%)"),
        ("max_daily_drawdown", str(config.MAX_DAILY_DRAWDOWN_PERCENT), "Max daily loss stop in %"),
        ("max_sl_pips", str(config.MAX_SL_PIPS), "Max allowed SL in pips (50 pips = 5.0 USD)"),
        ("target_rr", str(config.TARGET_RR), "Target Risk-to-Reward ratio"),
        ("trading_enabled", "1", "Master trading kill-switch (1 = enabled, 0 = paused)")
    ]
    cursor.executemany("""
    INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?);
    """, default_settings)

    conn.commit()
    conn.close()
    logger.info(f"SQLite DB initialized with 4 core tables at: {db_path}")


class Database:
    """
    Asynchronous Database Access Layer for RTB v2.0.
    Manages: channels, signals, trades, settings.
    """

    def __init__(self, db_path: str = config.DB_PATH):
        self.db_path = db_path

    # ==========================================
    # 1. CHANNELS CRUD
    # ==========================================
    async def add_channel(self, channel_id: str, title: str, weight: float = config.WEIGHT_CHANNEL_DEFAULT):
        clamped_weight = round(max(1.0, min(10.0, float(weight))), 2)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO channels (channel_id, title, weight, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(channel_id) DO UPDATE SET title = excluded.title, is_active = 1, weight = excluded.weight
                """,
                (str(channel_id), title, clamped_weight)
            )
            await db.commit()

    async def remove_channel(self, channel_id: str):
        async with aiosqlite.connect(self.db_path) as db:
            clean_id = str(channel_id).strip()
            clean_id = clean_id.replace("https://t.me/", "").replace("t.me/", "")
            no_at = clean_id.lstrip("@")
            with_at = f"@{no_at}"
            await db.execute(
                """
                DELETE FROM channels 
                WHERE channel_id = ? 
                   OR channel_id = ? 
                   OR channel_id = ?
                   OR CAST(id AS TEXT) = ?
                """,
                (clean_id, no_at, with_at, clean_id)
            )
            await db.commit()

    async def delete_channel_hard(self, channel_id: str):
        await self.remove_channel(channel_id)

    async def get_active_channels(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM channels WHERE is_active = 1 ORDER BY id ASC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_channel_stats(self, channel_id: str, is_win: bool):
        async with aiosqlite.connect(self.db_path) as db:
            win_inc = 1 if is_win else 0
            await db.execute(
                """
                UPDATE channels 
                SET total_signals = total_signals + 1,
                    winning_signals = winning_signals + ?
                WHERE channel_id = ?
                """,
                (win_inc, str(channel_id))
            )
            await db.commit()

    # ==========================================
    # 2. SIGNALS CRUD
    # ==========================================
    async def save_signal(
        self,
        source_type: str,
        source_name: str,
        direction: str,
        zone_min: float,
        zone_max: float,
        sl: float,
        tp_targets: List[float],
        raw_text: str = ""
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO signals 
                (source_type, source_name, direction, zone_min, zone_max, sl, tp_targets, raw_text, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (source_type, source_name, direction, zone_min, zone_max, sl, json.dumps(tp_targets), raw_text)
            )
            await db.commit()
            return cursor.lastrowid

    async def get_latest_signals(self, limit: int = 5) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                signals = []
                for row in rows:
                    item = dict(row)
                    item["tp_targets"] = json.loads(item["tp_targets"]) if item["tp_targets"] else []
                    signals.append(item)
                return signals

    async def get_recent_active_signals(self, max_age_hours: int = 4) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = """
            SELECT * FROM signals 
            WHERE is_active = 1 
              AND datetime(created_at) >= datetime('now', ?)
            ORDER BY id DESC
            """
            async with db.execute(query, (f"-{max_age_hours} hours",)) as cursor:
                rows = await cursor.fetchall()
                signals = []
                for row in rows:
                    item = dict(row)
                    item["tp_targets"] = json.loads(item["tp_targets"]) if item["tp_targets"] else []
                    signals.append(item)
                return signals

    # ==========================================
    # 3. TRADES CRUD
    # ==========================================
    async def open_trade(
        self,
        ticket: int,
        direction: str,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        risk_percent: float,
        risk_usd: float,
        lot_size: float,
        sources: List[str] = None
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO trades 
                (ticket, direction, entry_price, sl_price, tp_price, risk_percent, risk_usd, lot_size, status, sources_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
                """,
                (
                    ticket, direction, entry_price, sl_price, tp_price,
                    risk_percent, risk_usd, lot_size, json.dumps(sources or [])
                )
            )
            await db.commit()
            return cursor.lastrowid

    async def close_trade(
        self,
        trade_id: int,
        status: str,
        pnl_usd: float,
        pnl_r: float,
        close_reason: str = ""
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE trades 
                SET status = ?, pnl_usd = ?, pnl_r = ?, close_reason = ?, closed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, pnl_usd, pnl_r, close_reason, trade_id)
            )
            await db.commit()

    async def get_open_trades(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY id DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_trade_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_performance_summary(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = """
            SELECT 
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as win_trades,
                SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) as loss_trades,
                COALESCE(SUM(pnl_usd), 0.0) as total_pnl_usd,
                COALESCE(SUM(pnl_r), 0.0) as total_pnl_r
            FROM trades
            WHERE status != 'OPEN'
            """
            async with db.execute(query) as cursor:
                row = await cursor.fetchone()
                res = dict(row) if row else {}
                total = res.get("total_trades", 0) or 0
                wins = res.get("win_trades", 0) or 0
                win_rate = round((wins / total) * 100, 1) if total > 0 else 0.0
                res["win_rate"] = win_rate
                return res

    # ==========================================
    # 4. SETTINGS CRUD
    # ==========================================
    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    async def set_setting(self, key: str, value: str, description: Optional[str] = None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO settings (key, value, description, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET 
                    value = excluded.value, 
                    description = COALESCE(excluded.description, settings.description),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, str(value), description)
            )
            await db.commit()

    async def get_all_settings(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, value FROM settings") as cursor:
                rows = await cursor.fetchall()
                return {r[0]: r[1] for r in rows}


db = Database()
