import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo


def initialise(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS audit_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          kind TEXT NOT NULL, actor INTEGER NOT NULL, subject INTEGER NOT NULL,
          details TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS tickets (
          id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, user INTEGER NOT NULL,
          channel INTEGER, status TEXT NOT NULL, created_at TEXT NOT NULL,
          closed_at TEXT, closed_by INTEGER);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_ticket ON tickets(guild,user)
          WHERE status IN ('creating','open');
        ''')


def event(db, kind, actor, subject, details):
    db.execute('INSERT INTO audit_events(created_at,kind,actor,subject,details) VALUES (?,?,?,?,?)',
               (datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), kind, actor, subject,
                json.dumps(details, ensure_ascii=False)))


def record(store, kind, actor, subject, details):
    with store.connect() as db:
        event(db, kind, actor, subject, details)


def active_ticket(store, guild, user):
    with store.connect() as db:
        return db.execute("SELECT id,channel,status FROM tickets WHERE guild=? AND user=? AND status IN ('creating','open')", (guild,user)).fetchone()


def reserve_ticket(store, guild, user):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        cur = db.execute("INSERT INTO tickets(guild,user,status,created_at) VALUES (?,?,'creating',?)",
                         (guild,user,datetime.now(ZoneInfo('Asia/Seoul')).isoformat()))
        return cur.lastrowid


def activate_ticket(store, tid, channel):
    with store.connect() as db:
        db.execute("UPDATE tickets SET channel=?,status='open' WHERE id=?", (channel,tid))
        event(db, '문의 접수', 0, 0, {'문의번호':tid, '채널':channel})


def close_ticket(store, channel, actor):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT id,user FROM tickets WHERE channel=? AND status='open'",(channel,)).fetchone()
        if row is None:
            return False
        db.execute("UPDATE tickets SET status='closed',closed_at=?,closed_by=? WHERE channel=?",
                   (datetime.now(ZoneInfo('Asia/Seoul')).isoformat(), actor, channel))
        event(db,'문의 종료',actor,row[1],{'문의번호':row[0], '채널':channel})
        return True


def backup_database(store, path):
    # SQLite backup API includes committed WAL data without stopping the bot.
    with store.connect() as source:
        with sqlite3.connect(path) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('백업 무결성 검사 실패')
