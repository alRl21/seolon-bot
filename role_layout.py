import discord

LABELS={'titles':'～～～ ⟡ 칭호 ⟡ ～～～','basic':'── 기본 ──','staff':'── 관리자 ──','games':'── 게임 ──'}


def series_label(series):
    return '～～ 〈 '+series+' 〉 ～～'


def separator_safe(guild,role):
    if role is None or role.managed or role.permissions.value or role>=guild.me.top_role:
        raise ValueError('칭호 구분선은 권한 없이 설온지기 역할보다 아래에 있어야 합니다.')


async def sync_member_title_separators(guild,store,member,title_role_ids):
    """Show title/series dividers in member profiles without granting permissions."""
    with store.connect() as db:
        title_series={r[0]:r[1] for r in db.execute('SELECT role,series FROM titles WHERE guild=?',(guild.id,))}
        series_ids={r[0][len('roles:series:'):]:r[1] for r in db.execute("SELECT key,id FROM objects WHERE key LIKE 'roles:series:%'")}
    active={rid for rid in title_role_ids if rid in title_series}
    wanted=set()
    if active:
        header=store.get('roles:separator:titles')
        if header:wanted.add(header)
        wanted|={series_ids[s] for s in {title_series[rid] for rid in active} if s in series_ids}
    all_separators={store.get('roles:separator:titles')}|set(series_ids.values())
    all_separators.discard(None)
    current={r.id for r in member.roles if r.id in all_separators}
    add=[guild.get_role(rid) for rid in wanted-current]
    remove=[guild.get_role(rid) for rid in current-wanted]
    for role in add+remove:separator_safe(guild,role)
    if remove:await member.remove_roles(*remove,reason='착용 중인 칭호 세트에 맞춰 프로필 구분선 정리')
    if add:await member.add_roles(*add,reason='프로필에서도 칭호와 세트가 크게 구분되도록 장식 역할 부착')


def group_ids(roles,title_ids,separator_ids,bot_top,everyone_id,game_ids=frozenset()):
    groups={'titles':[],'basic':[],'staff':[],'games':[]}
    for role in sorted(roles,key=lambda r:(r.position,-r.id)):
        if role.id==everyone_id or role.id in separator_ids or role.managed or role>=bot_top:
            continue
        key='titles' if role.id in title_ids else ('staff' if any(getattr(role.permissions,p) for p in
            ['administrator','manage_guild','manage_roles','manage_channels','moderate_members','kick_members','ban_members']) else 'basic')
        if role.id in game_ids:key='games'
        groups[key].append(role.id)
    return groups


async def organise_roles(guild,store,logger):
    with store.connect() as db:
        title_rows=db.execute('SELECT role,series,tier,price FROM titles WHERE guild=?',(guild.id,)).fetchall()
        title_series={r[0]:r[1] for r in title_rows}
        title_order={r[0]:(r[2],r[3],r[0]) for r in title_rows}
        title_ids=set(title_series)
        game_ids={r[0] for r in db.execute('SELECT role FROM game_roles')} if db.execute("SELECT 1 FROM sqlite_master WHERE name='game_roles'").fetchone() else set()
        series_separators={r[0][len('roles:series:'):]:r[1] for r in db.execute("SELECT key,id FROM objects WHERE key LIKE 'roles:series:%'")}
    roles=await guild.fetch_roles()
    bot_ids={r.id for r in guild.me.roles}
    top=max(r for r in roles if r.id in bot_ids)
    separators={key:store.get('roles:separator:'+key) for key in LABELS}
    groups=group_ids(roles,title_ids,set(separators.values()) | set(series_separators.values()),top,guild.default_role.id,game_ids)
    for key,ids in groups.items():
        if not ids:continue
        separator=next((r for r in roles if r.id==separators[key]),None)
        if separator is None:
            if any(r.name==LABELS[key] for r in roles):
                raise ValueError('같은 이름의 역할 구분선이 있어 자동 중복 생성을 중단합니다.')
            separator=await guild.create_role(name=LABELS[key],permissions=discord.Permissions.none(),
                hoist=False,mentionable=False,reason='소유자 요청: 역할 목록 구분선 생성 · 누구에게도 지급하지 않음')
            store.put('roles:separator:'+key,separator.id)
            separators[key]=separator.id
        if separator.permissions.value or separator.managed:
            raise ValueError('역할 구분선 권한이 변경되어 정렬을 중단합니다.')
        if separator.name!=LABELS[key]:
            await separator.edit(name=LABELS[key],reason='소유자 요청: 역할 구분선을 어느 화면에서나 크게 표시')
    active_series=sorted({title_series[rid] for rid in groups['titles']})
    for series in active_series:
        separator=next((r for r in roles if r.id==series_separators.get(series)),None)
        if separator is None:
            separator=await guild.create_role(name=series_label(series),permissions=discord.Permissions.none(),
                hoist=False,mentionable=False,reason='소유자 요청: 칭호 세트 구분선 · 회원 미지급')
            store.put('roles:series:'+series,separator.id)
            series_separators[series]=separator.id
        if separator.permissions.value or separator.managed:
            raise ValueError('칭호 세트 구분선 권한이 변경되어 정렬을 중단합니다.')
        if separator.name!=series_label(series):
            await separator.edit(name=series_label(series),reason='소유자 요청: 프로필에서도 세트가 크게 구분되도록 표시')
    roles=await guild.fetch_roles()
    indexed={r.id:r for r in roles}
    top=max(r for r in roles if r.id in bot_ids)
    groups=group_ids(roles,title_ids,set(separators.values()) | set(series_separators.values()),top,guild.default_role.id,game_ids)
    desired=[]
    for key in ['titles','games','basic','staff']:
        if groups[key]:
            if key=='titles':
                for series in reversed(active_series):
                    desired.extend(sorted((rid for rid in groups[key] if title_series[rid]==series),key=lambda rid:title_order[rid],reverse=True))
                    desired.append(series_separators[series])
            else:
                desired.extend(groups[key])
            desired.append(separators[key])
    if any(indexed[rid]>=top for rid in desired):
        raise ValueError('봇보다 높은 역할은 이동할 수 없습니다.')
    current=[r.id for r in sorted(roles) if r.id in set(desired)]
    if current!=desired:
        await guild.edit_role_positions(positions={indexed[rid]:n for n,rid in enumerate(desired,1)},
            reason='소유자 요청: 관리자·기본·칭호 구역 정리 · 권한과 회원 배정 유지')
    after=await guild.fetch_roles()
    actual=[r.id for r in sorted(after) if r.id in set(desired)]
    if actual!=desired:raise RuntimeError('역할 구역 정렬 확인 실패')
    higher=[r.name for r in after if r.id not in bot_ids and not r.managed and r.id!=guild.default_role.id and r>=top]
    logger.warning('역할 구역 정리 완료 · 큰 칭호/세트 구분선 · 칭호 세트 %s · 장식 칭호 %s개 · 봇보다 높은 기존 역할 유지: %s',', '.join(active_series),len(title_ids),', '.join(higher) or '없음')
