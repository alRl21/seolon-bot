import time
from operations_store import event

KINDS = {'sale':'상시 판매', 'limited':'한정 판매', 'grant':'한정 · 구매 불가'}


def initialise_titles(store):
    with store.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS titles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, name TEXT NOT NULL,
            role INTEGER NOT NULL UNIQUE, kind TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0,
            ends_at INTEGER, stock INTEGER, sold INTEGER NOT NULL DEFAULT 0,
            hidden INTEGER NOT NULL DEFAULT 0, selling INTEGER NOT NULL DEFAULT 0,
            UNIQUE(guild,name));
        CREATE TABLE IF NOT EXISTS title_owned (
            guild INTEGER NOT NULL,user INTEGER NOT NULL,title INTEGER NOT NULL,source TEXT NOT NULL,
            PRIMARY KEY(guild,user,title));
        CREATE TABLE IF NOT EXISTS title_equipped (
            guild INTEGER NOT NULL,user INTEGER NOT NULL,title INTEGER NOT NULL,
            PRIMARY KEY(guild,user));
        ''')
        if 'series' not in {r[1] for r in db.execute('PRAGMA table_info(titles)')}:
            db.execute("ALTER TABLE titles ADD COLUMN series TEXT NOT NULL DEFAULT '기타'")
        columns={r[1] for r in db.execute('PRAGMA table_info(titles)')}
        if 'tier' not in columns: db.execute('ALTER TABLE titles ADD COLUMN tier INTEGER NOT NULL DEFAULT 1')
        if 'colour' not in columns: db.execute('ALTER TABLE titles ADD COLUMN colour INTEGER NOT NULL DEFAULT 0')
        db.executescript('CREATE TABLE IF NOT EXISTS title_collections(series TEXT PRIMARY KEY,requirements TEXT NOT NULL,reward INTEGER NOT NULL UNIQUE); CREATE TABLE IF NOT EXISTS collection_claims(guild INTEGER,user INTEGER,reward INTEGER,PRIMARY KEY(guild,user,reward));')


def rows(store, guild, user=None):
    with store.connect() as db:
        db.row_factory=__import__('sqlite3').Row
        if user is None:
            result=db.execute('SELECT * FROM titles WHERE guild=? ORDER BY id',(guild,)).fetchall()
        else:
            result=db.execute('SELECT t.* FROM titles t JOIN title_owned o ON o.title=t.id AND o.guild=t.guild WHERE o.guild=? AND o.user=? ORDER BY t.id',(guild,user)).fetchall()
        return [dict(r) for r in result]


def get_title(store, guild, tid):
    return next((r for r in rows(store,guild) if r['id']==tid),None)


def create_title(store,guild,name,role,kind,price,hidden,actor,series='기타'):
    if kind not in KINDS or not 0<=price<=1_000_000:
        raise ValueError('칭호 종류와 가격을 확인하세요.')
    with store.connect() as db:
        cur=db.execute('INSERT INTO titles(guild,name,role,kind,price,hidden,selling,series) VALUES (?,?,?,?,?,?,?,?)',
                       (guild,name,role,kind,price,int(hidden),int(kind=='sale' and price>0),series))
        event(db,'칭호 생성',actor,role,{'칭호':name,'종류':KINDS[kind]})
        return cur.lastrowid


def configure_sale(store,guild,tid,price,ends_at,stock,actor):
    if not 1<=price<=1_000_000 or (stock is not None and stock<1):
        raise ValueError('가격은 1~1,000,000, 수량은 1 이상이어야 합니다.')
    if ends_at is not None and ends_at<=int(time.time()):
        raise ValueError('종료일은 미래여야 합니다.')
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT kind,sold FROM titles WHERE guild=? AND id=?',(guild,tid)).fetchone()
        if row is None or row[0]=='grant':
            raise ValueError('지급 전용 칭호는 판매할 수 없습니다.')
        if row[0]=='limited' and ends_at is None and stock is None:
            raise ValueError('한정 판매는 종료일 또는 수량이 필요합니다.')
        if stock is not None and stock<row[1]:
            raise ValueError('이미 판매한 수량보다 적게 설정할 수 없습니다.')
        db.execute('UPDATE titles SET kind=?,price=?,ends_at=?,stock=?,selling=1 WHERE guild=? AND id=?',
                   ('limited' if ends_at is not None or stock is not None else row[0],price,ends_at,stock,guild,tid))
        event(db,'칭호 판매 설정',actor,tid,{'가격':price,'종료':ends_at,'수량':stock})


def purchase(store,guild,user,tid,now=None):
    now=int(time.time()) if now is None else now
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT kind,price,ends_at,stock,sold,selling,name FROM titles WHERE guild=? AND id=?',(guild,tid)).fetchone()
        if row is None: raise ValueError('칭호가 없습니다.')
        kind,price,end,stock,sold,selling,name=row
        if db.execute('SELECT 1 FROM title_owned WHERE guild=? AND user=? AND title=?',(guild,user,tid)).fetchone():
            raise ValueError('이미 보유한 칭호예요. 다시 결제하지 않았어요.')
        if kind=='grant' or not selling or price<=0:
            raise ValueError('구매할 수 없는 칭호예요.')
        if (end is not None and now>=end) or (stock is not None and sold>=stock):
            raise ValueError('판매가 종료됐거나 품절됐어요.')
        wallet=db.execute('SELECT balance FROM wallet WHERE guild=? AND user=?',(guild,user)).fetchone()
        if wallet is None or wallet[0]<price: raise ValueError('눈송이가 부족해요.')
        db.execute('UPDATE wallet SET balance=balance-? WHERE guild=? AND user=?',(price,guild,user))
        db.execute('INSERT INTO title_owned VALUES (?,?,?,?)',(guild,user,tid,'purchase'))
        db.execute('UPDATE titles SET sold=sold+1 WHERE id=?',(tid,))
        event(db,'칭호 구매·눈송이 차감',user,user,{'칭호':name,'차감':price})
        from collection_rewards import award_completed
        award_completed(db,guild,user)
        return price


def grant(store,guild,user,tid,actor):
    with store.connect() as db:
        if db.execute('SELECT 1 FROM titles WHERE guild=? AND id=?',(guild,tid)).fetchone() is None:
            raise ValueError('등록되지 않은 칭호입니다.')
        cur=db.execute('INSERT OR IGNORE INTO title_owned VALUES (?,?,?,?)',(guild,user,tid,'grant'))
        if cur.rowcount:
            event(db,'칭호 지급',actor,user,{'칭호번호':tid})
        result=bool(cur.rowcount)
        from collection_rewards import award_completed
        award_completed(db,guild,user)
        return result


def revoke(store,guild,user,tid,actor):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM title_equipped WHERE guild=? AND user=? AND title=?',(guild,user,tid))
        db.execute('DELETE FROM title_owned WHERE guild=? AND user=? AND title=?',(guild,user,tid))
        event(db,'칭호 회수',actor,user,{'칭호번호':tid})
        from collection_rewards import award_completed
        award_completed(db,guild,user)


def equipped(store,guild,user,tid):
    with store.connect() as db:
        if tid is None:
            db.execute('DELETE FROM title_equipped WHERE guild=? AND user=?',(guild,user))
        else:
            if not db.execute('SELECT 1 FROM title_owned WHERE guild=? AND user=? AND title=?',(guild,user,tid)).fetchone():
                raise ValueError('보유하지 않은 칭호입니다.')
            db.execute('INSERT OR REPLACE INTO title_equipped VALUES (?,?,?)',(guild,user,tid))
        event(db,'칭호 착용 변경',user,user,{'칭호번호':tid})
