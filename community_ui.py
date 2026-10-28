import asyncio
import json
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord.ext import tasks
from core import GUILD_ID,PROTECTED_CHANNEL_IDS
from membership import CLAN_ROLE_ID,view_overwrites
from channel_policy import readonly_overwrites
from private_screen import render
from titles_ui import available,role_safe,ShopPanel
from titles_store import rows
from community_store import (initialise_community,object_row,create_party,party_members,join_party,
    create_poll,vote,counts,close_item)
from collection_rewards import claim_completed
from items_store import initialise_items

LOCK=asyncio.Lock()
GAMES=('발로란트','롤','워프레임','배그','오버워치','메이플스토리')
OWNER_ID=1119230372174123169
LOG=logging.getLogger('seolon')
KST=ZoneInfo('Asia/Seoul')


def is_staff(i):return i.user.id==OWNER_ID or i.permissions.administrator


class MemberView(discord.ui.View):
    def __init__(self,store,user=None,timeout=300):
        super().__init__(timeout=timeout);self.store,self.user=store,user
    async def interaction_check(self,i):
        if self.user is not None and self.user!=i.user.id:return False
        try:available(i)
        except ValueError as exc:
            await render(i,content=str(exc));return False
        return True
    async def on_error(self,i,error,item):
        LOG.exception('커뮤니티 버튼 실패',exc_info=error)
        await render(i,content=str(error)[:500] if isinstance(error,ValueError) else '처리하지 못했어요. 잠시 후 다시 시도해 주세요.',view=Hub(self.store,i.user.id))


class Hub(MemberView):
    def __init__(self,store,user=None):super().__init__(store,user,timeout=None if user is None else 300)
    @discord.ui.button(label='내 프로필',custom_id='seolon:hub:profile:v1')
    async def profile(self,i,b):await show_profile(i,self.store)
    @discord.ui.button(label='칭호 도감',custom_id='seolon:hub:codex:v1')
    async def codex(self,i,b):await show_codex(i,self.store)
    @discord.ui.button(label='게임 역할',custom_id='seolon:hub:games:v1')
    async def games(self,i,b):await show_games(i,self.store)
    @discord.ui.button(label='내 음성방',custom_id='seolon:hub:voice:v1')
    async def voice(self,i,b):
        from voice_rooms import RoomMenu
        await render(i,content='생성 입장방에 들어가 개인 음성방을 만든 뒤 여기서 관리하세요.',view=RoomMenu(self.store,i.user.id))
    @discord.ui.button(label='상점',custom_id='seolon:hub:shop:v1')
    async def shop(self,i,b):
        from items_ui import StoreMenu
        await render(i,content='칭호 또는 아이템을 선택하세요.',view=StoreMenu(self.store,i.user.id))


async def show_profile(i,store):
    member=await i.guild.fetch_member(i.user.id)
    balance,days=store.profile(GUILD_ID,i.user.id)
    streak,today=store.attendance_status(GUILD_ID,i.user.id)
    with store.connect() as db:
        colour=db.execute('SELECT colour FROM profile_styles WHERE user=?',(i.user.id,)).fetchone()
        worn=db.execute('SELECT t.name FROM titles t JOIN title_equipped e ON e.title=t.id WHERE e.guild=? AND e.user=?',(GUILD_ID,i.user.id)).fetchone()
        completed=db.execute('SELECT count(*) FROM collection_claims WHERE guild=? AND user=?',(GUILD_ID,i.user.id)).fetchone()[0]
    from collection_rewards import automatic_hidden_ids
    hidden_ids=automatic_hidden_ids(store,GUILD_ID,i.user.id)
    hidden_names=[r['name'] for r in rows(store,GUILD_ID,i.user.id) if r['id'] in hidden_ids]
    embed=discord.Embed(title=member.display_name,colour=colour[0] if colour else 0xB8C8D8)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name='눈송이',value=f'{balance:,}개')
    embed.add_field(name='출석',value=f'누적 {days}일 · 연속 {streak}일')
    embed.add_field(name='오늘 출석',value='완료' if today else '미완료')
    shown=([worn[0]] if worn else [])+hidden_names
    embed.add_field(name='착용 칭호',value=' · '.join(dict.fromkeys(shown)) if shown else '없음')
    embed.add_field(name='보유 칭호',value=f'{len(rows(store,GUILD_ID,i.user.id))}개 · 자동 히든 {completed}개')
    embed.add_field(name='서버 가입',value=f'<t:{int(member.joined_at.timestamp())}:D>' if member.joined_at else '확인할 수 없음')
    await render(i,embed=embed,view=Hub(store,i.user.id))


async def show_codex(i,store,series=None):
    earned=claim_completed(store,GUILD_ID,i.user.id)
    from collection_rewards import completion_state,sync_hidden_roles
    await sync_hidden_roles(i.guild,store,{i.user.id})
    catalog=rows(store,GUILD_ID);owned={r['id'] for r in rows(store,GUILD_ID,i.user.id)}
    series_names=sorted({r['series'] for r in catalog if not r['hidden'] and 1<=r['tier']<=4})
    with store.connect() as db:state=completion_state(db,GUILD_ID,i.user.id)
    if not series_names:
        await render(i,content='아직 등록된 도감 세트가 없어요.',view=Hub(store,i.user.id));return
    name=series if series in series_names else series_names[0]
    titles=[r for r in catalog if r['series']==name and not r['hidden'] and 1<=r['tier']<=4]
    titles.sort(key=lambda r:r['tier']);ids={r['id'] for r in titles}
    lines=[f"{r['tier']}단계 · {r['name']} · {'보유' if r['id'] in owned else '미보유'}" for r in titles]
    reward_names=[r['name'] for r in catalog if r['id'] in state['rewards']]
    embed=discord.Embed(title=name+' · 칭호 도감',description='\n'.join(lines),colour=0xC59A3C)
    embed.add_field(name='현재 세트',value=f'{len(set(ids)&owned)}/{len(ids)}')
    embed.add_field(name='전체 구매형',value=f"{len(state['purchasable']&owned)}/{len(state['purchasable'])}")
    embed.add_field(name='한정 · 구매 불가',value='조건 충족' if state['grant_only']&owned else '최소 1개 필요')
    reward_text=' · '.join(reward_names) if state['eligible'] else '???'
    embed.add_field(name='통합 히든',value=(reward_text+' · 자동 장착 중') if state['eligible'] else '구매 가능한 칭호 전체와 한정 칭호 1개 이상을 모으면 자동 장착돼요.',inline=False)
    if earned:embed.set_footer(text='히든 칭호를 획득해 자동 장착했어요.')
    view=Hub(store,i.user.id)
    select=discord.ui.Select(placeholder='도감 세트 선택',options=[discord.SelectOption(label=s,value=s,default=s==name) for s in series_names[:25]])
    async def pick(j):await show_codex(j,store,select.values[0])
    select.callback=pick;view.add_item(select)
    await render(i,embed=embed,view=view)


async def show_games(i,store):
    member=await i.guild.fetch_member(i.user.id)
    with store.connect() as db:games=db.execute('SELECT name,role FROM game_roles ORDER BY rowid').fetchall()
    view=Hub(store,i.user.id)
    select=discord.ui.Select(placeholder='모집 알림을 받을 게임 선택 · 다시 선택하면 변경',min_values=0,max_values=len(games),
        options=[discord.SelectOption(label=name,value=str(rid),default=any(r.id==rid for r in member.roles)) for name,rid in games])
    async def choose(j):
        await j.response.defer()
        async with LOCK:
            available(j)
            who=await j.guild.fetch_member(j.user.id)
            ids={rid for _,rid in games};wanted={int(v) for v in select.values}
            if not wanted<=ids:raise ValueError('잘못된 게임 역할입니다.')
            current={r.id for r in who.roles if r.id in ids}
            registered={rid:j.guild.get_role(rid) for rid in ids}
            for role in registered.values():role_safe(j.guild,role)
            changed=[]
            try:
                for rid in current-wanted:await who.remove_roles(registered[rid],reason='회원이 게임 알림 구독 해제');changed.append((rid,False))
                for rid in wanted-current:await who.add_roles(registered[rid],reason='회원이 게임 알림 구독');changed.append((rid,True))
            except Exception:
                for rid,added in reversed(changed):
                    try:
                        if added:await who.remove_roles(registered[rid],reason='게임 역할 변경 실패 복구')
                        else:await who.add_roles(registered[rid],reason='게임 역할 변경 실패 복구')
                    except discord.HTTPException:LOG.exception('게임 역할 복구 실패')
                raise
        await render(j,content='게임 역할을 저장했어요. 선택한 게임의 모집 알림을 받을 수 있어요.',view=Hub(store,j.user.id))
    select.callback=choose;view.add_item(select)
    await render(i,content='원하는 게임을 골라 주세요. 게임 역할은 채널 접근 권한을 바꾸지 않아요.',view=view)


def party_embed(store,row):
    members=party_members(store,row['id'])
    status='모집 중' if row['status']=='open' and int(time.time())<row['starts'] else '마감'
    embed=discord.Embed(title=f"{row['game']} · 파티 모집",description=row['description'] or '함께할 클랜원을 모집해요.',colour=0x82AFD6)
    embed.add_field(name='시작',value=f"<t:{row['starts']}:f> · <t:{row['starts']}:R>")
    embed.add_field(name='참여',value=f"{len(members)}/{row['capacity']}명 · {status}")
    embed.add_field(name='모집자',value=f"<@{row['owner']}>")
    embed.add_field(name='참여자',value=' · '.join(f'<@{u}>' for u in members) or '없음',inline=False)
    return embed


def poll_embed(store,row):
    totals=counts(store,row['id']);options=json.loads(row['options'])
    lines=[f'{name} · {totals.get(n,0)}표' for n,name in enumerate(options)]
    embed=discord.Embed(title=row['title'],description='\n'.join(lines),colour=0xB49AD9)
    embed.add_field(name='마감',value=f"<t:{row['ends']}:f>" if row['status']=='open' else '마감 완료')
    embed.set_footer(text='1인 1표 · 마감 전 선택 변경 가능')
    return embed


class PartyModal(discord.ui.Modal,title='파티 모집'):
    game=discord.ui.TextInput(label='게임',placeholder='발로란트 / 롤 / 워프레임 / 배그 / 오버워치 / 메이플스토리 / 기타',max_length=20)
    when=discord.ui.TextInput(label='시작 시간 · 한국 시간',placeholder='2026-10-28 21:00 또는 지금',max_length=16)
    capacity=discord.ui.TextInput(label='모집자 포함 인원',placeholder='2~20',max_length=2)
    description=discord.ui.TextInput(label='모집 내용',style=discord.TextStyle.paragraph,max_length=500,required=False)
    def __init__(self,store):super().__init__();self.store=store
    async def on_submit(self,i):
        try:
            available(i)
            game=self.game.value.strip()
            if game not in GAMES+('기타',):raise ValueError('안내된 게임 이름 또는 기타를 입력하세요.')
            try:
                starts=int(time.time())+60 if self.when.value.strip()=='지금' else int(datetime.strptime(self.when.value.strip(),'%Y-%m-%d %H:%M').replace(tzinfo=KST).timestamp())
                capacity=int(self.capacity.value)
            except ValueError:raise ValueError('시간은 YYYY-MM-DD HH:MM 또는 지금, 인원은 숫자로 입력하세요.')
            await render(i,content='파티 모집을 등록하는 중이에요.')
            async with LOCK:
                available(i)
                pid=create_party(self.store,i.user.id,game,starts,capacity,self.description.value)
                channel=i.guild.get_channel(self.store.get('text:┊파티모집') or 0)
                if not isinstance(channel,discord.TextChannel):raise ValueError('파티 모집 채널을 찾을 수 없습니다.')
                with self.store.connect() as db:
                    db.row_factory=__import__('sqlite3').Row
                    row=dict(db.execute('SELECT * FROM parties WHERE id=?',(pid,)).fetchone())
                    roleid=db.execute('SELECT role FROM game_roles WHERE name=?',(game,)).fetchone()
                row['status']='open'
                role=i.guild.get_role(roleid[0]) if roleid else None
                message=await channel.send(content=role.mention if role else None,embed=party_embed(self.store,row),view=PartyView(self.store),
                    allowed_mentions=discord.AllowedMentions(everyone=False,users=False,roles=[role] if role else [],replied_user=False))
                with self.store.connect() as db:db.execute("UPDATE parties SET channel=?,message=?,status='open' WHERE id=?",(channel.id,message.id,pid))
                await render(i,content=f'파티 모집을 등록했어요: {message.jump_url}',view=Hub(self.store,i.user.id))
        except Exception as exc:
            if 'pid' in locals():
                with self.store.connect() as db:db.execute("UPDATE parties SET status='failed' WHERE id=? AND status='draft'",(pid,))
            await render(i,content=str(exc)[:500],view=Hub(self.store,i.user.id))


class PartyPanel(MemberView):
    def __init__(self,store):super().__init__(store,timeout=None)
    @discord.ui.button(label='파티 모집하기',custom_id='seolon:party:create:v1',style=discord.ButtonStyle.primary)
    async def create(self,i,b):await i.response.send_modal(PartyModal(self.store))


class PartyView(MemberView):
    def __init__(self,store,closed=False):
        super().__init__(store,timeout=None)
        for child in self.children:child.disabled=closed
    async def update(self,i,action):
        await i.response.defer()
        async with LOCK:
            row=object_row(self.store,'parties',i.message.id)
            if row is None or row['channel']!=i.channel_id:raise ValueError('등록된 모집 메시지가 아닙니다.')
            if action=='close':close_item(self.store,'parties',row['id'],i.user.id,is_staff(i));row['status']='closed'
            else:join_party(self.store,row['id'],i.user.id,leave=action=='leave')
            await i.message.edit(embed=party_embed(self.store,row),view=PartyView(self.store,row['status']!='open'),allowed_mentions=discord.AllowedMentions.none())
    @discord.ui.button(label='참여',custom_id='seolon:party:join:v1',style=discord.ButtonStyle.primary)
    async def join(self,i,b):await self.update(i,'join')
    @discord.ui.button(label='참여 취소',custom_id='seolon:party:leave:v1')
    async def leave(self,i,b):await self.update(i,'leave')
    @discord.ui.button(label='모집 마감',custom_id='seolon:party:close:v1')
    async def close(self,i,b):await self.update(i,'close')


class PollModal(discord.ui.Modal,title='일정 투표'):
    question=discord.ui.TextInput(label='제목',placeholder='이번 주 같이 게임할 날짜',max_length=80)
    options=discord.ui.TextInput(label='선택지 · 한 줄에 하나씩',placeholder='토요일 20시\n일요일 21시',style=discord.TextStyle.paragraph,max_length=350)
    hours=discord.ui.TextInput(label='몇 시간 뒤 마감할까요?',placeholder='1~168',default='24',max_length=3)
    def __init__(self,store):super().__init__();self.store=store
    async def on_submit(self,i):
        try:
            available(i)
            options=[v.strip() for v in self.options.value.splitlines() if v.strip()]
            if any(len(v)>40 for v in options):raise ValueError('선택지는 각각 40자 이내로 입력하세요.')
            if not self.hours.value.isdigit():raise ValueError('마감 시간은 숫자로 입력하세요.')
            await render(i,content='투표를 등록하는 중이에요.')
            async with LOCK:
                available(i)
                pid=create_poll(self.store,i.user.id,self.question.value,options,int(self.hours.value))
                channel=i.guild.get_channel(self.store.get('community:poll-channel') or 0)
                with self.store.connect() as db:
                    db.row_factory=__import__('sqlite3').Row
                    row=dict(db.execute('SELECT * FROM polls WHERE id=?',(pid,)).fetchone())
                row['status']='open'
                message=await channel.send(embed=poll_embed(self.store,row),view=PollView(self.store,row),allowed_mentions=discord.AllowedMentions.none())
                with self.store.connect() as db:db.execute("UPDATE polls SET channel=?,message=?,status='open' WHERE id=?",(channel.id,message.id,pid))
                await render(i,content=f'투표를 등록했어요: {message.jump_url}',view=Hub(self.store,i.user.id))
        except Exception as exc:
            if 'pid' in locals():
                with self.store.connect() as db:db.execute("UPDATE polls SET status='failed' WHERE id=? AND status='draft'",(pid,))
            await render(i,content=str(exc)[:500],view=Hub(self.store,i.user.id))


class PollPanel(MemberView):
    def __init__(self,store):super().__init__(store,timeout=None)
    @discord.ui.button(label='일정 투표 만들기',custom_id='seolon:poll:create:v1',style=discord.ButtonStyle.primary)
    async def create(self,i,b):await i.response.send_modal(PollModal(self.store))


class PollView(MemberView):
    def __init__(self,store,row=None,closed=False):
        super().__init__(store,timeout=None)
        options=json.loads(row['options']) if row else ['선택']
        select=discord.ui.Select(custom_id='seolon:poll:vote:v1',placeholder='투표 · 다시 선택하면 변경',disabled=closed,
            options=[discord.SelectOption(label=name,value=str(n)) for n,name in enumerate(options)])
        async def pick(i):
            await i.response.defer()
            async with LOCK:
                row=object_row(store,'polls',i.message.id)
                if row is None or row['channel']!=i.channel_id:raise ValueError('등록된 투표 메시지가 아닙니다.')
                vote(store,row['id'],i.user.id,int(i.data['values'][0]))
                await i.message.edit(embed=poll_embed(store,row),view=PollView(store,row),allowed_mentions=discord.AllowedMentions.none())
        select.callback=pick;self.add_item(select)
        for child in self.children:child.disabled=closed
    @discord.ui.button(label='투표 마감',custom_id='seolon:poll:close:v1')
    async def close(self,i,b):
        await i.response.defer()
        async with LOCK:
            row=object_row(self.store,'polls',i.message.id)
            if row is None or row['channel']!=i.channel_id:raise ValueError('등록된 투표가 아닙니다.')
            close_item(self.store,'polls',row['id'],i.user.id,is_staff(i));row['status']='closed'
            await i.message.edit(embed=poll_embed(self.store,row),view=PollView(self.store,row,True),allowed_mentions=discord.AllowedMentions.none())


async def setup_community(guild,store,logger):
    initialise_community(store);initialise_items(store)
    owner=await guild.fetch_member(OWNER_ID);clan=guild.get_role(CLAN_ROLE_ID)
    for name in GAMES:
        with store.connect() as db:row=db.execute('SELECT role FROM game_roles WHERE name=?',(name,)).fetchone()
        if row:
            role_safe(guild,guild.get_role(row[0]));continue
        all_roles=await guild.fetch_roles()
        role=next((r for r in all_roles if r.id==store.get('games:pending:'+name) and r.name=='게임 · '+name),None)
        if role is None:
            if any(r.name=='게임 · '+name for r in all_roles):raise ValueError('동일 이름의 기존 게임 역할이 있어 자동 중복 생성을 중단합니다.')
            role=await guild.create_role(name='게임 · '+name,permissions=discord.Permissions.none(),mentionable=False,hoist=False,reason='소유자 요청: 선택형 게임 모집 알림 역할')
            store.put('games:pending:'+name,role.id)
        role_safe(guild,role)
        with store.connect() as db:db.execute('INSERT INTO game_roles VALUES (?,?)',(name,role.id))
        store.put('games:pending:'+name,0)
    channels=await guild.fetch_channels()
    poll_channel=next((c for c in channels if c.id==store.get('community:poll-channel')),None)
    if poll_channel is None:
        category=guild.get_channel(store.get('category:── ⟡ 함께하는 순간 ⟡ ──') or 0)
        if not isinstance(category,discord.CategoryChannel):raise ValueError('모임 카테고리를 찾을 수 없습니다.')
        permissions=view_overwrites({},guild.default_role,clan,owner,guild.me,'main')
        permissions=readonly_overwrites(permissions,guild.default_role,clan,owner,guild.me)
        poll_channel=await guild.create_text_channel('┊일정',category=category,overwrites=permissions,reason='소유자 요청: 일정 투표 채널')
        store.put('community:poll-channel',poll_channel.id);store.put('entry:gated:'+str(poll_channel.id),poll_channel.id)
    panels=[('community:hub','text:┊봇명령어','커뮤니티 메뉴','내 프로필·도감·게임 역할·음성방을 한 화면에서 관리하세요.',Hub(store)),
            ('community:games','text:┊역할선택','게임 역할','관심 게임을 선택하면 해당 게임 모집 알림을 받을 수 있어요.',Hub(store)),
            ('community:party-panel','text:┊파티모집','파티 모집','게임·시간·인원을 정하고 버튼으로 참여하세요. 게임 알림은 최초 모집 때 한 번만 전송돼요.',PartyPanel(store))]
    for key,channel_key,title,desc,view in panels:
        if store.get(key):continue
        channel=guild.get_channel(store.get(channel_key) or 0)
        if not isinstance(channel,discord.TextChannel) or channel.id in PROTECTED_CHANNEL_IDS:raise ValueError('메뉴를 둘 채널을 확인할 수 없습니다.')
        message=await channel.send(embed=discord.Embed(title=title,description=desc,colour=0xB8C8D8),view=view)
        store.put(key,message.id)
    if not store.get('community:poll-panel'):
        message=await poll_channel.send(embed=discord.Embed(title='일정 투표',description='선택지를 등록하고 함께할 시간을 정하세요. 한 사람 한 표이며 마감 전 변경할 수 있어요. 봇 휴식 중에도 마감 시간은 유지되고 재접속 후 결과를 반영해요.',colour=0xB8C8D8),view=PollPanel(store))
        store.put('community:poll-panel',message.id)
    from role_layout import organise_roles
    await organise_roles(guild,store,logger)
    trigger=guild.get_channel(store.get('voice:＋ 새로운 방') or 0)
    if not isinstance(trigger,discord.VoiceChannel):raise ValueError('개인 음성방 생성용 입장방을 확인할 수 없습니다.')
    logger.warning('개인 음성방 기능 활성 · 생성 입장방 %s · 본인 방 이름/인원/잠금 관리 · 생성한 빈 방만 정리',trigger.id)
    logger.warning('커뮤니티 메뉴 준비 완료 · 게임 역할6종/파티모집/일정투표/내프로필/칭호도감 · 일정채널 %s',poll_channel.id)


class CommunityWorker:
    def __init__(self,client,store):self.client,self.store=client,store
    @tasks.loop(minutes=1)
    async def run(self):
        guild=self.client.get_guild(GUILD_ID)
        if guild is None or not self.store.get('community:poll-channel'):return
        from voice_rooms import cleanup_empty_rooms
        try:await cleanup_empty_rooms(guild,self.store)
        except Exception:LOG.exception('개인 음성방 정리 재시도 대기')
        for table,deadline in [('parties','starts'),('polls','ends')]:
            try:
                with self.store.connect() as db:
                    db.row_factory=__import__('sqlite3').Row
                    due=[dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE status='open' AND {deadline}<=?",(int(time.time()),))]
                for row in due:
                    async with LOCK:
                        channel=guild.get_channel(row['channel'])
                        if channel is None:continue
                        try:
                            message=await channel.fetch_message(row['message'])
                            row['status']='closed'
                            await message.edit(embed=party_embed(self.store,row) if table=='parties' else poll_embed(self.store,row),
                                view=PartyView(self.store,True) if table=='parties' else PollView(self.store,row,True))
                        except discord.NotFound:pass
                        with self.store.connect() as db:db.execute(f"UPDATE {table} SET status='closed' WHERE id=?",(row['id'],))
            except Exception:LOG.exception('모집·투표 자동 마감 실패 · 다음 주기에 재시도')
