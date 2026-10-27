import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

GUILD_ID = 1402689392677490718
# Private categories are omitted: existing private permissions remain untouched.
LAYOUT = [
    ('❄ ─── 처음 오셨나요 ───', ['📌 시작 및 안내', '01  ──  INFORMATION'], [
        ('👋┃시작하기', ['시작하기', 'welcome']), ('📢┃공지사항', ['공지사항', 'announcements']),
        ('📖┃이용규칙', ['서버규칙', '이용규칙', 'guidelines']), ('🎭┃역할선택', ['역할선택'])]),
    ('☁ ─── 설온 라운지 ───', ['💬 커뮤니티', '02  ──  SOCIAL'], [
        ('💬┃도란도란', ['자유채팅', 'lounge']), ('📷┃사진과기록', ['사진·일상', 'media']),
        ('🎵┃취향공유', ['게임추천']), ('💡┃질문과도움', ['질문·도움'])]),
    ('✦ ─── 함께하는 시간 ───', ['🎮 게임 및 파티', '03  ──  GAMING'], [
        ('🎮┃파티모집', ['파티모집', 'party-finder']), ('🕹️┃게임이야기', ['게임정보', 'game-chat']),
        ('🏆┃오늘의성과', ['플레이기록']), ('🎉┃이벤트', ['이벤트공지'])]),
    ('✧ ─── 눈꽃 기록실 ───', [], [
        ('📅┃출석체크', ['출석체크']), ('💳┃설온여권', []), ('🛍️┃눈꽃상점', []),
        ('🏅┃명예의전당', ['명예의전당']), ('🤖┃봇명령어', [])]),
]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS attendance (
                guild INTEGER, user INTEGER, day TEXT, reward INTEGER,
                PRIMARY KEY (guild,user,day));
            CREATE TABLE IF NOT EXISTS wallet (
                guild INTEGER, user INTEGER, balance INTEGER DEFAULT 0,
                PRIMARY KEY (guild,user));
            CREATE TABLE IF NOT EXISTS objects (
                key TEXT PRIMARY KEY, id INTEGER NOT NULL);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.execute('PRAGMA journal_mode=WAL')
        return db

    def get(self, key):
        with self.connect() as db:
            row = db.execute('SELECT id FROM objects WHERE key=?', (key,)).fetchone()
            return row[0] if row else None

    def put(self, key, value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO objects VALUES (?,?)', (key, value))

    def attend(self, guild, user, day=None):
        day = day or datetime.now(ZoneInfo('Asia/Seoul')).date()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM attendance WHERE guild=? AND user=? AND day=?',
                          (guild, user, day.isoformat())).fetchone():
                return None
            streak = 1
            previous = day - timedelta(days=1)
            while db.execute('SELECT 1 FROM attendance WHERE guild=? AND user=? AND day=?',
                             (guild, user, previous.isoformat())).fetchone():
                streak += 1
                previous -= timedelta(days=1)
            reward = 30 + (100 if streak % 7 == 0 else 0) + (500 if streak % 30 == 0 else 0)
            db.execute('INSERT INTO attendance VALUES (?,?,?,?)', (guild, user, day.isoformat(), reward))
            db.execute('INSERT INTO wallet VALUES (?,?,?) ON CONFLICT(guild,user) DO UPDATE SET balance=balance+excluded.balance',
                       (guild, user, reward))
            return reward, streak

    def profile(self, guild, user):
        with self.connect() as db:
            row = db.execute('SELECT balance FROM wallet WHERE guild=? AND user=?', (guild, user)).fetchone()
            days = db.execute('SELECT count(*) FROM attendance WHERE guild=? AND user=?', (guild, user)).fetchone()[0]
            return (row[0] if row else 0), days
