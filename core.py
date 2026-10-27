import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

GUILD_ID = 1402689392677490718
PROTECTED_CHANNEL_IDS = frozenset({
    1523604140108939285,
    1523611752837681192,
    1523611037591535768,
    1522946795217490141,
})
ARCHIVE_NAME = '── ⟡ 이전 기록 ⟡ ──'
VOICE_NAMES = frozenset({'﹒라운지', '﹒게임방', '＋ 새로운 방', '﹒자리비움'})
LAYOUT = [
    ('── ⟡ 처음의 설온 ⟡ ──', ['❄ ─── 처음 오셨나요 ───', '📌 시작 및 안내', '01  ──  INFORMATION'], [
        ('﹒시작하기', ['👋┃시작하기', '시작하기', 'welcome']), ('﹒공지사항', ['📢┃공지사항', '공지사항', 'announcements']),
        ('﹒이용규칙', ['📖┃이용규칙', '서버규칙', '이용규칙', 'guidelines']), ('﹒역할선택', ['🎭┃역할선택', '역할선택'])]),
    ('── ⟡ 머무는 공간 ⟡ ──', ['☁ ─── 설온 라운지 ───', '💬 커뮤니티', '02  ──  SOCIAL'], [
        ('﹒도란도란', ['💬┃도란도란', '자유채팅', 'lounge']), ('﹒사진과기록', ['📷┃사진과기록', '사진·일상', 'media']),
        ('﹒취향공유', ['🎵┃취향공유', '게임추천'])]),
    ('── ⟡ 함께하는 순간 ⟡ ──', ['✦ ─── 함께하는 시간 ───', '🎮 게임 및 파티', '03  ──  GAMING'], [
        ('﹒파티모집', ['🎮┃파티모집', '파티모집', 'party-finder']), ('﹒게임이야기', ['🕹️┃게임이야기', '게임정보', 'game-chat']),
        ('﹒이벤트', ['🎉┃이벤트', '이벤트공지'])]),
    ('── ⟡ 쌓이는 발자국 ⟡ ──', ['✧ ─── 눈꽃 기록실 ───'], [
        ('﹒출석체크', ['📅┃출석체크', '출석체크']), ('﹒눈꽃상점', ['🛍️┃눈꽃상점']), ('﹒봇명령어', ['🤖┃봇명령어'])]),
    ('── ⟡ 목소리의 온도 ⟡ ──', ['🔊 음성 라운지', '♫ ─── 목소리가 모이는 곳 ───'], [
        ('﹒라운지', ['☕┃도란도란', '자유 음성채팅', '자유 통화방']), ('﹒게임방', ['🎮┃같이해요', '게임 파티 1']),
        ('＋ 새로운 방', ['➕┃우리방만들기', '＋ 통화방 만들기']), ('﹒자리비움', ['💤┃자리비움', '잠수방'])]),
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
            CREATE TABLE IF NOT EXISTS membership_requests (
                user INTEGER PRIMARY KEY, requested_at TEXT NOT NULL, state TEXT NOT NULL,
                last_notice_at INTEGER NOT NULL DEFAULT 0);
            ''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(membership_requests)')}
            if 'last_notice_at' not in columns:
                db.execute('ALTER TABLE membership_requests ADD COLUMN last_notice_at INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            with db:
                yield db
        finally:
            db.close()

    def request_membership(self, user):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM membership_requests WHERE user=?', (user,)).fetchone()
            if row and row[0] == 'pending':
                return False
            db.execute('INSERT OR REPLACE INTO membership_requests (user,requested_at,state) VALUES (?,?,?)',
                       (user, datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), 'pending'))
            return True

    def reserve_membership_notice(self, user, now=None):
        now = int(time.time()) if now is None else int(now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT requested_at,state,last_notice_at FROM membership_requests WHERE user=?', (user,)).fetchone()
            if row and row[2] and now - row[2] < 600:
                return False, min(600, row[2] + 600 - now), False
            requested = row[0] if row and row[1] == 'pending' else datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
            repeat = bool(row and row[1] == 'pending' and row[2])
            db.execute('INSERT INTO membership_requests (user,requested_at,state,last_notice_at) VALUES (?,?,?,?) '
                       'ON CONFLICT(user) DO UPDATE SET requested_at=excluded.requested_at,state=excluded.state,last_notice_at=excluded.last_notice_at',
                       (user, requested, 'pending', now))
            return True, 0, repeat

    def membership_state(self, user, state):
        with self.connect() as db:
            db.execute('UPDATE membership_requests SET state=? WHERE user=?', (state, user))

    def pending_members(self):
        with self.connect() as db:
            return db.execute("SELECT user,requested_at FROM membership_requests WHERE state='pending' ORDER BY requested_at LIMIT 25").fetchall()

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
