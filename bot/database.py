import aiosqlite
from typing import List, Dict, Optional, Any
import time

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def _execute(self, query: str, params: tuple = (), commit: bool = False) -> Optional[List[Dict]]:
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(query, params)
            if commit:
                await conn.commit()
                return None
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def init(self):
        await self._execute('''
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT, score INTEGER, duration INTEGER, timestamp INTEGER
            )
        ''', commit=True)
        await self._execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT, first_name TEXT, last_name TEXT,
                first_seen INTEGER, last_seen INTEGER
            )
        ''', commit=True)
        await self._execute('''
            CREATE TABLE IF NOT EXISTS ai_history (
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp INTEGER,
                PRIMARY KEY (user_id, timestamp)
            )
        ''', commit=True)
        await self._execute('CREATE INDEX IF NOT EXISTS idx_ai_user ON ai_history(user_id)', commit=True)

    async def save_user(self, user_id: int, username: str, first_name: str, last_name: str):
        now = int(time.time())
        await self._execute('''
            INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen)
            VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, first_name=excluded.first_name, last_seen=excluded.last_seen
        ''', (user_id, username, first_name, last_name, now, now), commit=True)

    async def add_record(self, name: str, score: int, duration: int):
        await self._execute(
            'INSERT INTO records (name, score, duration, timestamp) VALUES (?,?,?,?)',
            (name, score, duration, int(time.time())), commit=True
        )

    async def get_top_records(self, limit=20) -> List[Dict]:
        return await self._execute(
            'SELECT * FROM records ORDER BY score DESC LIMIT ?', (limit,)
        ) or []

    async def get_ai_history(self, user_id: int, limit=10) -> List[Dict]:
        rows = await self._execute(
            'SELECT role, content FROM ai_history WHERE user_id=? ORDER BY timestamp ASC LIMIT ?',
            (user_id, limit)
        )
        return rows or []

    async def add_ai_message(self, user_id: int, role: str, content: str):
        await self._execute(
            'INSERT INTO ai_history (user_id, role, content, timestamp) VALUES (?,?,?,?)',
            (user_id, role, content, int(time.time())), commit=True
        )

    async def clear_ai_history(self, user_id: int = None):
        if user_id:
            await self._execute('DELETE FROM ai_history WHERE user_id=?', (user_id,), commit=True)
        else:
            await self._execute('DELETE FROM ai_history', commit=True)

    async def get_all_users(self) -> List[Dict]:
        return await self._execute('SELECT * FROM users ORDER BY last_seen DESC') or []

    # ... (остальные методы при необходимости)