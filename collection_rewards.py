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


def completion_state(db,guild,user):
    owned={r[0] for r in db.execute('SELECT title FROM title_owned WHERE guild=? AND user=?',(guild,user))}
    purchasable={r[0] for r in db.execute(
        "SELECT id FROM titles WHERE guild=? AND hidden=0 AND kind IN ('sale','limited')",(guild,))}
    grant_only={r[0] for r in db.execute(
        "SELECT id FROM titles WHERE guild=? AND hidden=0 AND kind='grant'",(guild,))}
    rewards={r[0] for r in db.execute('SELECT reward FROM title_collections')}
    eligible=bool(purchasable) and purchasable<=owned and bool(grant_only&owned)
    return {'owned':owned,'purchasable':purchasable,'grant_only':grant_only,
            'rewards':rewards,'eligible':eligible}


def award_completed(db,guild,user):
    """Reconcile global hidden rewards in the caller's current transaction."""
    state=completion_state(db,guild,user)
    awarded=[]
    for reward in state['rewards']:
        if state['eligible']:
            inserted=db.execute('INSERT OR IGNORE INTO collection_claims VALUES (?,?,?)',(guild,user,reward)).rowcount
            db.execute('INSERT OR IGNORE INTO title_owned VALUES (?,?,?,?)',(guild,user,reward,'collection'))
            if inserted:
                event(db,'전체 칭호 조건 달성·히든 지급',user,user,{'보상':reward})
                awarded.append(reward)
        else:
            had=db.execute('SELECT 1 FROM title_owned WHERE guild=? AND user=? AND title=?',(guild,user,reward)).fetchone()
            db.execute('DELETE FROM collection_claims WHERE guild=? AND user=? AND reward=?',(guild,user,reward))
            db.execute('DELETE FROM title_equipped WHERE guild=? AND user=? AND title=?',(guild,user,reward))
            db.execute('DELETE FROM title_owned WHERE guild=? AND user=? AND title=?',(guild,user,reward))
            if had:event(db,'전체 칭호 조건 미달·히든 회수',user,user,{'보상':reward})
    return awarded


def claim_completed(store,guild,user):
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return award_completed(db,guild,user)


def automatic_hidden_ids(store,guild,user):
    with store.connect() as db:
        state=completion_state(db,guild,user)
        return state['rewards'] if state['eligible'] else set()


async def sync_hidden_roles(guild,store,user_ids=None):
    """Make Discord's automatic hidden roles match the transactionally stored state."""
    with store.connect() as db:
        if user_ids is None:
            user_ids={r[0] for r in db.execute('SELECT DISTINCT user FROM title_owned WHERE guild=?',(guild.id,))}
        else:user_ids=set(user_ids)
        reward_ids={x[0] for x in db.execute('SELECT reward FROM title_collections')}
    reward_rows={r['id']:r for r in rows(store,guild.id) if r['id'] in reward_ids}
    all_titles=rows(store,guild.id)
    reward_role_ids={r['role'] for r in reward_rows.values()}
    all_title_role_ids={r['role'] for r in all_titles}
    from titles_ui import role_safe
    for uid in user_ids:
        try:member=await guild.fetch_member(uid)
        except discord.NotFound:continue
        wanted_titles=automatic_hidden_ids(store,guild.id,uid)
        wanted_roles={reward_rows[tid]['role'] for tid in wanted_titles if tid in reward_rows}
        current={r.id for r in member.roles if r.id in reward_role_ids}
        add=[guild.get_role(rid) for rid in wanted_roles-current]
        remove=[guild.get_role(rid) for rid in current-wanted_roles]
        for role in add+remove:role_safe(guild,role)
        if remove:await member.remove_roles(*remove,reason='새 칭호 추가 또는 조건 미달로 자동 히든 회수')
        if add:await member.add_roles(*add,reason='전체 구매형 칭호와 지급 전용 칭호 보유로 자동 히든 장착')
        from role_layout import sync_member_title_separators
        remaining=({r.id for r in member.roles if r.id in all_title_role_ids}-set(current-wanted_roles))|wanted_roles
        await sync_member_title_separators(guild,store,member,remaining)


async def grant_owner_test_titles(guild,store,owner_id,logger):
    """One-time owner-only experiment; future titles are intentionally not auto-granted."""
    if store.get('titles:owner-test-all')==2:return
    catalog=rows(store,guild.id)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for title in catalog:
            db.execute('INSERT OR IGNORE INTO title_owned VALUES (?,?,?,?)',(guild.id,owner_id,title['id'],'owner-test'))
        award_completed(db,guild.id,owner_id)
    member=await guild.fetch_member(owner_id)
    roles=[]
    from titles_ui import role_safe
    for title in rows(store,guild.id):
        role=guild.get_role(title['role']);role_safe(guild,role);roles.append(role)
    missing=[r for r in roles if r not in member.roles]
    if missing:await member.add_roles(*missing,reason='소유자 요청: 현재 모든 칭호 역할 장착 실험')
    from role_layout import sync_member_title_separators
    await sync_member_title_separators(guild,store,member,{r.id for r in roles})
    store.put('titles:owner-test-all',2)
    logger.warning('소유자 칭호 실험 적용 · 현재 칭호 %s개와 큰 칭호/세트 구분선 부착 · 이후 추가 칭호는 자동 지급하지 않음',len(roles))


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
                    hoist=False,mentionable=False,reason='소유자 요청: 전체 칭호 조건 히든 역할 생성')
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
        logger.warning('히든 보상 등록: %s · 전체 구매형+지급전용1개 조건',series)
    with store.connect() as db:
        users={r[0] for r in db.execute('SELECT DISTINCT user FROM title_owned WHERE guild=?',(GUILD_ID,))}
        for user in users:award_completed(db,GUILD_ID,user)
    await sync_hidden_roles(guild,store,users)
