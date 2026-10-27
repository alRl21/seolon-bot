import json
import discord
from core import GUILD_ID
from operations_store import event
from titles_store import rows,create_title

REWARDS={'설월의 시':('설월무극','雪月無極','눈과 달의 경계 너머에 이른 자'),
         '인연의 서약':('연화불멸','緣華不滅','시들지 않는 인연의 꽃')}


def initialise_collections(store):
    with store.connect() as db:
        db.executescript('''CREATE TABLE IF NOT EXISTS title_collections(
          series TEXT PRIMARY KEY,requirements TEXT NOT NULL,reward INTEGER NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS collection_claims(
          guild INTEGER,user INTEGER,reward INTEGER,PRIMARY KEY(guild,user,reward));''')


def award_completed(db,guild,user):
    # Runs in the same transaction as purchase/grant; repeated claims never duplicate.
    owned={r[0] for r in db.execute('SELECT title FROM title_owned WHERE guild=? AND user=?',(guild,user))}
    awarded=[]
    for series,requirements,reward in db.execute('SELECT series,requirements,reward FROM title_collections').fetchall():
        required=set(json.loads(requirements))
        if len(required)!=4 or not required<=owned:continue
        inserted=db.execute('INSERT OR IGNORE INTO collection_claims VALUES (?,?,?)',(guild,user,reward)).rowcount
        if inserted:
            db.execute('INSERT OR IGNORE INTO title_owned VALUES (?,?,?,?)',(guild,user,reward,'collection'))
            event(db,'도감 완성·히든 칭호 지급',user,user,{'세트':series,'보상':reward})
            awarded.append(reward)
    return awarded


def claim_completed(store,guild,user):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return award_completed(db,guild,user)


async def ensure_collections(guild,store,logger):
    from titles_ui import role_safe,OWNER_ID,TITLE_MEANINGS
    TITLE_MEANINGS.update({name:(hanja,meaning) for name,hanja,meaning in REWARDS.values()})
    initialise_collections(store)
    catalog=rows(store,GUILD_ID)
    series_names=sorted({r['series'] for r in catalog if not r['hidden'] and 1<=r['tier']<=4})
    for series in series_names:
        required=[r for r in catalog if r['series']==series and not r['hidden'] and 1<=r['tier']<=4]
        if len(required)!=4 or len({r['tier'] for r in required})!=4:continue
        with store.connect() as db:
            existing=db.execute('SELECT reward FROM title_collections WHERE series=?',(series,)).fetchone()
        if existing:continue
        name=REWARDS.get(series,(series+'의 증표','',''))[0]
        existing_title=next((r for r in catalog if r['name']==name),None)
        if existing_title is None:
            all_roles=await guild.fetch_roles()
            role=next((r for r in all_roles if r.name=='⟡ '+name and r.id==store.get('collection:pending-role')),None)
            if role is None:
                if any(r.name=='⟡ '+name for r in all_roles):raise ValueError('도감 보상 이름과 같은 기존 역할이 있어 중단합니다.')
                role=await guild.create_role(name='⟡ '+name,permissions=discord.Permissions.none(),colour=discord.Colour(0xC59A3C),
                    hoist=False,mentionable=False,reason='소유자 요청: 세트 완성 전용 히든 칭호 생성')
                store.put('collection:pending-role',role.id)
            role_safe(guild,role)
            reward=create_title(store,GUILD_ID,name,role.id,'grant',0,True,OWNER_ID,series)
            with store.connect() as db:db.execute('UPDATE titles SET tier=5,colour=? WHERE id=?',(0xC59A3C,reward))
            store.put('collection:pending-role',0)
        else:
            if existing_title['kind']!='grant' or not existing_title['hidden'] or existing_title['tier']!=5:
                raise ValueError('기존 칭호와 도감 보상 이름이 충돌합니다.')
            reward=existing_title['id']
        with store.connect() as db:
            db.execute('INSERT INTO title_collections VALUES (?,?,?)',(series,json.dumps([r['id'] for r in required]),reward))
        logger.warning('도감 히든 보상 등록: %s · 4개 완성 보상 · 기존 보유 데이터 유지',series)
    # Catch up existing owners; no member enumeration or Discord role auto-equip.
    with store.connect() as db:
        users=db.execute('SELECT DISTINCT guild,user FROM title_owned').fetchall()
        for gid,user in users:award_completed(db,gid,user)
