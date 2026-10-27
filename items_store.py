from operations_store import event


def initialise_items(store):
    with store.connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS shop_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE,kind TEXT,value INTEGER,price INTEGER,enabled INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS item_bag(user INTEGER,item INTEGER,quantity INTEGER,PRIMARY KEY(user,item));
        CREATE TABLE IF NOT EXISTS profile_styles(user INTEGER PRIMARY KEY,colour INTEGER);
        ''')
        for name,kind,value,price in [('칭호 교환권','voucher',650,650),('프로필 · 자정','theme',0x334D80,300),('프로필 · 여명','theme',0x9F72B8,300)]:
            db.execute('INSERT OR IGNORE INTO shop_items(name,kind,value,price) VALUES (?,?,?,?)',(name,kind,value,price))


def item_rows(store,user=None):
    with store.connect() as db:
        db.row_factory=__import__('sqlite3').Row
        query='SELECT i.*,coalesce(b.quantity,0) quantity FROM shop_items i LEFT JOIN item_bag b ON b.item=i.id AND b.user=?'
        return [dict(r) for r in db.execute(query,(user or 0,)) if user is None or r['quantity']>0]


def buy_item(store,guild,user,item):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT kind,price,enabled,name FROM shop_items WHERE id=?',(item,)).fetchone()
        if row is None or not row[2]:raise ValueError('판매 중인 상품이 아닙니다.')
        quantity=db.execute('SELECT quantity FROM item_bag WHERE user=? AND item=?',(user,item)).fetchone()
        q=quantity[0] if quantity else 0
        if (row[0]=='theme' and q>0) or q>=99:raise ValueError('이미 보유했거나 보유 한도에 도달한 아이템이에요.')
        result=db.execute('UPDATE wallet SET balance=balance-? WHERE guild=? AND user=? AND balance>=?',(row[1],guild,user,row[1]))
        if not result.rowcount:raise ValueError('눈송이가 부족해요.')
        db.execute('INSERT INTO item_bag VALUES (?,?,1) ON CONFLICT(user,item) DO UPDATE SET quantity=quantity+1',(user,item))
        event(db,'아이템 구매·눈송이 차감',user,user,{'아이템':row[3],'차감':row[1]})
        return row[1]


def use_theme(store,user,item):
    with store.connect() as db:
        row=db.execute('SELECT i.kind,i.value,b.quantity FROM shop_items i JOIN item_bag b ON i.id=b.item WHERE i.id=? AND b.user=?',(item,user)).fetchone()
        if row is None or row[0]!='theme' or row[2]<1:raise ValueError('보유한 프로필 테마를 선택하세요.')
        db.execute('INSERT OR REPLACE INTO profile_styles VALUES (?,?)',(user,row[1]))
        event(db,'프로필 테마 적용',user,user,{'아이템':item})


def redeem_voucher(store,guild,user,item,title):
    from collection_rewards import award_completed
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        voucher=db.execute('SELECT i.kind,i.value,b.quantity FROM shop_items i JOIN item_bag b ON b.item=i.id AND b.user=? WHERE i.id=?',(user,item)).fetchone()
        target=db.execute('SELECT kind,price,hidden,selling,ends_at,stock,name FROM titles WHERE guild=? AND id=?',(guild,title)).fetchone()
        if voucher is None or voucher[0]!='voucher' or voucher[2]<1:raise ValueError('교환권이 없어요.')
        if (target is None or target[0]!='sale' or target[2] or not target[3] or target[4] is not None or target[5] is not None or not 0<target[1]<=voucher[1]):
            raise ValueError('교환권 금액 이내의 상시 판매 칭호만 교환할 수 있어요. 한정·지급 전용·히든은 제외합니다.')
        if db.execute('SELECT 1 FROM title_owned WHERE guild=? AND user=? AND title=?',(guild,user,title)).fetchone():raise ValueError('이미 가진 칭호예요. 교환권은 사용하지 않았어요.')
        db.execute('UPDATE item_bag SET quantity=quantity-1 WHERE user=? AND item=?',(user,item))
        db.execute('INSERT INTO title_owned VALUES (?,?,?,?)',(guild,user,title,'voucher'))
        db.execute('UPDATE titles SET sold=sold+1 WHERE id=?',(title,))
        event(db,'칭호 교환권 사용',user,user,{'아이템':item,'칭호':target[6]})
        award_completed(db,guild,user)
