import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from discord import app_commands
from core import GUILD_ID, PROTECTED_CHANNEL_IDS
from role_layout import organise_roles
from private_screen import render
from membership import CLAN_ROLE_ID
from operations_store import record
from titles_store import initialise_titles, rows, get_title, create_title, configure_sale, purchase, grant, revoke, equipped, KINDS

TITLE_LOCK=asyncio.Lock()
OWNER_ID=1119230372174123169
LOG=logging.getLogger('seolon')
TITLE_NAMES={'첫눈':'설중매향','눈길':'설월청휘','설온의 시작':'설온지연'}
TITLE_MEANINGS={'설중매향':('雪中梅香','눈 속에 피어난 매화의 향기'),'설월청휘':('雪月淸輝','눈과 달이 빚어낸 맑은 빛'),'설온지연':('雪溫之緣','설온에서 이어진 인연')}
SERIES_NAMES={'겨울':'설월의 시','기념':'인연의 서약'}
PALETTES={'설월의 시':(0xBFD9EF,0x82AFD6,0x4B7DB8,0x315294),
          '인연의 서약':(0xD7C8EE,0xB49AD9,0x8B69BE,0x654694)}
DEFAULT_PALETTE=(0xB9DFD4,0x7EC4AF,0x439C86,0x236B5A)
TITLE_MEANINGS.update({'월하설영':('月下雪影','달 아래 드리운 눈의 그림자'),
 '현야빙화':('玄夜氷華','깊은 밤에 피어난 얼음꽃'),
 '성상지약':('星霜之約','오랜 세월을 함께하겠다는 약속'),
 '연휘지증':('緣輝之證','빛나는 인연의 증표'),
 '영설지인':('永雪之印','오래도록 남을 설온의 인장')})


def tier_colour(series,tier):
    if not 1<=tier<=4:raise ValueError('단계는 1~4입니다.')
    return PALETTES.get(series,DEFAULT_PALETTE)[tier-1]

ADMIN_COMMANDS={'칭호생성','칭호판매설정','칭호판매중지','칭호수정','칭호지급','칭호회수'}


def admin(i):
    if getattr(i.client,'closing_for_backup',False) is True:
        raise app_commands.CheckFailure('종료 전 백업 중입니다. 내일 오전 7시 이후 이용하세요.')
    if i.guild_id!=GUILD_ID or not (i.user.id==OWNER_ID or i.permissions.administrator):
        raise app_commands.CheckFailure('서버 관리자만 칭호를 관리할 수 있습니다.')


def role_safe(guild, role):
    if role is None or role.managed or role.permissions.value!=0 or role>=guild.me.top_role:
        raise ValueError('칭호 역할의 권한·순서를 확인하세요. 관리 권한 없이 설온지기 아래에 있어야 합니다.')
    for c in guild.channels:
        if c.overwrites_for(role).pair()[0].value:
            raise ValueError('이 칭호 역할에 채널 권한이 추가되어 사용을 중단했습니다.')


def available(i):
    if i.guild_id!=GUILD_ID or i.user.bot or not any(r.id==CLAN_ROLE_ID for r in i.user.roles):
        raise ValueError('클랜원만 사용할 수 있어요.')
    if getattr(i.client,'closing_for_backup',False) is True:
        raise ValueError('종료 전 백업 중이에요. 내일 오전 7시 이후 이용해 주세요.')


async def wear(i,store,tid):
    member=await i.guild.fetch_member(i.user.id)
    registered=rows(store,GUILD_ID)
    owned={r['id'] for r in rows(store,GUILD_ID,member.id)}
    if tid is not None and tid not in owned: raise ValueError('보유하지 않은 칭호예요.')
    selected=get_title(store,GUILD_ID,tid) if tid is not None else None
    new=i.guild.get_role(selected['role']) if selected else None
    if selected: role_safe(i.guild,new)
    ids={r['role'] for r in registered}
    previous=[r for r in member.roles if r.id in ids]
    for role in previous: role_safe(i.guild,role)
    removed=[]
    try:
        for role in previous:
            if new is None or role.id!=new.id:
                await member.remove_roles(role,reason='보유 칭호 변경 · 장식 역할만 변경')
                removed.append(role)
        if new is not None and new not in member.roles:
            await member.add_roles(new,reason='보유 칭호 착용')
        fresh=await i.guild.fetch_member(member.id)
        actual={r.id for r in fresh.roles if r.id in ids}
        if actual!=({new.id} if new else set()): raise ValueError('칭호 역할 적용 확인 실패')
        equipped(store,GUILD_ID,member.id,tid)
    except Exception:
        try:
            if new is not None and all(r.id!=new.id for r in previous):
                await member.remove_roles(new,reason='칭호 변경 실패 복구')
            for role in removed: await member.add_roles(role,reason='칭호 변경 실패 복구')
        except Exception:
            LOG.exception('칭호 역할 복구 실패 · 관리자 확인 필요')
        raise


class ShopPanel(discord.ui.View):
    def __init__(self,store):
        super().__init__(timeout=None)
        self.store=store

    async def interaction_check(self,i):
        try: available(i)
        except ValueError as exc:
            await render(i,content=str(exc),view=MenuHome(self.store,i.user.id))
            return False
        return True

    @discord.ui.button(label='칭호 상점',style=discord.ButtonStyle.primary,custom_id='seolon:titles:shop:v1')
    async def shop(self,i,button):
        choices=[r for r in rows(self.store,GUILD_ID) if not r['hidden']]
        await show_series(i,self.store,choices,'shop')

    @discord.ui.button(label='내 칭호',style=discord.ButtonStyle.secondary,custom_id='seolon:titles:owned:v1')
    async def owned(self,i,button):
        await show_series(i,self.store,rows(self.store,GUILD_ID,i.user.id),'owned')

    @discord.ui.button(label='칭호 해제',style=discord.ButtonStyle.secondary,custom_id='seolon:titles:clear:v1')
    async def clear(self,i,button):
        await render(i,content='칭호를 해제하는 중이에요.',view=None)
        async with TITLE_LOCK:
            available(i)
            await wear(i,self.store,None)
        await render(i,content='칭호를 해제했어요. 보유 칭호는 그대로 남아 있어요.',view=MenuHome(self.store,i.user.id))

    async def on_error(self,i,error,item):
        LOG.exception('칭호 버튼 처리 실패',exc_info=error)
        text=str(error)[:500] if isinstance(error,ValueError) else '처리하지 못했어요. 역할 권한을 확인해 주세요.'
        await render(i,content=text,view=MenuHome(self.store,i.user.id))


def member_choices(store,user,mode):
    return rows(store,GUILD_ID,user) if mode=='owned' else [r for r in rows(store,GUILD_ID) if not r['hidden']]


def add_home_button(view,store,user):
    button=discord.ui.Button(label='메뉴')
    async def home(i):
        balance,_=store.profile(GUILD_ID,user)
        await render(i,content=f'칭호 메뉴 · 보유 눈송이 {balance:,}개',view=MenuHome(store,user))
    button.callback=home;view.add_item(button)


class MenuHome(ShopPanel):
    def __init__(self,store,user):
        super().__init__(store)
        self.user=user
        self.timeout=300

    async def interaction_check(self,i):
        if i.user.id!=self.user:return False
        return await super().interaction_check(i)


async def show_series(i,store,choices,mode):
    if not choices:
        await show_picker(i,store,choices,mode)
        return
    await render(i,content='먼저 칭호 세트를 선택해 주세요.',view=SeriesPicker(store,choices,mode,i.user.id))


class SeriesPicker(discord.ui.View):
    def __init__(self,store,choices,mode,user,page=0):
        super().__init__(timeout=300)
        self.user=user
        self.store=store
        series=sorted({r['series'] for r in choices})
        select=discord.ui.Select(placeholder='칭호 세트 선택',options=[discord.SelectOption(label=name,value=name) for name in series[page*25:(page+1)*25]])
        async def select_series(i):
            await show_picker(i,store,[r for r in choices if r['series']==select.values[0]],mode)
        select.callback=select_series;self.add_item(select)
        add_home_button(self,store,user)
        if len(series)>25:
            for label,offset in [('이전',-1),('다음',1)]:
                button=discord.ui.Button(label=label,disabled=not 0<=page+offset<((len(series)+24)//25))
                async def turn(i,offset=offset):
                    await i.response.edit_message(view=SeriesPicker(store,choices,mode,user,page+offset))
                button.callback=turn;self.add_item(button)

    async def interaction_check(self,i):
        if i.user.id!=self.user:return False
        available(i);return True

    async def on_error(self,i,error,item):
        await ShopPanel.on_error(self,i,error,item)


class TitlePicker(discord.ui.View):
    def __init__(self,store,choices,mode,user,page=0):
        super().__init__(timeout=300)
        self.store,self.choices,self.mode,self.user,self.page=store,choices,mode,user,page
        selected=choices[page*25:(page+1)*25]
        select=discord.ui.Select(placeholder='칭호를 선택하세요',options=[discord.SelectOption(label=r['name'][:100],value=str(r['id']),
            description=(r['series']+f" · {r['tier']}단계 · "+KINDS[r['kind']] + (f" · {r['price']:,}눈송이" if r['kind']!='grant' else ' · 구매 불가'))[:100]) for r in selected])
        async def pick(i):
            row=get_title(store,GUILD_ID,int(select.values[0]))
            if row is None: raise ValueError('칭호가 없습니다.')
            await render(i,embed=detail_embed(row),view=TitleAction(store,row['id'],user,mode))
        select.callback=pick
        self.add_item(select)
        back=discord.ui.Button(label='세트 목록')
        async def go_back(i):
            await show_series(i,store,member_choices(store,user,mode),mode)
        back.callback=go_back;self.add_item(back)
        add_home_button(self,store,user)
        if len(choices)>25:
            for label,offset in [('이전',-1),('다음',1)]:
                b=discord.ui.Button(label=label,disabled=not 0<=page+offset<((len(choices)+24)//25))
                async def turn(i,offset=offset):
                    await i.response.edit_message(view=TitlePicker(store,choices,mode,user,page+offset))
                b.callback=turn;self.add_item(b)

    async def interaction_check(self,i):
        if i.user.id!=self.user: return False
        available(i);return True

    async def on_error(self,i,error,item):
        await ShopPanel.on_error(self,i,error,item)


class TitleAction(TitlePicker):
    def __init__(self,store,tid,user,mode):
        discord.ui.View.__init__(self,timeout=300)
        self.store,self.tid,self.user=store,tid,user
        buy=discord.ui.Button(label='구매하기' if mode=='shop' else '착용하기',style=discord.ButtonStyle.primary)
        async def act(i):
            await i.response.defer()
            async with TITLE_LOCK:
                available(i)
                row=get_title(store,GUILD_ID,tid)
                if row is None: raise ValueError('칭호가 없습니다.')
                role_safe(i.guild,i.guild.get_role(row['role']))
                if mode=='shop':
                    amount=purchase(store,GUILD_ID,i.user.id,tid)
                    balance,_=store.profile(GUILD_ID,i.user.id)
                    text=f"{row['name']} 구매 완료 · 눈송이 -{amount:,}개\\n남은 눈송이 {balance:,}개\\n‘내 칭호’에서 착용할 수 있어요."
                else:
                    await wear(i,store,tid)
                    text=f"{row['name']} 착용 완료"
            await render(i,content=text.replace('\\n','\n'),view=MenuHome(store,user))
        buy.callback=act
        row=get_title(store,GUILD_ID,tid)
        owned=any(r['id']==tid for r in rows(store,GUILD_ID,user))
        if mode=='shop' and row and (row['kind']=='grant' or not row['selling'] or owned):
            buy.disabled=True
            buy.label='이미 보유 중' if owned else '구매 불가'
        self.add_item(buy)
        back=discord.ui.Button(label='칭호 목록')
        async def go_back(i):
            fresh=get_title(store,GUILD_ID,tid)
            choices=member_choices(store,user,mode)
            await show_picker(i,store,[r for r in choices if fresh and r['series']==fresh['series']],mode)
        back.callback=go_back;self.add_item(back)
        add_home_button(self,store,user)


def detail_embed(row):
    import time
    desc='세트: '+row['series']+f" · {row.get('tier',1)}단계\n"+KINDS[row['kind']]
    if row['kind']=='grant': desc+=' · 관리자 지급으로만 획득'
    else:
        desc+=f"\n가격: {row['price']:,}눈송이"
        if not row['selling']: desc+='\n판매 중지'
        if row['ends_at']: desc+=f"\n판매 종료: <t:{row['ends_at']}:f>"
        if row['stock'] is not None: desc+=f"\n남은 판매 수량: {max(0,row['stock']-row['sold'])}개"
    meaning=TITLE_MEANINGS.get(row['name'])
    if meaning: desc=f'{meaning[0]} · {meaning[1]}\n\n'+desc
    return discord.Embed(title=row['name'],description=desc,colour=row.get('colour') or 0xB8C8D8)


async def show_picker(i,store,choices,mode):
    if not choices:
        await render(i,content='아직 보유한 칭호가 없어요.' if mode=='owned' else '등록된 칭호가 없어요.',view=MenuHome(store,i.user.id))
        return
    await render(i,content='칭호를 선택해 주세요. 여러 개를 보유하고 하나만 착용할 수 있어요.',
                 view=TitlePicker(store,choices,mode,i.user.id))


async def new_role_title(guild,store,name,kind,price,hidden,actor,series='기타',tier=1):
    series=series.strip()
    colour=tier_colour(series,tier)
    if not 1<=len(series)<=20 or any(x in series for x in ['@','\n','\r']): raise ValueError('세트 이름은 멘션·줄바꿈 없이 1~20자입니다.')
    same_series=[r for r in rows(store,GUILD_ID) if r['series']==series]
    if len(same_series)>=4:raise ValueError('한 세트는 4개까지 등록합니다. 새 세트를 만들어 주세요.')
    if any(r['tier']==tier for r in same_series):raise ValueError('이 세트의 해당 단계는 이미 있습니다. 빈 단계를 선택하세요.')
    name=name.strip()
    if not 1<=len(name)<=40 or any(x in name for x in ['@','\n','\r']):
        raise ValueError('이름은 멘션·줄바꿈 없이 1~40자로 입력하세요.')
    if kind not in KINDS or not 0<=price<=1_000_000: raise ValueError('종류와 가격을 확인하세요.')
    if kind=='sale' and price<1: raise ValueError('상시 판매 가격은 1눈송이 이상이어야 합니다.')
    if any(r['name']==name for r in rows(store,GUILD_ID)): raise ValueError('동일한 이름의 칭호가 있습니다.')
    # A stable prefixed name lets startup recover a role created just before a crash.
    role_name='⟡ '+name
    roles=await guild.fetch_roles()
    existing=[r for r in roles if r.name==role_name]
    if existing and (len(existing)!=1 or existing[0].id!=store.get('titles:pending-role')):
        raise ValueError('같은 이름의 역할이 이미 있습니다. 다른 이름을 사용하세요.')
    role=existing[0] if existing else await guild.create_role(name=role_name,permissions=discord.Permissions.none(),hoist=False,mentionable=False,colour=discord.Colour(colour),
                                 reason='관리자가 요청한 장식 전용 칭호 생성')
    store.put('titles:pending-role',role.id)
    role_safe(guild,role)
    tid=create_title(store,GUILD_ID,name,role.id,kind,price,hidden,actor,series)
    with store.connect() as db:db.execute('UPDATE titles SET tier=?,colour=? WHERE id=?',(tier,colour,tid))
    store.put('titles:pending-role',0)
    await organise_roles(guild,store,LOG)
    return tid,role


async def refresh_default_title_names(guild,store,logger):
    if store.get('titles:literary-names')==1:return
    roles={r.id:r for r in await guild.fetch_roles()}
    for old,new in TITLE_NAMES.items():
        title=next((r for r in rows(store,GUILD_ID) if r['name']==old),None)
        if title is None:continue
        if any(r['name']==new for r in rows(store,GUILD_ID)):
            raise ValueError('새 기본 칭호 이름이 중복되어 이름 변경을 중단합니다.')
        role=roles.get(title['role'])
        role_safe(guild,role)
        await role.edit(name='⟡ '+new,reason='소유자 요청: 기존 칭호를 네 글자 한자어 스타일로 변경')
        with store.connect() as db:
            db.execute('UPDATE titles SET name=? WHERE id=? AND guild=?',(new,title['id'],GUILD_ID))
        record(store,'기본 칭호 이름 변경',OWNER_ID,role.id,{'이전':old,'이름':new})
        logger.warning('칭호 이름 변경 완료: %s → %s · 역할 ID %s · 가격/보유/세트 유지',old,new,role.id)
    store.put('titles:literary-names',1)


async def setup_title_progression(guild,store,logger):
    if store.get('titles:four-tier-style')==1:return
    roles={r.id:r for r in await guild.fetch_roles()}
    for old,new in SERIES_NAMES.items():
        separator_id=store.get('roles:series:'+old)
        if separator_id:
            separator=roles.get(separator_id)
            if separator:
                role_safe(guild,separator)
                await separator.edit(name='─ '+new+' ─',reason='소유자 요청: 칭호 세트 이름 변경')
            store.put('roles:series:'+new,separator_id)
            with store.connect() as db:db.execute('DELETE FROM objects WHERE key=?',('roles:series:'+old,))
        with store.connect() as db:db.execute('UPDATE titles SET series=? WHERE guild=? AND series=?',(new,GUILD_ID,old))
    defaults=[('설중매향','sale',650,'설월의 시',1),('설월청휘','sale',1200,'설월의 시',2),
              ('월하설영','sale',2400,'설월의 시',3),('현야빙화','sale',4800,'설월의 시',4),
              ('설온지연','grant',0,'인연의 서약',1),('성상지약','grant',0,'인연의 서약',2),
              ('연휘지증','grant',0,'인연의 서약',3),('영설지인','grant',0,'인연의 서약',4)]
    for name,kind,price,series,tier in defaults:
        title=next((r for r in rows(store,GUILD_ID) if r['name']==name),None)
        if title is None:
            await new_role_title(guild,store,name,kind,price,False,OWNER_ID,series,tier)
            title=next(r for r in rows(store,GUILD_ID) if r['name']==name)
        role=next((r for r in await guild.fetch_roles() if r.id==title['role']),None)
        role_safe(guild,role)
        colour=tier_colour(series,tier)
        await role.edit(colour=discord.Colour(colour),reason='소유자 요청: 칭호 등급이 높을수록 진한 색으로 설정')
        with store.connect() as db:
            db.execute('UPDATE titles SET series=?,tier=?,colour=?,price=? WHERE id=?',
                       (series,tier,colour,price,title['id']))
        logger.warning('칭호 단계 설정 완료: %s · %s단계 · %s · 가격 %s · 색 #%06X',series,tier,name,price,colour)
    store.put('titles:four-tier-style',1)


async def setup_titles(guild,store,logger):
    initialise_titles(store)
    async with TITLE_LOCK:
        await refresh_default_title_names(guild,store,logger)
        if not store.get('titles:seeded'):
            for name,kind,price,series in [('설중매향','sale',650,'겨울'),('설월청휘','sale',1200,'겨울'),('설온지연','grant',0,'기념')]:
                if any(r['name']==name for r in rows(store,GUILD_ID)): continue
                await new_role_title(guild,store,name,kind,price,False,OWNER_ID,series,2 if name=='설월청휘' else 1)
            store.put('titles:seeded',1)
        await setup_title_progression(guild,store,logger)
        if not store.get('titles:panel'):
            cid=store.get('text:﹒눈꽃상점')
            channels=await guild.fetch_channels()
            channel=next((c for c in channels if c.id==cid and isinstance(c,discord.TextChannel)),None)
            if channel is None or channel.id in PROTECTED_CHANNEL_IDS: raise ValueError('칭호 상점 채널을 확인할 수 없습니다.')
            embed=discord.Embed(title='칭호 상점',colour=0xB8C8D8,description='눈송이로 칭호를 모아 보세요.\n여러 칭호를 보유하고, 한 번에 하나만 착용할 수 있어요.\n상시 판매 · 기간/수량 한정 판매 · 관리자 지급 전용\n판매가 끝나도 이미 얻은 칭호는 계속 사용할 수 있어요.\n상점과 보유 칭호는 아래 버튼으로 확인하세요.')
            m=await channel.send(embed=embed,view=ShopPanel(store))
            store.put('titles:panel',m.id)
    await organise_roles(guild,store,logger)
    verified_roles={r.id:r for r in await guild.fetch_roles()}
    for title in rows(store,GUILD_ID):
        role_safe(guild,verified_roles.get(title['role']))
        if verified_roles[title['role']].colour.value != title['colour']:
            raise RuntimeError('칭호 색상 적용 확인 실패: '+title['name'])
    logger.warning('눈송이 칭호 상점 준비 완료 · 칭호 %s개 · 관리자 명령 생성/판매설정/수정/지급/회수 · 패널 %s',len(rows(store,GUILD_ID)),store.get('titles:panel'))


def register_commands(tree,store,scope):
    @tree.command(name='칭호생성',description='장식용 칭호 역할을 생성하고 등록합니다',guild=scope)
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(종류=[app_commands.Choice(name=v,value=k) for k,v in KINDS.items()])
    async def create(i:discord.Interaction,이름:str,종류:app_commands.Choice[str],가격:app_commands.Range[int,0,1000000]=0,숨김:bool=False,세트:str='기타',단계:app_commands.Range[int,1,4]=1):
        admin(i);await i.response.defer(ephemeral=True)
        async with TITLE_LOCK:
            admin(i)
            tid,role=await new_role_title(i.guild,store,이름,종류.value,가격,숨김,i.user.id,세트,단계)
        await i.followup.send(f'칭호 #{tid} · {role.name} 생성 완료. 한정 판매는 /칭호판매설정으로 조건을 지정해야 판매됩니다.',ephemeral=True)

    @tree.command(name='칭호판매설정',description='가격·종료일·총 판매 수량을 지정합니다',guild=scope)
    @app_commands.default_permissions(administrator=True)
    async def sale(i:discord.Interaction,칭호:discord.Role,가격:app_commands.Range[int,1,1000000],종료일:str='',수량:app_commands.Range[int,1,1000000]=None):
        admin(i)
        row=next((r for r in rows(store,GUILD_ID) if r['role']==칭호.id),None)
        if row is None: raise ValueError('봇으로 만든 칭호 역할을 선택하세요.')
        end=None
        if 종료일:
            try: end=int(datetime.strptime(종료일,'%Y-%m-%d %H:%M').replace(tzinfo=ZoneInfo('Asia/Seoul')).timestamp())
            except ValueError: raise ValueError('종료일은 한국 시간 YYYY-MM-DD HH:MM 형식입니다.')
        async with TITLE_LOCK: configure_sale(store,GUILD_ID,row['id'],가격,end,수량,i.user.id)
        await i.response.send_message('판매 설정 완료. 수량은 남은 개수가 아닌 누적 판매 한도입니다.',ephemeral=True)

    @tree.command(name='칭호판매중지',description='기존 보유는 유지하고 신규 판매를 중단합니다',guild=scope)
    @app_commands.default_permissions(administrator=True)
    async def stop(i:discord.Interaction,칭호:discord.Role):
        admin(i)
        with store.connect() as db:
            cur=db.execute('UPDATE titles SET selling=0 WHERE guild=? AND role=?',(GUILD_ID,칭호.id))
            if not cur.rowcount: raise ValueError('등록된 칭호가 아닙니다.')
        record(store,'칭호 판매 중지',i.user.id,칭호.id,{})
        await i.response.send_message('판매를 중지했어요. 기존 구매자의 보유·착용은 유지돼요.',ephemeral=True)

    @tree.command(name='칭호수정',description='칭호 이름과 숨김 여부를 수정합니다',guild=scope)
    @app_commands.default_permissions(administrator=True)
    async def edit(i:discord.Interaction,칭호:discord.Role,이름:str='',숨김:bool=False,세트:str='',단계:app_commands.Range[int,1,4]=None):
        admin(i);await i.response.defer(ephemeral=True)
        async with TITLE_LOCK:
            admin(i)
            row=next((r for r in rows(store,GUILD_ID) if r['role']==칭호.id),None)
            if row is None: raise ValueError('등록된 칭호가 아닙니다.')
            role_safe(i.guild,칭호)
            name=이름.strip() or row['name']
            if not 1<=len(name)<=40 or any(x in name for x in ['@','\n','\r']): raise ValueError('이름 형식을 확인하세요.')
            if any(r['name']==name and r['id']!=row['id'] for r in rows(store,GUILD_ID)): raise ValueError('중복된 이름입니다.')
            series=세트.strip() or row['series']
            if not 1<=len(series)<=20 or any(x in series for x in ['@','\n','\r']): raise ValueError('세트 이름 형식을 확인하세요.')
            tier=단계 or row['tier']
            others=[r for r in rows(store,GUILD_ID) if r['series']==series and r['id']!=row['id']]
            if len(others)>=4 or any(r['tier']==tier for r in others):raise ValueError('세트는 최대4개이며 단계가 겹칠 수 없습니다.')
            colour=tier_colour(series,tier)
            await 칭호.edit(name='⟡ '+name,colour=discord.Colour(colour),reason='관리자가 요청한 칭호 이름 변경')
            with store.connect() as db: db.execute('UPDATE titles SET name=?,hidden=?,series=?,tier=?,colour=? WHERE id=?',(name,int(숨김),series,tier,colour,row['id']))
            await organise_roles(i.guild,store,LOG)
            record(store,'칭호 수정',i.user.id,칭호.id,{'이름':name,'숨김':숨김})
        await i.followup.send('칭호를 수정했어요. 숨김 칭호는 상점에서만 숨기며 보유자는 내 칭호에서 확인해요.',ephemeral=True)

    @tree.command(name='칭호지급',description='회원의 보유 목록에 칭호를 지급합니다',guild=scope)
    @app_commands.default_permissions(administrator=True)
    async def give(i:discord.Interaction,대상:discord.Member,칭호:discord.Role):
        admin(i)
        if 대상.bot: raise ValueError('봇에는 칭호를 지급하지 않습니다.')
        row=next((r for r in rows(store,GUILD_ID) if r['role']==칭호.id),None)
        if row is None: raise ValueError('등록된 칭호 역할을 선택하세요.')
        role_safe(i.guild,칭호)
        async with TITLE_LOCK: result=grant(store,GUILD_ID,대상.id,row['id'],i.user.id)
        await i.response.send_message('보유 목록에 지급했어요. 받은 회원이 내 칭호에서 착용하면 돼요.' if result else '이미 보유한 칭호입니다.',ephemeral=True)

    @tree.command(name='칭호회수',description='지정한 칭호만 회수합니다. 눈송이는 자동 환불하지 않습니다',guild=scope)
    @app_commands.default_permissions(administrator=True)
    async def take(i:discord.Interaction,대상:discord.Member,칭호:discord.Role):
        admin(i);await i.response.defer(ephemeral=True)
        async with TITLE_LOCK:
            admin(i)
            row=next((r for r in rows(store,GUILD_ID) if r['role']==칭호.id),None)
            if row is None: raise ValueError('등록된 칭호 역할을 선택하세요.')
            role_safe(i.guild,칭호)
            member=await i.guild.fetch_member(대상.id)
            if 칭호 in member.roles: await member.remove_roles(칭호,reason='관리자 요청 칭호 회수')
            revoke(store,GUILD_ID,대상.id,row['id'],i.user.id)
        await i.followup.send('칭호를 회수했어요. 눈송이는 자동 환불하지 않습니다.',ephemeral=True)
