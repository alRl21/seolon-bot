import asyncio
import io
import json
import logging
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks
from core import GUILD_ID, PROTECTED_CHANNEL_IDS
from ticket_archive import archive_ticket
from schedule import nightly_backup_due
from membership import CLAN_ROLE_ID
from channel_policy import readonly_overwrites
from operations_store import initialise, record, active_ticket, reserve_ticket, activate_ticket, close_ticket, backup_database

OWNER_ID = 1119230372174123169
LOG = logging.getLogger('seolon')
TICKET_LOCK = asyncio.Lock()


def staff(member):
    return member.id == OWNER_ID or member.guild_permissions.administrator


def private_permissions(guild, owner):
    return {guild.default_role: discord.PermissionOverwrite(view_channel=False),
            owner: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                  attach_files=True, embed_links=True)}


def require_private_channel(channel, guild):
    if channel.overwrites_for(guild.default_role).view_channel is not False:
        raise RuntimeError('관리자 채널의 비공개 권한이 변경되어 전송을 중단합니다.')
    for target, value in channel.overwrites.items():
        permitted = target.id in {OWNER_ID, guild.me.id} or (
            isinstance(target, discord.Role) and target.permissions.administrator)
        if value.view_channel is True and not permitted:
            raise RuntimeError('관리자 외 접근 허용이 있어 전송을 중단합니다.')


class CloseTicketView(discord.ui.View):
    def __init__(self, store):
        super().__init__(timeout=None)
        self.store = store

    @discord.ui.button(label='문의 종료', custom_id='seolon:ticket-close:v1', style=discord.ButtonStyle.secondary)
    async def close(self, interaction, button):
        if getattr(interaction.client, 'closing_for_backup', False) is True:
            await interaction.response.send_message('종료 전 백업 중이에요. 오전 7시 재시작 후 이용해 주세요.', ephemeral=True)
            return
        if interaction.guild_id != GUILD_ID or not staff(interaction.user):
            await interaction.response.send_message('관리자만 문의를 종료할 수 있어요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with TICKET_LOCK:
            with self.store.connect() as db:
                row = db.execute("SELECT id,user FROM tickets WHERE guild=? AND channel=? AND status='open'",
                                 (GUILD_ID,interaction.channel_id)).fetchone()
            if row is None:
                await interaction.followup.send('이미 종료했거나 문의 채널이 아니에요.', ephemeral=True)
                return
            if (interaction.channel_id in PROTECTED_CHANNEL_IDS or
                    interaction.channel.category_id in PROTECTED_CHANNEL_IDS or
                    interaction.channel.category_id != self.store.get('support:category') or
                    interaction.channel.topic != f'seolon-ticket:{row[0]}:{row[1]}'):
                await interaction.followup.send('등록된 문의 채널을 확인할 수 없어 종료하지 않았어요.', ephemeral=True)
                return
            destination = interaction.guild.get_channel(self.store.get('support:log') or 0)
            if destination is None:
                await interaction.followup.send('관리자 기록 채널을 찾지 못해 문의 채널을 유지했어요.', ephemeral=True)
                return
            original_overwrites = None
            try:
                require_private_channel(destination, interaction.guild)
                original_overwrites = interaction.channel.overwrites
                frozen = {target: discord.PermissionOverwrite.from_pair(*v.pair()) for target,v in original_overwrites.items()}
                for target,value in frozen.items():
                    if target.id != interaction.guild.me.id:
                        value.send_messages = value.send_messages_in_threads = False
                        value.create_public_threads = value.create_private_threads = False
                await interaction.channel.edit(overwrites=frozen, reason='문의 대화 기록 중 새 메시지 방지')
                await archive_ticket(interaction.channel, row[0], row[1], interaction.user.id, self.store, destination)
                # Recheck immediately before the only destructive operation.
                fresh = await interaction.guild.fetch_channel(interaction.channel_id)
                if (fresh.id in PROTECTED_CHANNEL_IDS or fresh.category_id != self.store.get('support:category') or
                        fresh.topic != f'seolon-ticket:{row[0]}:{row[1]}'):
                    raise ValueError('문의 채널 보호 검사 실패. 삭제하지 않았습니다.')
                await fresh.delete(reason=f'관리자 요청 문의 #{row[0]} 종료 · 대화/첨부파일 보관 완료')
                close_ticket(self.store, interaction.channel_id, interaction.user.id)
                await interaction.followup.send('대화와 첨부파일을 관리자 운영기록에 저장하고 문의 채널을 삭제했어요.', ephemeral=True)
            except Exception as exc:
                LOG.exception('문의 보관·종료 중단 · 원본 채널 보존')
                if original_overwrites is not None:
                    try:
                        await interaction.channel.edit(overwrites=original_overwrites, reason='문의 종료 중단 · 작성 권한 복원')
                    except discord.HTTPException:
                        LOG.exception('문의 작성 권한 복원 실패 또는 이미 삭제됨')
                await interaction.followup.send(f'종료 처리 중단: {str(exc)[:500]}\n기록 보관이나 삭제를 완료하지 못한 채널은 유지돼요.', ephemeral=True)



class TicketModal(discord.ui.Modal, title='비공개 문의'):
    subject = discord.ui.TextInput(label='제목', max_length=80)
    body = discord.ui.TextInput(label='문의 내용', style=discord.TextStyle.paragraph, max_length=1800,
                               placeholder='문제 상황을 적어 주세요. 비밀번호나 인증번호는 적지 마세요.')

    def __init__(self, store):
        super().__init__()
        self.store = store

    async def on_submit(self, interaction):
        if getattr(interaction.client, 'closing_for_backup', False) is True:
            await interaction.response.send_message('종료 전 백업 중이에요. 오전 7시 재시작 후 이용해 주세요.', ephemeral=True)
            return
        if interaction.guild_id != GUILD_ID or interaction.user.bot:
            return
        if not staff(interaction.user) and not any(r.id == CLAN_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message('클랜원만 문의할 수 있어요.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        async with TICKET_LOCK:
            previous = active_ticket(self.store, GUILD_ID, interaction.user.id)
            if previous:
                await interaction.followup.send(f'이미 열린 문의가 있어요: <#{previous[1]}>' if previous[1] else '문의 채널을 만드는 중이에요.', ephemeral=True)
                return
            with self.store.connect() as db:
                last = db.execute('SELECT created_at FROM tickets WHERE guild=? AND user=? ORDER BY id DESC LIMIT 1',
                                  (GUILD_ID,interaction.user.id)).fetchone()
            if last and (datetime.now(ZoneInfo('Asia/Seoul')) - datetime.fromisoformat(last[0])).total_seconds() < 600:
                await interaction.followup.send('새 문의는 이전 접수로부터 10분 뒤 만들 수 있어요.', ephemeral=True)
                return
            tid = reserve_ticket(self.store, GUILD_ID, interaction.user.id)
            channel = None
            try:
                guild = interaction.guild
                owner = await guild.fetch_member(OWNER_ID)
                category = guild.get_channel(self.store.get('support:category') or 0)
                if not isinstance(category, discord.CategoryChannel):
                    raise ValueError('문의 공간이 준비되지 않았습니다.')
                permissions = private_permissions(guild, owner)
                permissions[interaction.user] = discord.PermissionOverwrite(view_channel=True, send_messages=True,
                    read_message_history=True, attach_files=True, embed_links=True,
                    create_public_threads=False, create_private_threads=False)
                channel = await guild.create_text_channel(f'문의-{tid:04d}', category=category,
                    overwrites=permissions, topic=f'seolon-ticket:{tid}:{interaction.user.id}',
                    reason='회원이 요청한 비공개 문의 생성')
                activate_ticket(self.store, tid, channel.id)
                embed = discord.Embed(title=str(self.subject.value), description=str(self.body.value), colour=0xB8C8D8)
                embed.set_footer(text=f'문의 #{tid:04d} · 작성자 ID {interaction.user.id}')
                await channel.send(content=f'<@{interaction.user.id}> 문의가 접수됐어요. 관리자 답변을 기다려 주세요.',
                                   embed=embed, view=CloseTicketView(self.store), allowed_mentions=discord.AllowedMentions.none())
                alert = guild.get_channel(self.store.get('support:log') or 0)
                if alert:
                    await alert.send(f'비공개 문의 #{tid:04d} · 작성자 ID {interaction.user.id} · {channel.mention}',
                                     allowed_mentions=discord.AllowedMentions.none())
                await interaction.followup.send(f'문의 채널이 열렸어요: {channel.mention}\n본인과 관리자만 볼 수 있어요.', ephemeral=True)
            except Exception:
                if channel is None:
                    with self.store.connect() as db:
                        db.execute("UPDATE tickets SET status='failed' WHERE id=?",(tid,))
                LOG.exception('비공개 문의 생성 실패')
                await interaction.followup.send(f'문의 채널: {channel.mention}. 안내 전송에 문제가 생겼어요.' if channel else '문의 생성에 실패했어요. 잠시 후 다시 시도해 주세요.', ephemeral=True)


class TicketPanel(discord.ui.View):
    def __init__(self, store):
        super().__init__(timeout=None)
        self.store = store

    @discord.ui.button(label='문의하기', custom_id='seolon:ticket-open:v1', style=discord.ButtonStyle.primary)
    async def open(self, interaction, button):
        if getattr(interaction.client, 'closing_for_backup', False) is True:
            await interaction.response.send_message('종료 전 백업 중이에요. 오전 7시 재시작 후 이용해 주세요.', ephemeral=True)
            return
        if interaction.guild_id != GUILD_ID or interaction.user.bot:
            return
        if not staff(interaction.user) and not any(r.id == CLAN_ROLE_ID for r in interaction.user.roles):
            await interaction.response.send_message('클랜원만 문의할 수 있어요.', ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(self.store))


async def setup_operations(guild, store, logger):
    initialise(store)
    owner = await guild.fetch_member(OWNER_ID)
    channels = await guild.fetch_channels()
    async def ensure(key, name, category=None, permissions=None):
        found = next((c for c in channels if c.id == store.get(key)), None)
        if found is None:
            if category is None:
                found = await guild.create_category(name, overwrites=permissions)
            else:
                found = await guild.create_text_channel(name, category=category, overwrites=permissions)
            store.put(key, found.id)
        return found
    private = private_permissions(guild, owner)
    category = await ensure('support:category', '── ⟡ 문의 ⟡ ──', permissions=private)
    admin_category = guild.get_channel(store.get('admin:category') or 0)
    if not isinstance(admin_category, discord.CategoryChannel):
        raise ValueError('관리자 카테고리를 찾을 수 없습니다.')
    audit = await ensure('support:log', '﹒운영기록', admin_category, private)
    backup = await ensure('support:backup', '﹒백업', admin_category, private)
    utility = guild.get_channel(store.get('utility:category') or 0)
    if not isinstance(utility, discord.CategoryChannel):
        raise ValueError('봇 카테고리를 찾을 수 없습니다.')
    clan = guild.get_role(CLAN_ROLE_ID)
    panel_permissions = {**private, clan: discord.PermissionOverwrite(view_channel=True, read_message_history=True)}
    panel_permissions = readonly_overwrites(panel_permissions, guild.default_role, clan, owner, guild.me)
    panel = await ensure('support:panel-channel', '﹒문의', utility, panel_permissions)
    if store.get('support:panel-version') != 2:
        embed = discord.Embed(title='문의', colour=0xB8C8D8,
            description='운영진에게 문의하거나 문제를 신고할 수 있어요.\n버튼을 누르면 본인과 관리자만 보는 채널이 열려요.\n진행 중인 문의는 한 개, 새 문의는 10분 간격으로 만들 수 있어요.\n종료 시 대화·첨부파일을 관리자 기록에 보관하고 문의 채널은 삭제해요.')
        message = None
        if store.get('support:panel-message'):
            try:
                message = await panel.fetch_message(store.get('support:panel-message'))
            except discord.NotFound:
                pass
        if message:
            await message.edit(embed=embed, view=TicketPanel(store))
        else:
            message = await panel.send(embed=embed, view=TicketPanel(store))
            store.put('support:panel-message', message.id)
        store.put('support:panel-version', 2)
    # Recover interrupted reservations. An API-created channel carries a stable topic.
    with store.connect() as db:
        creating = db.execute("SELECT id,user FROM tickets WHERE status='creating'").fetchall()
    for tid, user in creating:
        match = next((c for c in channels if getattr(c,'topic',None) == f'seolon-ticket:{tid}:{user}'),None)
        if match:
            activate_ticket(store, tid, match.id)
            await match.send('문의 접수를 복구했어요. 관리자 답변을 기다려 주세요.', view=CloseTicketView(store))
        else:
            with store.connect() as db:
                db.execute("UPDATE tickets SET status='failed' WHERE id=?",(tid,))
    with store.connect() as db:
        interrupted = db.execute("SELECT t.channel,a.actor FROM tickets t JOIN ticket_archives a ON a.ticket=t.id WHERE t.status='open'").fetchall()
    live_ids = {c.id for c in channels}
    for channel_id, actor in interrupted:
        if channel_id not in live_ids:
            close_ticket(store, channel_id, actor)
    fresh = {c.id:c for c in await guild.fetch_channels()}
    for cid in [category.id,audit.id,backup.id]:
        if fresh[cid].overwrites_for(guild.default_role).view_channel is not False:
            raise RuntimeError('문의·운영기록·백업 비공개 검증 실패')
    logger.warning('문의 종료 시 대화 보관 후 삭제 · 자동 백업 KST 00:59 후 종료 · 문의 패널 %s', panel.id)


class OperationsWorker:
    def __init__(self, client, store, data):
        self.client, self.store, self.data = client, store, data

    @tasks.loop(minutes=1)
    async def run(self):
        guild = self.client.get_guild(GUILD_ID)
        if not guild or not self.store.get('support:backup'):
            return
        for action in [self.flush]:
            try:
                await action(guild)
            except Exception:
                LOG.exception('운영 기록·백업 처리 실패 · 다음 주기에 재시도')

    async def flush(self, guild):
        channel = guild.get_channel(self.store.get('support:log') or 0)
        if not channel:
            return
        require_private_channel(channel, guild)
        with self.store.connect() as db:
            rows = db.execute('SELECT id,created_at,kind,actor,subject,details FROM audit_events WHERE delivered=0 ORDER BY id LIMIT 10').fetchall()
        for eid, when, kind, actor, subject, details in rows:
            embed = discord.Embed(title=kind, description=f'기록 #{eid} · {when[:19]}\n처리자 ID {actor}\n대상 ID {subject}\n{details[:2000]}', colour=0xB8C8D8)
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            with self.store.connect() as db:
                db.execute('UPDATE audit_events SET delivered=1 WHERE id=?',(eid,))

    async def backup(self, guild, force=False):
        now = datetime.now(ZoneInfo('Asia/Seoul'))
        day = int(now.strftime('%Y%m%d'))
        if not force and (not nightly_backup_due(now) or self.store.get('backup:last-night') == day):
            return
        channel = guild.get_channel(self.store.get('support:backup') or 0)
        if channel is None or channel.overwrites_for(guild.default_role).view_channel is not False:
            raise RuntimeError('관리자 전용 백업 채널 확인 실패')
        require_private_channel(channel, guild)
        folder = self.data / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'seolon-{now:%Y%m%d-%H%M%S-%f}.sqlite3'
        await asyncio.to_thread(backup_database, self.store, path)
        # Keep 28 local snapshots; Discord copies are retained for manual recovery.
        for old in sorted(folder.glob('seolon-*.sqlite3'))[:-28]:
            old.unlink()
        structure = []
        for c in await guild.fetch_channels():
            structure.append({'id':c.id,'name':c.name,'type':str(c.type),'category_id':getattr(c,'category_id',None),
                'position':c.position,'overwrites':[{'id':t.id,'type':'role' if isinstance(t,discord.Role) else 'member',
                    'allow':v.pair()[0].value,'deny':v.pair()[1].value} for t,v in c.overwrites.items()]})
        files = [discord.File(path), discord.File(io.BytesIO(json.dumps(structure,ensure_ascii=False,indent=2).encode()),filename='structure.json')]
        await channel.send(f'{"수동 백업" if force else "종료 전 백업"} · {now:%Y-%m-%d %H:%M} KST\n출석·눈송이·문의 상태·운영 기록 DB + 채널 구조/권한\n종료된 문의의 대화·첨부파일은 관리자 운영기록의 보관 파일을 확인하세요.', files=files,
                           allowed_mentions=discord.AllowedMentions.none())
        if not force:
            self.store.put('backup:last-night', day)
        LOG.warning('데이터 자동 백업 완료 · SQLite 무결성 확인 · 관리자 전용 채널에 별도 사본 전송')
        return path
