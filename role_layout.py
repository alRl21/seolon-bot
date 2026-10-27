import discord

LABELS={'titles':'── 칭호 ──','basic':'── 기본 ──','staff':'── 관리자 ──'}


def group_ids(roles,title_ids,separator_ids,bot_top,everyone_id):
    groups={'titles':[],'basic':[],'staff':[]}
    for role in sorted(roles,key=lambda r:(r.position,-r.id)):
        if role.id==everyone_id or role.id in separator_ids or role.managed or role>=bot_top:
            continue
        key='titles' if role.id in title_ids else ('staff' if any(getattr(role.permissions,p) for p in
            ['administrator','manage_guild','manage_roles','manage_channels','moderate_members','kick_members','ban_members']) else 'basic')
        groups[key].append(role.id)
    return groups


async def organise_roles(guild,store,logger):
    with store.connect() as db:
        title_series={r[0]:r[1] for r in db.execute('SELECT role,series FROM titles WHERE guild=?',(guild.id,))}
        title_ids=set(title_series)
        series_separators={r[0][len('roles:series:'):]:r[1] for r in db.execute("SELECT key,id FROM objects WHERE key LIKE 'roles:series:%'")}
    roles=await guild.fetch_roles()
    bot_ids={r.id for r in guild.me.roles}
    top=max(r for r in roles if r.id in bot_ids)
    separators={key:store.get('roles:separator:'+key) for key in LABELS}
    groups=group_ids(roles,title_ids,set(separators.values()) | set(series_separators.values()),top,guild.default_role.id)
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
    active_series=sorted({title_series[rid] for rid in groups['titles']})
    for series in active_series:
        separator=next((r for r in roles if r.id==series_separators.get(series)),None)
        if separator is None:
            separator=await guild.create_role(name='─ '+series+' ─',permissions=discord.Permissions.none(),
                hoist=False,mentionable=False,reason='소유자 요청: 칭호 세트 구분선 · 회원 미지급')
            store.put('roles:series:'+series,separator.id)
            series_separators[series]=separator.id
        if separator.permissions.value or separator.managed:
            raise ValueError('칭호 세트 구분선 권한이 변경되어 정렬을 중단합니다.')
    roles=await guild.fetch_roles()
    indexed={r.id:r for r in roles}
    top=max(r for r in roles if r.id in bot_ids)
    groups=group_ids(roles,title_ids,set(separators.values()) | set(series_separators.values()),top,guild.default_role.id)
    desired=[]
    for key in ['titles','basic','staff']:
        if groups[key]:
            if key=='titles':
                for series in reversed(active_series):
                    desired.extend(rid for rid in groups[key] if title_series[rid]==series)
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
    logger.warning('역할 구역 정리 완료 · 관리자/기본/칭호 구분선 · 칭호 세트 %s · 장식 칭호 %s개 · 봇보다 높은 기존 역할 유지: %s',', '.join(active_series),len(title_ids),', '.join(higher) or '없음')
