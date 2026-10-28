import json
import time
from operations_store import event


def initialise_community(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS game_roles(name TEXT PRIMARY KEY,role INTEGER NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS profile_roles(
          kind TEXT NOT NULL,name TEXT NOT NULL,role INTEGER NOT NULL UNIQUE,position INTEGER NOT NULL,
          PRIMARY KEY(kind,name));
        CREATE TABLE IF NOT EXISTS parties(id INTEGER PRIMARY KEY AUTOINCREMENT,owner INTEGER NOT NULL,
          game TEXT NOT NULL,starts INTEGER NOT NULL,capacity INTEGER NOT NULL,description TEXT NOT NULL,
          channel INTEGER,message INTEGER,status TEXT NOT NULL DEFAULT 'draft',created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS party_members(party INTEGER,user INTEGER,PRIMARY KEY(party,user));
        CREATE TABLE IF NOT EXISTS polls(id INTEGER PRIMARY KEY AUTOINCREMENT,owner INTEGER NOT NULL,
          title TEXT NOT NULL,options TEXT NOT NULL,ends INTEGER NOT NULL,channel INTEGER,message INTEGER,
          status TEXT NOT NULL DEFAULT 'draft',created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS poll_votes(poll INTEGER,user INTEGER,choice INTEGER,PRIMARY KEY(poll,user));
        CREATE TABLE IF NOT EXISTS temp_voices(channel INTEGER PRIMARY KEY,owner INTEGER NOT NULL,category INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS parties_deadline ON parties(status,starts);
        CREATE INDEX IF NOT EXISTS polls_deadline ON polls(status,ends);
        CREATE INDEX IF NOT EXISTS parties_owner ON parties(owner,created);
        CREATE INDEX IF NOT EXISTS polls_owner ON polls(owner,created);
        CREATE INDEX IF NOT EXISTS rooms_owner ON temp_voices(owner);
        ''')


def object_row(store,table,message):
    if table not in {'parties','polls'}:raise ValueError('잘못된 항목')
    with store.connect() as db:
        db.row_factory=__import__('sqlite3').Row
        row=db.execute(f'SELECT * FROM {table} WHERE message=?',(message,)).fetchone()
        return dict(row) if row else None


def create_party(store,owner,game,starts,capacity,description,now=None):
    now=int(time.time()) if now is None else now
    if not 2<=capacity<=20 or not now<=starts<=now+30*86400:raise ValueError('인원은 2~20명, 시작은 지금부터 30일 이내로 정하세요.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        active=db.execute("SELECT count(*) FROM parties WHERE owner=? AND status IN ('draft','open')",(owner,)).fetchone()[0]
        last=db.execute('SELECT max(created) FROM parties WHERE owner=?',(owner,)).fetchone()[0]
        if active>=2 or (last is not None and now-last<600):raise ValueError('모집은 10분 간격, 진행 중 최대 2개까지 만들 수 있어요.')
        pid=db.execute('INSERT INTO parties(owner,game,starts,capacity,description,created) VALUES (?,?,?,?,?,?)',
                       (owner,game,starts,capacity,description,now)).lastrowid
        db.execute('INSERT INTO party_members VALUES (?,?)',(pid,owner))
        event(db,'파티 모집 생성',owner,owner,{'파티':pid,'게임':game})
        return pid


def party_members(store,pid):
    with store.connect() as db:return [r[0] for r in db.execute('SELECT user FROM party_members WHERE party=? ORDER BY rowid',(pid,))]


def join_party(store,pid,user,leave=False,now=None):
    now=int(time.time()) if now is None else now
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT owner,starts,capacity,status FROM parties WHERE id=?',(pid,)).fetchone()
        if row is None or row[3]!='open' or now>=row[1]:raise ValueError('모집이 마감됐어요.')
        if leave:
            if user==row[0]:raise ValueError('모집자는 나가기 대신 모집 마감을 사용하세요.')
            db.execute('DELETE FROM party_members WHERE party=? AND user=?',(pid,user));return
        if db.execute('SELECT 1 FROM party_members WHERE party=? AND user=?',(pid,user)).fetchone():return
        if db.execute('SELECT count(*) FROM party_members WHERE party=?',(pid,)).fetchone()[0]>=row[2]:raise ValueError('정원이 찼어요.')
        db.execute('INSERT INTO party_members VALUES (?,?)',(pid,user))


def create_poll(store,owner,title,options,hours,now=None):
    now=int(time.time()) if now is None else now
    if not 2<=len(options)<=8 or len(set(options))!=len(options) or not 1<=hours<=168:raise ValueError('중복 없는 선택지 2~8개와 마감 1~168시간을 입력하세요.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        last=db.execute('SELECT max(created) FROM polls WHERE owner=?',(owner,)).fetchone()[0]
        active=db.execute("SELECT count(*) FROM polls WHERE owner=? AND status IN ('draft','open')",(owner,)).fetchone()[0]
        if active>=3 or (last is not None and now-last<600):raise ValueError('투표는 10분 간격, 진행 중 최대 3개까지 만들 수 있어요.')
        return db.execute('INSERT INTO polls(owner,title,options,ends,created) VALUES (?,?,?,?,?)',
                          (owner,title,json.dumps(options,ensure_ascii=False),now+hours*3600,now)).lastrowid


def vote(store,pid,user,choice,now=None):
    now=int(time.time()) if now is None else now
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT options,ends,status FROM polls WHERE id=?',(pid,)).fetchone()
        if row is None or row[2]!='open' or now>=row[1]:raise ValueError('투표가 마감됐어요.')
        if not 0<=choice<len(json.loads(row[0])):raise ValueError('선택지가 잘못됐어요.')
        db.execute('INSERT OR REPLACE INTO poll_votes VALUES (?,?,?)',(pid,user,choice))


def counts(store,pid):
    with store.connect() as db:return dict(db.execute('SELECT choice,count(*) FROM poll_votes WHERE poll=? GROUP BY choice',(pid,)))


def close_item(store,table,pid,actor,is_admin=False):
    if table not in {'parties','polls'}:raise ValueError('잘못된 항목')
    with store.connect() as db:
        row=db.execute(f'SELECT owner FROM {table} WHERE id=?',(pid,)).fetchone()
        if row is None or (row[0]!=actor and not is_admin):raise ValueError('작성자나 관리자만 마감할 수 있어요.')
        db.execute(f"UPDATE {table} SET status='closed' WHERE id=?",(pid,))
        event(db,'모집·투표 마감',actor,row[0],{'종류':table,'번호':pid})
