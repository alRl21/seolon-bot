import asyncio
import logging
import discord
from core import GUILD_ID,PROTECTED_CHANNEL_IDS
from membership import CLAN_ROLE_ID
from community_ui import MemberView
from private_screen import render

VOICE_LOCK=asyncio.Lock()
LOG=logging.getLogger('seolon')


def room_row(store,cid):
    with store.connect() as db:return db.execute('SELECT owner,category FROM temp_voices WHERE channel=?',(cid,)).fetchone()


def editable_room(i,store):
    channel=i.user.voice.channel if i.user.voice else None
    row=room_row(store,channel.id) if channel else None
    if row is None or (row[0]!=i.user.id and not i.permissions.administrator):raise ValueError('본인이 만든 개인 음성방에 들어간 뒤 이용하세요.')
    if channel.id in PROTECTED_CHANNEL_IDS or channel.category_id!=row[1] or channel.category_id in PROTECTED_CHANNEL_IDS:
        raise ValueError('개인 음성방 보호 검사를 통과하지 못했습니다.')
    return channel


class RoomModal(discord.ui.Modal):
    def __init__(self,store,kind):
        super().__init__(title='방 이름 변경' if kind=='name' else '인원 제한')
        self.store,self.kind=store,kind
        self.value=discord.ui.TextInput(label='새 방 이름' if kind=='name' else '0은 제한 없음 · 1~99명',max_length=30)
        self.add_item(self.value)
    async def on_submit(self,i):
        try:
            from titles_ui import available
            available(i)
            await render(i,content='음성방 설정을 변경하는 중이에요.')
            async with VOICE_LOCK:
                channel=editable_room(i,self.store)
                if self.kind=='name':
                    name=self.value.value.strip()
                    if not name or any(x in name for x in ['@','\n','\r']):raise ValueError('사용할 수 없는 이름입니다.')
                    await channel.edit(name=name,reason='개인 음성방 소유자가 이름 변경')
                else:
                    limit=int(self.value.value)
                    if not 0<=limit<=99:raise ValueError('인원은 0~99로 입력하세요.')
                    await channel.edit(user_limit=limit,reason='개인 음성방 소유자가 인원 제한 변경')
            await render(i,content='음성방 설정을 변경했어요.',view=RoomMenu(self.store,i.user.id))
        except Exception as exc:await render(i,content=str(exc)[:500],view=RoomMenu(self.store,i.user.id))


class RoomMenu(MemberView):
    @discord.ui.button(label='이름 변경')
    async def name(self,i,b):editable_room(i,self.store);await i.response.send_modal(RoomModal(self.store,'name'))
    @discord.ui.button(label='인원 제한')
    async def limit(self,i,b):editable_room(i,self.store);await i.response.send_modal(RoomModal(self.store,'limit'))
    @discord.ui.button(label='잠금 / 해제')
    async def lock(self,i,b):
        await render(i,content='음성방 잠금 설정을 변경하는 중이에요.')
        async with VOICE_LOCK:
            channel=editable_room(i,self.store)
            clan=i.guild.get_role(CLAN_ROLE_ID)
            value=channel.overwrites_for(clan)
            locked=value.connect is not False
            value.connect=not locked
            await channel.set_permissions(clan,overwrite=value,reason='개인 음성방 소유자의 잠금 설정')
        await render(i,content='새 입장을 잠갔어요. 이미 들어온 회원은 그대로 있어요.' if locked else '음성방 잠금을 해제했어요.',view=RoomMenu(self.store,i.user.id))
    @discord.ui.button(label='메뉴')
    async def home(self,i,b):
        from community_ui import Hub
        await render(i,content='커뮤니티 메뉴',view=Hub(self.store,i.user.id))


async def cleanup_room(channel,store):
    row=room_row(store,channel.id)
    if row is None or any(not m.bot for m in channel.members):return
    if channel.id in PROTECTED_CHANNEL_IDS or channel.category_id in PROTECTED_CHANNEL_IDS or channel.category_id!=row[1]:return
    # Only IDs recorded immediately after this bot created a room may be deleted.
    try:await channel.delete(reason='소유자가 승인한 개인 음성방 자동 정리 · 참가자 없음')
    except discord.NotFound:pass
    with store.connect() as db:db.execute('DELETE FROM temp_voices WHERE channel=?',(channel.id,))
    LOG.info('비어 있는 개인 음성방 정리 완료: %s',channel.id)


async def voice_update(member,before,after,store,client=None):
    if member.guild.id!=GUILD_ID:return
    guild=member.guild
    async with VOICE_LOCK:
        if client is not None and getattr(client,'closing_for_backup',False):return
        if not member.bot and after.channel and after.channel.id==store.get('voice:＋ 새로운 방'):
            if not any(r.id==CLAN_ROLE_ID for r in member.roles):return
            with store.connect() as db:existing=db.execute('SELECT channel,category FROM temp_voices WHERE owner=? ORDER BY rowid DESC LIMIT 1',(member.id,)).fetchone()
            old=guild.get_channel(existing[0]) if existing else None
            if old and old.category_id==existing[1] and old.id not in PROTECTED_CHANNEL_IDS:
                await member.move_to(old,reason='기존 개인 음성방으로 이동')
            else:
                category=after.channel.category
                if not category or category.id in PROTECTED_CHANNEL_IDS:raise ValueError('개인 방 생성 카테고리를 확인할 수 없습니다.')
                if len(category.channels)>=45:raise ValueError('음성방이 많아 새 방 생성을 잠시 제한합니다.')
                owner=await guild.fetch_member(1119230372174123169)
                clan=guild.get_role(CLAN_ROLE_ID)
                permissions={guild.default_role:discord.PermissionOverwrite(view_channel=False),
                    clan:discord.PermissionOverwrite(view_channel=True,connect=True,speak=True,stream=True),
                    member:discord.PermissionOverwrite(view_channel=True,connect=True,speak=True),
                    owner:discord.PermissionOverwrite(view_channel=True,connect=True,speak=True),
                    guild.me:discord.PermissionOverwrite(view_channel=True,connect=True,speak=True)}
                for role in guild.roles:
                    if role.managed and getattr(role.tags,'bot_id',None)==1019061779357245521:
                        permissions[role]=discord.PermissionOverwrite(view_channel=True,connect=True,speak=True,send_messages=True,read_message_history=True)
                room=await guild.create_voice_channel(member.display_name[:25]+' · 음성',category=category,overwrites=permissions,
                                                     reason='소유자 요청: 회원이 생성 입장방에 들어와 개인 음성방 생성')
                with store.connect() as db:db.execute('INSERT INTO temp_voices VALUES (?,?,?)',(room.id,member.id,category.id))
                try:await member.move_to(room,reason='본인의 새 개인 음성방으로 이동')
                except Exception:
                    await cleanup_room(room,store)
                    raise
        if before.channel and (not after.channel or before.channel.id!=after.channel.id):await cleanup_room(before.channel,store)


async def cleanup_empty_rooms(guild,store):
    async with VOICE_LOCK:
        with store.connect() as db:ids=[r[0] for r in db.execute('SELECT channel FROM temp_voices')]
        for cid in ids:
            channel=guild.get_channel(cid)
            if channel is None:
                with store.connect() as db:db.execute('DELETE FROM temp_voices WHERE channel=?',(cid,))
            else:await cleanup_room(channel,store)
