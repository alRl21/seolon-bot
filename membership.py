import copy
import io
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace

import discord

from core import GUILD_ID, LAYOUT, PROTECTED_CHANNEL_IDS
from rebuild import inventory_signature, keep_ids

ENTRY_CATEGORY = '── ⟡ 설온의 문 앞 ⟡ ──'
ENTRY_LOG = '﹒입장로그'
ENTRY_ROLE = '﹒역할받기'
CLAN_ROLE = '클랜원'
CLAN_ROLE_ID = 1522946198984593541
OPS_CATEGORY = '── ⟡ 설온 운영실 ⟡ ──'
ENTRY_LOG_CHANNEL_ID = 1523611752837681192
ADMIN_PROTECTED_IDS = PROTECTED_CHANNEL_IDS - {ENTRY_LOG_CHANNEL_ID}
CONFIG_REQUEST = 2026102703
ADMIN_CHAT_CHANNEL_ID = 1523604140108939285
ADMIN_CATEGORY = '── ⟡ 관리자 전용 ⟡ ──'
ADMIN_CHAT_NAME = '﹒관리자채팅'
ADMIN_CHAT_REQUEST = 2026102701


async def correct_clan_role(guild, owner_id, store, logger):
    """Use the exact existing role authorized by the owner, regardless of hierarchy."""
    if store.get('entry:exact-role-applied') == CLAN_ROLE_ID:
        return
    roles = await guild.fetch_roles()
    role = next((r for r in roles if r.id == CLAN_ROLE_ID), None)
    if role is None or role.permissions.administrator:
        raise ValueError('지정한 클랜원 역할이 없거나 관리자 권한 때문에 채널 숨김을 적용할 수 없습니다.')
    owner = await guild.fetch_member(owner_id)
    channels = await guild.fetch_channels()
    if not PROTECTED_CHANNEL_IDS <= {c.id for c in channels}:
        raise ValueError('보호 채널을 모두 확인할 수 없습니다.')
    modes = {}
    for channel in channels:
        mode = entry_mode(channel, guild, store)
        if channel.id in {store.get('admin:category'), ADMIN_CHAT_CHANNEL_ID}:
            mode = 'admin'
        if mode in {'main', 'admin', 'entry'}:
            modes[channel.id] = mode
            await channel.edit(overwrites=view_overwrites(channel.overwrites, guild.default_role,
                role, owner, guild.me, mode, readonly=mode == 'entry'),
                reason='소유자가 지정한 실제 클랜원 역할 1522946198984593541로 접근권한 수정')
    fresh = {c.id: c for c in await guild.fetch_channels()}
    for cid, mode in modes.items():
        channel = fresh[cid]
        if (channel.overwrites_for(role).view_channel is not (mode == 'main') or
                channel.overwrites_for(guild.default_role).view_channel is not (mode == 'entry')):
            raise RuntimeError(f'{cid}: 실제 클랜원 역할 접근권한 검증 실패')
        for target, overwrite in channel.overwrites.items():
            if target.id not in {role.id, owner.id, guild.me.id, guild.default_role.id} and overwrite.view_channel is True:
                raise RuntimeError(f'{cid}: 이전 역할 접근 허용이 남아 있습니다.')
    store.put('entry:clan-role', role.id)
    store.put('entry:exact-role-applied', role.id)
    logger.warning('실제 클랜원 역할 수정 완료: %s (%s) · %s개 채널/카테고리 검증 · 역할 지급 가능: %s',
                   role.name, role.id, len(modes), not role.managed and role < guild.me.top_role)


async def setup_admin_chat(guild, owner_id, store, logger):
    if store.get('admin:configured') == ADMIN_CHAT_REQUEST:
        return
    channels = await guild.fetch_channels()
    chat = next((c for c in channels if c.id == ADMIN_CHAT_CHANNEL_ID), None)
    if not isinstance(chat, discord.TextChannel):
        raise ValueError('지정한 관리자 채팅 채널을 확인할 수 없습니다.')
    owner = await guild.fetch_member(owner_id)
    clan = guild.get_role(store.get('entry:clan-role') or 0)
    if clan is None:
        raise ValueError('클랜원 역할 설정이 먼저 필요합니다.')
    category = next((c for c in channels if c.id == store.get('admin:category') and isinstance(c, discord.CategoryChannel)), None)
    if category is None:
        matches = [c for c in channels if isinstance(c, discord.CategoryChannel) and c.name == ADMIN_CATEGORY]
        if len(matches) > 1:
            raise ValueError('관리자 전용 카테고리가 중복되어 있습니다.')
        category = matches[0] if matches else await guild.create_category(ADMIN_CATEGORY,
            overwrites=view_overwrites({}, guild.default_role, clan, owner, guild.me, 'admin'),
            reason='관리자 채팅과 가입 신청 확인을 위한 전용 카테고리 생성')
        store.put('admin:category', category.id)
    await category.edit(overwrites=view_overwrites(category.overwrites, guild.default_role, clan, owner, guild.me, 'admin'),
                        reason='소유자 요청: 관리자 전용 카테고리 보기 권한')
    await chat.edit(name=ADMIN_CHAT_NAME, category=category, sync_permissions=False,
                    overwrites=view_overwrites(chat.overwrites, guild.default_role, clan, owner, guild.me, 'admin'),
                    reason='소유자가 지정한 기존 관리자 채팅 채널 재사용')
    fresh = {c.id: c for c in await guild.fetch_channels()}
    for cid in [category.id, chat.id]:
        c = fresh[cid]
        if c.overwrites_for(guild.default_role).view_channel is not False or c.overwrites_for(clan).view_channel is not False:
            raise RuntimeError('관리자 채팅 공간의 비공개 권한 확인에 실패했습니다.')
    if fresh[chat.id].category_id != category.id:
        raise RuntimeError('관리자 채팅 채널의 위치 확인에 실패했습니다.')
    store.put('admin:chat', chat.id)
    store.put('admin:configured', ADMIN_CHAT_REQUEST)
    logger.warning('관리자 채팅 설정 완료: %s (%s) · 카테고리 %s (%s)',
                   ADMIN_CHAT_NAME, chat.id, ADMIN_CATEGORY, category.id)


async def configure_once(guild, owner_id, store, data, lock, require_admin, logger):
    """Apply the user's explicitly requested configuration once, not on every reconnect."""
    if store.get('entry:configuration-request') == CONFIG_REQUEST:
        return
    store.put('entry:configuration-request', CONFIG_REQUEST)
    try:
        owner = await guild.fetch_member(owner_id)
        channels = await guild.fetch_channels()
        class Reporter:
            success = False

            async def defer(self, **kwargs):
                pass

            async def send(self, content, **kwargs):
                self.success = content.startswith('입장 설정 완료')
                if self.success:
                    logger.warning('자동 입장 설정 완료: %s', content)
                else:
                    logger.error('자동 입장 설정 결과: %s', content)

        report = Reporter()
        context = SimpleNamespace(guild=guild, guild_id=guild.id, user=owner,
                                  permissions=owner.guild_permissions, response=report, followup=report)
        view = GateView(owner_id, inventory_signature(channels), store, data, lock, require_admin, logger)
        await view.apply(context)
        if report.success:
            store.put('entry:configuration-success', CONFIG_REQUEST)
        else:
            logger.error('입장 설정이 완료되지 않았습니다. 소유자가 /입장설정으로 원인을 확인할 수 있습니다.')
    except Exception:
        logger.exception('자동 입장 설정 중단. 반복 실행하지 않습니다. /입장설정으로 확인하세요.')


def view_overwrites(existing, everyone, clan, owner, bot, mode, readonly=False):
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair()) for target, value in existing.items()}
    # Other role/member allows would override a role deny in Discord.
    # Normalize only visibility; preserve all unrelated permission bits.
    for value in result.values():
        value.view_channel = None
    for target, visible in [(everyone, mode == 'entry'), (clan, mode == 'main'), (owner, True), (bot, True)]:
        value = result.setdefault(target, discord.PermissionOverwrite())
        value.view_channel = visible
    if readonly:
        result[everyone].send_messages = False
        result[bot].send_messages = True
    return result


def safe_clan_role(role):
    return not any(getattr(role.permissions, p) for p in
                   ['administrator', 'manage_guild', 'manage_roles', 'manage_channels', 'ban_members',
                    'kick_members', 'moderate_members', 'manage_webhooks'])


def protected_channel(c):
    return c.id in PROTECTED_CHANNEL_IDS or getattr(c, 'category_id', None) in PROTECTED_CHANNEL_IDS


def entry_mode(c, guild, store):
    if c.id == ENTRY_LOG_CHANNEL_ID:
        return 'entry'
    if protected_channel(c):
        return 'admin'
    if c.id in {store.get('entry:category'), store.get('entry:log'), store.get('entry:role')}:
        return 'entry'
    if c.name == OPS_CATEGORY or (getattr(c, 'category', None) and c.category.name == OPS_CATEGORY):
        return 'private'
    if isinstance(c, discord.CategoryChannel) and any(
            protected_channel(ch) and getattr(ch, 'category_id', None) == c.id for ch in guild.channels):
        return 'protected-parent'
    known = {store.get('category:' + name) for name, _, _ in LAYOUT}
    for _, _, entries in LAYOUT:
        for name, _ in entries:
            known.add(store.get('text:' + name))
            known.add(store.get('voice:' + name))
    if c.id in known or store.get('entry:gated:' + str(c.id)):
        return 'main'
    return 'main' if c.permissions_for(guild.default_role).view_channel else 'private'


class JoinRequestView(discord.ui.View):
    def __init__(self, store):
        super().__init__(timeout=None)
        self.store = store

    @discord.ui.button(label='클랜 가입 신청', style=discord.ButtonStyle.primary, custom_id='seolon:membership-request:v1')
    async def request(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID or interaction.user.bot:
            await interaction.response.send_message('설온 서버 회원만 신청할 수 있습니다.', ephemeral=True)
            return
        role_id = self.store.get('entry:clan-role')
        if role_id and any(r.id == role_id for r in interaction.user.roles):
            await interaction.response.send_message('이미 클랜원 역할이 있습니다.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = interaction.guild.get_channel(ADMIN_CHAT_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send('관리자 알림 채널을 준비 중입니다. 잠시 후 다시 신청해 주세요.', ephemeral=True)
            return
        accepted, wait, repeat = self.store.reserve_membership_notice(interaction.user.id)
        if not accepted:
            minutes, seconds = divmod(wait, 60)
            await interaction.followup.send(f'이미 관리자를 호출했습니다. {minutes}분 {seconds}초 후 다시 알릴 수 있습니다.', ephemeral=True)
            return
        # Ping real administrator roles and the server owner, never @everyone.
        admins = [r for r in interaction.guild.roles if r.id != interaction.guild.id
                  and r.permissions.administrator and not r.managed]
        owner_id = interaction.guild.owner_id
        mentions = ' '.join(r.mention for r in admins)
        if owner_id:
            mentions += f' <@{owner_id}>'
        embed = discord.Embed(title='클랜 가입 신청 재알림' if repeat else '새 클랜 가입 신청', colour=0xB8C8D8,
            description=f'신청자: <@{interaction.user.id}>\n사용자 ID: `{interaction.user.id}`\n관리자 확인을 기다리고 있습니다.')
        embed.set_footer(text='같은 신청자의 관리자 호출은 10분에 한 번만 전달됩니다.')
        try:
            await channel.send(content=(mentions.strip() + '\n클랜 가입 신청을 확인해 주세요.'), embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=admins,
                    users=[discord.Object(id=owner_id)] if owner_id else [], replied_user=False))
        except discord.HTTPException:
            logging.getLogger('seolon').exception('가입 신청 관리자 알림 전송 실패')
            await interaction.followup.send('신청은 접수했지만 관리자 알림을 보내지 못했습니다. 운영진에게 알려 주세요. 재알림은 10분 후 가능합니다.', ephemeral=True)
            return
        unlock = int(time.time()) + 600
        await interaction.followup.send('관리자 채널에 알림을 보냈습니다. 확인할 때까지 기다려 주세요.\n'
                                       f'다음 관리자 호출: <t:{unlock}:R>', ephemeral=True)


class GateView(discord.ui.View):
    def __init__(self, owner, signature, store, data, lock, require_admin, logger):
        super().__init__(timeout=180)
        self.owner = owner
        self.signature = signature
        self.store = store
        self.data = data
        self.lock = lock
        self.require_admin = require_admin
        self.logger = logger
        self.used = False

    @discord.ui.button(label='입장·클랜원·관리자 권한 적용', style=discord.ButtonStyle.primary)
    async def apply_permissions(self, interaction, button):
        await self.apply(interaction)

    async def apply(self, interaction):
        self.require_admin(interaction)
        if interaction.user.id != self.owner or self.used or self.lock.locked():
            await interaction.response.send_message('소유자 계정인지 확인하세요. 이미 실행 중인 작업은 다시 시작할 수 없습니다.', ephemeral=True)
            return
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            count = 0
            try:
                guild = interaction.guild
                original = await guild.fetch_channels()
                if not PROTECTED_CHANNEL_IDS <= {c.id for c in original}:
                    raise ValueError('보호 채널 4개를 모두 확인할 수 없어 중단합니다.')
                if inventory_signature(original) != self.signature:
                    raise ValueError('미리보기 이후 서버가 바뀌었습니다. /입장설정을 다시 실행하세요.')
                if not guild.me.guild_permissions.manage_roles or not guild.me.guild_permissions.manage_channels:
                    raise ValueError('봇에 역할 관리·채널 관리 권한이 필요합니다.')
                if guild.default_role.permissions.administrator:
                    raise ValueError('@everyone에 관리자 권한이 있어 채널 숨김이 불가능합니다. 기본 역할의 관리자 권한을 먼저 해제하세요.')
                matches = [r for r in guild.roles if r.id == (CLAN_ROLE_ID if self.store.get('entry:exact-role-applied') else self.store.get('entry:clan-role'))]
                if not matches:
                    matches = [r for r in guild.roles if r.name == CLAN_ROLE]
                if len(matches) > 1:
                    raise ValueError('클랜원 역할이 여러 개입니다. 중복 역할을 확인하세요.')
                role = matches[0] if matches else await guild.create_role(
                    name=CLAN_ROLE, permissions=discord.Permissions.none(), colour=discord.Colour(0xB8C8D8),
                    reason='소유자가 승인한 클랜원 접근 역할 생성')
                if (not safe_clan_role(role) or role.managed or role >= guild.me.top_role) and not self.store.get('entry:clan-role'):
                    # Do not modify old higher/privileged roles. Use a new ordinary role
                    # below the bot so the owner does not need to rearrange legacy roles.
                    alternatives = [r for r in guild.roles if r.name == '✧ 클랜원']
                    if len(alternatives) > 1:
                        raise ValueError('✧ 클랜원 역할이 중복되어 있습니다. 중복 역할을 확인하세요.')
                    role = alternatives[0] if alternatives else await guild.create_role(
                        name='✧ 클랜원', permissions=discord.Permissions.none(), colour=discord.Colour(0xB8C8D8),
                        reason='기존 역할을 유지하고 봇이 관리할 수 있는 전용 클랜원 역할 생성')
                    fresh_roles = await guild.fetch_roles()
                    bot_role_ids = {r.id for r in guild.me.roles}
                    bot_top = max(r for r in fresh_roles if r.id in bot_role_ids)
                    role = next(r for r in fresh_roles if r.id == role.id)
                    if not safe_clan_role(role) or role.managed or role >= bot_top:
                        raise ValueError('전용 클랜원 역할이 설온지기의 관리 범위를 벗어납니다. 역할 순서를 확인하세요.')
                    self.logger.warning('기존 역할을 유지하고 전용 역할 사용: %s (%s)', role.name, role.id)
                elif role.id != CLAN_ROLE_ID and (not safe_clan_role(role) or role.managed or role >= guild.me.top_role):
                    raise ValueError('클랜원 역할은 관리 권한이 없어야 하고 설온지기 역할보다 아래에 있어야 합니다.')
                self.store.put('entry:clan-role', role.id)
                self.data.mkdir(parents=True, exist_ok=True)
                (self.data / ('entry-permissions-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')).write_text(
                    json.dumps({'protected_admin_only_approved': True,
                                'structure': inventory_signature(original)}, ensure_ascii=False, indent=2), encoding='utf-8')
                category = guild.get_channel(self.store.get('entry:category') or 0)
                if category is None:
                    category = await guild.create_category(ENTRY_CATEGORY, overwrites=view_overwrites(
                        {}, guild.default_role, role, interaction.user, guild.me, 'entry', readonly=True))
                    self.store.put('entry:category', category.id)
                elif protected_channel(category):
                    raise ValueError('입장 채널 설정이 보호 항목을 가리키고 있어 중단합니다.')
                entries = [category]
                for key, name in [('log', ENTRY_LOG), ('role', ENTRY_ROLE)]:
                    if key == 'log':
                        c = next((c for c in original if c.id == ENTRY_LOG_CHANNEL_ID), None)
                        if not isinstance(c, discord.TextChannel):
                            raise ValueError('지정한 입장 로그 채널을 확인할 수 없습니다.')
                        # The user explicitly authorized this one pinned channel's name,
                        # parent, and visibility change; its ID/messages remain protected.
                        c = await c.edit(name=ENTRY_LOG, category=category, sync_permissions=False,
                            overwrites=view_overwrites(c.overwrites, guild.default_role, role,
                                                      interaction.user, guild.me, 'entry', readonly=True),
                            reason='소유자가 지정한 기존 입장 로그 채널 재사용')
                        self.store.put('entry:log', c.id)
                    else:
                        c = guild.get_channel(self.store.get('entry:' + key) or 0)
                    if c is None:
                        c = await guild.create_text_channel(name, category=category,
                            overwrites=view_overwrites({}, guild.default_role, role, interaction.user, guild.me, 'entry', readonly=True))
                        self.store.put('entry:' + key, c.id)
                    if protected_channel(c) and c.id != ENTRY_LOG_CHANNEL_ID:
                        raise ValueError('입장 채널 설정이 보호 항목을 가리키고 있어 중단합니다.')
                    entries.append(c)
                # Hide main channels before making the entrance visible on reconfiguration.
                for c in original:
                    mode = entry_mode(c, guild, self.store)
                    if mode not in {'main', 'admin'}:
                        continue
                    await c.edit(overwrites=view_overwrites(c.overwrites, guild.default_role, role,
                                interaction.user, guild.me, mode), reason='소유자가 지정한 신규·클랜원·관리자 채널 보기 설정')
                    if mode == 'main':
                        self.store.put('entry:gated:' + str(c.id), c.id)
                    count += 1
                for c in entries:
                    await c.edit(overwrites=view_overwrites(c.overwrites, guild.default_role, role,
                                interaction.user, guild.me, 'entry', readonly=True), reason='신규 회원 전용 입장 공간 설정')
                flags = copy.copy(guild.system_channel_flags)
                flags.join_notifications = True
                await guild.edit(system_channel=entries[1], system_channel_flags=flags,
                                 reason='디스코드 기본 입장 로그를 사용하도록 설정')
                embed = discord.Embed(title='설온에 오신 것을 환영합니다', colour=0xB8C8D8,
                    description='아래 버튼으로 클랜 가입을 신청해 주세요.\n운영진이 확인하고 클랜원 역할을 지급하면 설온의 채널이 열립니다.')
                panel_id = self.store.get('entry:panel')
                message = None
                if panel_id:
                    try:
                        message = await entries[2].fetch_message(panel_id)
                    except discord.NotFound:
                        pass
                if message:
                    await message.edit(embed=embed, view=JoinRequestView(self.store))
                else:
                    message = await entries[2].send(embed=embed, view=JoinRequestView(self.store))
                    self.store.put('entry:panel', message.id)
                self.store.put('entry:active', 1)
                self.store.put('entry:protected-admin-only', 1)
                final = await guild.fetch_channels()
                indexed = {c.id: c for c in final}
                for prior in original:
                    mode = entry_mode(prior, guild, self.store)
                    if mode not in {'main', 'admin'}:
                        continue
                    current = indexed.get(prior.id)
                    expected_clan_view = mode == 'main'
                    if current is None or current.overwrites_for(guild.default_role).view_channel is not False or current.overwrites_for(role).view_channel is not expected_clan_view:
                        raise RuntimeError(f'{prior.name}: 본 채널 권한 확인에 실패했습니다.')
                for entry in entries:
                    current = indexed.get(entry.id)
                    if current is None or current.overwrites_for(guild.default_role).view_channel is not True or current.overwrites_for(role).view_channel is not False:
                        raise RuntimeError(f'{entry.name}: 입장 공간 권한 확인에 실패했습니다.')
                # No existing users are silently assigned membership.
                await interaction.followup.send(
                    f'입장 설정 완료 · 본 채널 {count}개 항목 접근권한 설정\n'
                    f'접근 역할: {role.name}\n'
                    '신규: 입장로그·역할받기 → 클랜원 역할 지급 후: 본 채널\n'
                    '기존 회원에게 역할을 자동 지급하지 않았습니다. /가입신청목록, /클랜원지급을 사용하세요.\n'
                    + '기존 입장 로그 채널은 메시지를 보존해 재사용하고, 나머지 보호 3개는 관리자 전용으로 설정했습니다.'
                    + '\n서버 소유자와 관리자 권한 계정은 디스코드 특성상 두 공간을 모두 볼 수 있습니다.', ephemeral=True)
            except Exception as exc:
                self.logger.exception('Entrance setup stopped')
                await interaction.followup.send(f'입장 설정 중단: {type(exc).__name__}: {str(exc)[:700]}\n'
                    f'{count}개 항목까지 권한을 적용했을 수 있습니다. /입장설정으로 다시 확인하세요.', ephemeral=True)
        self.stop()

    async def on_error(self, interaction, error, item):
        self.logger.error('Entrance button error: %s', type(error).__name__)
        text = '소유자 계정과 관리자 권한을 확인하세요.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


async def show_gate(interaction, store, data, lock, require_admin, logger):
    require_admin(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    channels = await interaction.guild.fetch_channels()
    lines = ['입장 설정: 신규 회원 → 입장로그/역할받기만 표시, 클랜원 → 본 채널 표시',
             '클랜원 역할 지급은 소유자 명령어로만 처리합니다. 가입 신청 버튼은 역할을 직접 지급하지 않습니다.',
             '회원·역할·채널·메시지 삭제 없음. 기존 회원 자동 역할 지급 없음.',
             f'기존 입장 로그 {ENTRY_LOG_CHANNEL_ID}는 메시지를 보존하고 입장 공간으로 이동·이름 변경합니다.', '', '항목별 접근권한 계획']
    for c in channels:
        mode = entry_mode(c, interaction.guild, store)
        labels = {'main': '신규 숨김 / 클랜원 표시', 'private': '비공개 권한 유지',
                  'admin': '보호 항목: 신규·클랜원 숨김 / 서버 관리자만 표시',
                  'protected-parent': '보호 채널의 상위 카테고리: 권한 유지', 'entry': '신규 표시 / 클랜원 숨김'}
        lines.append(f'{c.name} ({c.id}) · {labels[mode]}')
    lines += ['', '사용자가 지정한 관리자 전용 보호 채널 3개:']
    for cid in sorted(ADMIN_PROTECTED_IDS):
        c = next((c for c in channels if c.id == cid), None)
        lines.append(f'{cid} · {c.name if c else "현재 서버에서 확인되지 않음"}')
    lines += ['', '관리자 전용 보호 3개는 이름·위치·메시지를 유지하고 보기 권한만 적용합니다.',
              '디스코드의 관리자 권한은 채널 숨김을 우회합니다. 일반 계정 기준으로 확인하세요.',
              '입장 로그는 서버 기본 입장 알림을 사용하므로 봇이 꺼진 새벽에도 기록됩니다.']
    embed = discord.Embed(title='설온 · 입장과 역할 설정', colour=0xB8C8D8,
        description='신규 회원: ﹒입장로그 / ﹒역할받기\n클랜원: 본 채널 표시 + 입장 공간 숨김\n\n'
        f'기존 입장 로그 `{ENTRY_LOG_CHANNEL_ID}`는 메시지를 보존해 입장 공간에 재사용합니다.\n'
        '나머지 보호 채널 **3개는 서버 관리자만** 볼 수 있게 설정합니다.\n'
        '첨부한 항목별 계획을 확인하세요. 관리자 계정은 채널 숨김의 예외입니다.')
    await interaction.followup.send(embed=embed,
        file=discord.File(io.BytesIO('\n'.join(lines).encode()), filename='seolon-entry-permissions.txt'),
        view=GateView(interaction.user.id, inventory_signature(channels), store, data, lock, require_admin, logger), ephemeral=True)
