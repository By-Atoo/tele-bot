import sqlite3
import time
import logging

logger = logging.getLogger(__name__)

class Database:
    _instance = None

    def __new__(cls, db_filename):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init(db_filename)
        return cls._instance

    def _init(self, db_filename):
        self.db_filename = db_filename
        self.conn = sqlite3.connect(db_filename, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def _connection(self):
        return self.conn

    def init_db(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS records
                             (id INTEGER PRIMARY KEY AUTOINCREMENT,
                              name TEXT, score INTEGER, duration INTEGER, timestamp INTEGER)''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS ai_logs
                             (id INTEGER PRIMARY KEY AUTOINCREMENT,
                              user_id INTEGER,
                              username TEXT,
                              first_name TEXT,
                              last_name TEXT,
                              message TEXT,
                              response TEXT,
                              timestamp INTEGER)''')
        self.conn.execute('''CREATE TABLE IF NOT EXISTS users
                             (user_id INTEGER PRIMARY KEY,
                              username TEXT,
                              first_name TEXT,
                              last_name TEXT,
                              first_seen INTEGER,
                              last_seen INTEGER)''')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_score ON records(score);')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_name ON records(name);')
        self.conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON records(timestamp);')
        self.conn.commit()
        logger.info(f"База данных инициализирована: {self.db_filename}")

    # ---------- game records ----------
    def save_record(self, name, score, duration):
        self.conn.execute(
            'INSERT INTO records (name, score, duration, timestamp) VALUES (?,?,?,?)',
            (name, score, duration, int(time.time()))
        )
        self.conn.commit()

    def get_top_records(self, limit=20):
        rows = self.conn.execute(
            'SELECT * FROM records ORDER BY score DESC LIMIT ?', (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_records(self, order_by='score DESC'):
        allowed = {
            'score DESC': 'score DESC',
            'score ASC': 'score ASC',
            'duration DESC': 'duration DESC',
            'duration ASC': 'duration ASC',
            'timestamp DESC': 'timestamp DESC',
            'timestamp ASC': 'timestamp ASC'
        }
        order_clause = allowed.get(order_by, 'score DESC')
        rows = self.conn.execute(
            f'SELECT * FROM records ORDER BY {order_clause}'
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_record(self, record_id):
        self.conn.execute('DELETE FROM records WHERE id = ?', (record_id,))
        self.conn.commit()

    def delete_all_records(self):
        self.conn.execute('DELETE FROM records')
        self.conn.commit()

    def update_record(self, record_id, field, value):
        if field not in ('name', 'score', 'duration'):
            raise ValueError("Invalid field")
        self.conn.execute(f'UPDATE records SET {field} = ? WHERE id = ?', (value, record_id))
        self.conn.commit()

    def get_record_by_id(self, record_id):
        row = self.conn.execute(
            'SELECT * FROM records WHERE id = ?', (record_id,)
        ).fetchone()
        return dict(row) if row else None

    def search_records_by_name(self, query):
        rows = self.conn.execute(
            'SELECT * FROM records WHERE name LIKE ? ORDER BY score DESC',
            (f'%{query}%',)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_statistics(self):
        row = self.conn.execute(
            'SELECT COUNT(*), AVG(score), MAX(score), MIN(score) FROM records'
        ).fetchone()
        return {
            'count': row[0] or 0,
            'avg': round(row[1], 2) if row[1] else 0,
            'max': row[2] or 0,
            'min': row[3] or 0
        }

    # ---------- AI logs ----------
    def save_ai_log(self, user_id, username, first_name, last_name, message, response):
        self.conn.execute(
            '''INSERT INTO ai_logs (user_id, username, first_name, last_name, message, response, timestamp)
               VALUES (?,?,?,?,?,?,?)''',
            (user_id, username, first_name, last_name, message, response, int(time.time()))
        )
        self.conn.commit()

    def get_ai_log_by_id(self, log_id):
        row = self.conn.execute('SELECT * FROM ai_logs WHERE id = ?', (log_id,)).fetchone()
        return dict(row) if row else None

    def get_ai_logs(self, limit=50, offset=0, search_query=None):
        if search_query:
            try:
                search_id = int(search_query)
                rows = self.conn.execute(
                    '''SELECT * FROM ai_logs
                       WHERE user_id = ? OR username LIKE ?
                       ORDER BY timestamp DESC LIMIT ? OFFSET ?''',
                    (search_id, f'%{search_query}%', limit, offset)
                ).fetchall()
            except ValueError:
                rows = self.conn.execute(
                    '''SELECT * FROM ai_logs
                       WHERE username LIKE ?
                       ORDER BY timestamp DESC LIMIT ? OFFSET ?''',
                    (f'%{search_query}%', limit, offset)
                ).fetchall()
        else:
            rows = self.conn.execute(
                'SELECT * FROM ai_logs ORDER BY timestamp DESC LIMIT ? OFFSET ?',
                (limit, offset)
            ).fetchall()
        return [dict(r) for r in rows]

    def count_ai_logs(self, search_query=None):
        if search_query:
            try:
                search_id = int(search_query)
                row = self.conn.execute(
                    'SELECT COUNT(*) FROM ai_logs WHERE user_id = ? OR username LIKE ?',
                    (search_id, f'%{search_query}%')
                ).fetchone()
            except ValueError:
                row = self.conn.execute(
                    'SELECT COUNT(*) FROM ai_logs WHERE username LIKE ?',
                    (f'%{search_query}%',)
                ).fetchone()
        else:
            row = self.conn.execute('SELECT COUNT(*) FROM ai_logs').fetchone()
        return row[0]

    def delete_ai_logs(self):
        self.conn.execute('DELETE FROM ai_logs')
        self.conn.commit()
        logger.info("Все логи ИИ удалены.")

    # ---------- users ----------
    def save_user(self, user):
        now = int(time.time())
        cur = self.conn.cursor()
        cur.execute('''UPDATE users SET username=?, first_name=?, last_name=?, last_seen=?
                       WHERE user_id=?''',
                    (user.username, user.first_name, user.last_name, now, user.id))
        if cur.rowcount == 0:
            cur.execute('''INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen)
                           VALUES (?, ?, ?, ?, ?, ?)''',
                        (user.id, user.username, user.first_name, user.last_name, now, now))
        self.conn.commit()

    def get_all_users(self):
        rows = self.conn.execute(
            'SELECT * FROM users ORDER BY last_seen DESC'
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user_by_id(self, user_id):
        row = self.conn.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)).fetchone()
        return dict(row) if row else None