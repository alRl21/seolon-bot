import asyncio
import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands

from core import GUILD_ID, LAYOUT, Store, PROTECTED_CHANNEL_IDS, ARCHIVE_NAME, VOICE_NAMES
from schedule import active_seconds
from reset_ui import show_reset
from membership import show_gate, JoinRequestView, safe_clan_role, configure_once, setup_admin_chat
from arrange import arrange_categories

DATA = Path(os.environ.get('DATA_DIR', 'data'))
store = Store(DATA / 'seolon.sqlite3')
log = logging.getLogger('seolon')
lock = asyncio.Lock()
OWNER_ID = 1119230372174123169
APPLICATION_ID = 1564477701199233125


def require_owner(interaction):
    if (OWNER_ID <= 0 or interaction.guild_id != GUILD_ID
            or interaction.user.id != OWNER_ID):
        raise app_commands.CheckFailure('이 봇은 지정된 소유자만 사용할 수 있습니다.')


class OwnerTree(app_commands.CommandTree):
    async def interaction_check(self, interaction):
        require_owner(interaction)
        return True


class Client(discord.Client):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.tree = OwnerTree(self)

    async def setup_hook(self):
        application = await self.application_info()
        if application.id != APPLICATION_ID:
            raise RuntimeError('설온지기의 토큰이 아닙니다. Application ID 1564477701199233125의 Bot 토큰을 확인하세요.')
        self.add_view(JoinRequestView(store))
        await self.tree.sync(guild=discord.Object(id=GUILD_ID))


client = Client()
scope = discord.Object(id=GUILD_ID)


@client.event
async def on_ready():
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        log.error('설온 서버에 봇이 설치되어 있지 않거나 서버에 접근할 수 없습니다.')
        return
    log.warning('설온지기 연결 완료 · 서버: %s · 소유자 전용 명령어 준비 완료', guild.name)
    await configure_once(guild, OWNER_ID, store, DATA, lock, require_admin, log)
    try:
        async with lock:
            await setup_admin_chat(guild, OWNER_ID, store, log)
            await arrange_categories(guild, store, client.http, log)
    except Exception:
        log.exception('카테고리 순서 적용을 완료하지 못했습니다.')


def require_admin(interaction):
    require_owner(interaction)
    if interaction.guild_id != GUILD_ID or not interaction.permissions.administrator:
        raise app_commands.CheckFailure('설온 서버 관리자만 사용할 수 있습니다.')


def unique(items, names):
    hits = [x for x in items if x.name in names]
    if len(hits) > 1:
        raise ValueError('같은 용도의 항목이 여러 개입니다: ' + ', '.join(f'{x.name} ({x.id})' for x in hits))
    return hits[0] if hits else None


def protected(obj):
    return obj.id in PROTECTED_CHANNEL_IDS or getattr(obj, 'category_id', None) in PROTECTED_CHANNEL_IDS


def protected_category(cat, guild):
    return protected(cat) or any(protected(c) and getattr(c, 'category_id', None) == cat.id for c in guild.channels)


def plan(guild):
    result = []
    used = set()
    for name, aliases, channels in LAYOUT:
        key = 'category:' + name
        categories = [c for c in guild.categories if not protected_category(c, guild)]
        saved = guild.get_channel(store.get(key) or 0)
        cat = saved if saved in categories else unique(categories, [name] + aliases)
        result.append((key, name, cat, None))
        if cat:
            used.add(cat.id)
        for cname, calias in channels:
            kind = discord.VoiceChannel if cname in VOICE_NAMES else discord.TextChannel
            ckey = ('voice:' if cname in VOICE_NAMES else 'text:') + cname
            candidates = [c for c in guild.channels if isinstance(c, kind) and not protected(c) and c.id not in used]
            saved = guild.get_channel(store.get(ckey) or 0)
            obj = saved if saved in candidates else None
            if obj is None:
                # Exact current names win; aliases are only used if unique.
                obj = unique(candidates, [cname]) or unique(candidates, calias)
            if obj:
                used.add(obj.id)
            result.append((ckey, cname, obj, key))
    # Surplus public channels are kept with their messages and permission overwrites.
    # Private channels and all explicitly protected channels stay in place.
    extras = [c for c in guild.channels if isinstance(c, (discord.TextChannel, discord.VoiceChannel))
              and c.id not in used and not protected(c)
              and c.permissions_for(guild.default_role).view_channel]
    if extras:
        akey = 'category:' + ARCHIVE_NAME
        archive = unique([c for c in guild.categories if not protected_category(c, guild)], [ARCHIVE_NAME])
        result.append((akey, ARCHIVE_NAME, archive, None))
        for c in extras:
            result.append(('archive:' + str(c.id), c.name, c, akey))
    return result


def overwrite_fingerprint(obj):
    return sorted((target.id, *[p.value for p in permissions.pair()])
                  for target, permissions in obj.overwrites.items())


def fingerprint(items):
    return [(k, n, o.id if o else None, o.name if o else None,
             getattr(o, 'category_id', None), overwrite_fingerprint(o) if o else [])
            for k, n, o, parent in items]


async def update_existing(obj, name, parent=None):
    if protected(obj):
        raise ValueError(f'보호 채널 {obj.id} 변경을 차단했습니다.')
    changes = {}
    if obj.name != name:
        changes['name'] = name
    if parent is not None and obj.category_id != parent.id:
        changes.update(category=parent, sync_permissions=False, overwrites=dict(obj.overwrites))
    if changes:
        return await obj.edit(**changes, reason='설온 특수기호 디자인 적용')
    return obj


class ApplyView(discord.ui.View):
    def __init__(self, owner, expected):
        super().__init__(timeout=180)
        self.owner = owner
        self.expected = expected
        self.used = False

    @discord.ui.button(label='이 디자인 적용', style=discord.ButtonStyle.primary)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        require_admin(interaction)
        if interaction.user.id != self.owner:
            await interaction.response.send_message('미리보기를 연 관리자만 적용할 수 있습니다.', ephemeral=True)
            return
        if self.used or lock.locked():
            await interaction.response.send_message('이미 적용 중이거나 사용한 버튼입니다.', ephemeral=True)
            return
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with lock:
            guild = interaction.guild
            try:
                current = plan(guild)
                if fingerprint(current) != self.expected:
                    raise ValueError('미리보기 이후 서버 구성이 바뀌었습니다. /디자인을 다시 실행하세요.')
                if not guild.me.guild_permissions.manage_channels:
                    raise ValueError('봇에 채널 관리 권한이 필요합니다.')
                DATA.mkdir(parents=True, exist_ok=True)
                snapshot = [{'id': c.id, 'name': c.name, 'type': str(c.type),
                             'position': c.position, 'category_id': getattr(c, 'category_id', None),
                             'overwrites': {str(t.id): list(p.pair()[i].value for i in (0, 1))
                                            for t, p in c.overwrites.items()}} for c in guild.channels]
                backup = DATA / ('structure-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
                backup.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
                parents = {}
                completed = 0
                for key, name, obj, parent in current:
                    if parent is None:
                        if obj:
                            if protected_category(obj, guild):
                                raise ValueError('보호 채널이 들어 있는 카테고리는 변경하지 않습니다.')
                            obj = await update_existing(obj, name)
                        else:
                            obj = await guild.create_category(name, reason='설온 특수기호 디자인 적용')
                        parents[key] = obj
                    else:
                        if obj:
                            obj = await update_existing(obj, name, parents[parent])
                        elif key.startswith('voice:'):
                            obj = await guild.create_voice_channel(name, category=parents[parent], reason='설온 특수기호 디자인 적용')
                        else:
                            obj = await guild.create_text_channel(name, category=parents[parent], reason='설온 특수기호 디자인 적용')
                    if not key.startswith('archive:'):
                        store.put(key, obj.id)
                    completed += 1
                await interaction.followup.send(
                    f'설온 디자인 v0.2 적용 완료 · {completed}개 항목 확인\n'
                    '지정한 보호 채널 4개는 이름·소속 카테고리·권한을 변경하지 않습니다.\n'
                    '남는 공개 채널은 이전 기록으로 보관하고, 비공개 채널은 기존 위치에 유지합니다.\n'
                    '메시지와 채널은 삭제하지 않습니다. 빈 기존 카테고리도 삭제하지 않았습니다.\n'
                    '＋ 새로운 방은 음성방 자리만 준비됩니다. 자동 생성 기능은 아직 없습니다.',
                    ephemeral=True)
            except Exception as exc:
                log.exception('Design apply failed')
                await interaction.followup.send(
                    f'적용을 멈췄습니다: {type(exc).__name__}: {str(exc)[:600]}\n'
                    '일부 항목은 적용됐을 수 있습니다. 원인을 해결한 뒤 /디자인으로 다시 확인하세요.', ephemeral=True)
        self.stop()

    async def on_error(self, interaction, error, item):
        log.error('Button failed: %s', type(error).__name__)
        message = '지정된 소유자 계정과 관리자 권한을 확인하세요. /디자인으로 다시 확인할 수 있습니다.'
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


@client.tree.command(name='디자인', description='한국어 채널 디자인을 미리 보고 적용합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
async def design(interaction: discord.Interaction):
    require_admin(interaction)
    if store.get('entry:active'):
        raise ValueError('입장 권한이 적용된 서버에서는 공개 채널이 다시 생기지 않도록 /디자인을 잠급니다. 현재 채널은 유지됩니다.')
    items = plan(interaction.guild)
    text = []
    for key, name, obj, parent in items:
        action = '생성' if not obj else ('유지' if obj.name == name else f'이름 변경: {obj.name}')
        if key.startswith('archive:'):
            action = '이전 기록에 보관' if not obj.category or obj.category.name != ARCHIVE_NAME else '보관 유지'
        elif obj and parent and obj.category_id != next((o.id for k, n, o, p in items if k == parent and o), None):
            action += ' · 카테고리 이동'
        text.append(f'{"  " if parent else ""}{name} · {action}')
    text.append('\n고정 보호 채널 (이름·소속 카테고리·권한 유지)')
    for cid in sorted(PROTECTED_CHANNEL_IDS):
        obj = interaction.guild.get_channel(cid)
        text.append(f'{cid} · {obj.name if obj else "현재 서버 목록에서 확인되지 않음 · 보호 설정은 유지"}')
    full_text = '\n'.join(text)
    embed = discord.Embed(title='설온 · 특수기호 디자인 v0.2', description=full_text[:3800], color=0xB8C8D8)
    embed.set_footer(text='소유자 전용 · 4개 채널 보호 · 삭제 없음 · 상세 변경 목록은 첨부 파일')
    attachment = discord.File(io.BytesIO(full_text.encode('utf-8')), filename='seolon-design-preview.txt')
    await interaction.response.send_message(embed=embed, file=attachment,
                                           view=ApplyView(interaction.user.id, fingerprint(items)), ephemeral=True)



@client.tree.command(name='출석', description='하루 한 번 출석하고 눈꽃을 받습니다', guild=scope)
async def attendance(interaction: discord.Interaction):
    result = store.attend(interaction.guild_id, interaction.user.id)
    if result is None:
        text = '오늘은 이미 출석했어요. 한국 시간 자정 이후 다시 만나요.'
    else:
        reward, streak = result
        text = f'❄ 출석 완료! 눈꽃 +{reward}\n연속 출석 {streak}일'
    await interaction.response.send_message(text, ephemeral=True)


@client.tree.command(name='초기화', description='보호 채널 4개 외 기존 채널을 삭제하고 새 디자인을 만듭니다', guild=scope)
@app_commands.default_permissions(administrator=True)
async def reset(interaction: discord.Interaction):
    require_admin(interaction)
    if store.get('entry:active'):
        raise ValueError('입장 권한이 적용되어 /초기화를 차단했습니다. 역할·입장 설정을 해제하는 별도 작업이 먼저 필요합니다.')
    await show_reset(interaction, store, DATA, lock, require_admin, log)


@client.tree.command(name='입장설정', description='신규 회원과 클랜원의 채널 보기 권한을 설정합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
async def entrance(interaction: discord.Interaction):
    await show_gate(interaction, store, DATA, lock, require_admin, log)


async def change_clan_role(interaction, member, add):
    require_admin(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    role = interaction.guild.get_role(store.get('entry:clan-role') or 0)
    if role is None:
        raise ValueError('먼저 /입장설정을 적용하세요.')
    if not safe_clan_role(role) or role.managed or role >= interaction.guild.me.top_role:
        raise ValueError('클랜원 역할 권한과 순서를 확인하세요. 설온지기 역할보다 아래에 있어야 합니다.')
    if member.bot:
        raise ValueError('봇 계정에는 클랜원 역할을 지급하지 않습니다.')
    member = await interaction.guild.fetch_member(member.id)
    if add:
        await member.add_roles(role, reason='설온 소유자 승인으로 클랜원 역할 지급')
        store.membership_state(member.id, 'approved')
    else:
        await member.remove_roles(role, reason='설온 소유자 요청으로 클랜원 역할 회수')
        store.membership_state(member.id, 'removed')
    await interaction.followup.send(f'{member.display_name}: {role.name} 역할 {"지급" if add else "회수"} 완료. 채널 보기가 자동으로 바뀝니다.', ephemeral=True)


@client.tree.command(name='클랜원지급', description='선택한 회원에게 클랜원 역할을 지급합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(대상='역할을 지급할 서버 회원')
async def grant_clan(interaction: discord.Interaction, 대상: discord.Member):
    await change_clan_role(interaction, 대상, True)


@client.tree.command(name='클랜원회수', description='선택한 회원의 클랜원 역할을 회수합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
@app_commands.describe(대상='역할을 회수할 서버 회원')
async def remove_clan(interaction: discord.Interaction, 대상: discord.Member):
    await change_clan_role(interaction, 대상, False)


@client.tree.command(name='가입신청목록', description='접수된 클랜 가입 신청을 확인합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
async def membership_requests(interaction: discord.Interaction):
    require_admin(interaction)
    rows = store.pending_members()
    text = '\n'.join(f'<@{user}> · ID {user} · {date[:16]}' for user, date in rows)
    await interaction.response.send_message('가입 신청 (먼저 접수된 25명)\n' + (text or '대기 중인 신청이 없습니다.')
                                           + '\n/클랜원지급에서 대상을 선택하면 승인됩니다.', ephemeral=True)


@client.tree.command(name='채널정렬', description='보호 구역 바로 아래 입장 공간과 본 채널 순서를 정렬합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
async def arrange(interaction: discord.Interaction):
    require_admin(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with lock:
        await arrange_categories(interaction.guild, store, client.http, log, force=True)
    await interaction.followup.send('보호 구역 바로 아래에 입장 공간과 설온 본 채널을 순서대로 배치했습니다.', ephemeral=True)


@client.tree.command(name='여권', description='내 출석과 눈꽃 잔액을 확인합니다', guild=scope)
async def passport(interaction: discord.Interaction):
    balance, days = store.profile(interaction.guild_id, interaction.user.id)
    embed = discord.Embed(title='❄ 설온 여권', color=0xA7C7E7)
    embed.add_field(name='이름', value=interaction.user.display_name)
    embed.add_field(name='누적 출석', value=f'{days}일')
    embed.add_field(name='보유 눈꽃', value=f'{balance:,}')
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name='상태', description='봇의 현재 기능을 확인합니다', guild=scope)
async def status(interaction: discord.Interaction):
    await interaction.response.send_message('설온 봇 v0.4 · 보호 채널 4개\n사용 가능: /디자인 /초기화 /입장설정 /클랜원지급 /클랜원회수 /가입신청목록 /출석 /여권 /구조기록\n'
                                           '아직 없음: 상점 교환, 자동 역할, 음성방, 제재, AI 대화', ephemeral=True)


@client.tree.command(name='구조기록', description='현재 채널 이름과 위치 기록을 다운로드합니다', guild=scope)
@app_commands.default_permissions(administrator=True)
async def export(interaction: discord.Interaction):
    require_admin(interaction)
    data = [{'id': c.id, 'name': c.name, 'category_id': getattr(c, 'category_id', None),
             'position': c.position} for c in interaction.guild.channels]
    await interaction.response.send_message(file=discord.File(io.BytesIO(json.dumps(data, ensure_ascii=False,
                                                        indent=2).encode()), filename='seolon-structure.json'), ephemeral=True)


@client.tree.error
async def command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, 'original', error)
    log.error('Command failed: %s', type(original).__name__)
    message = str(original)[:800] if isinstance(original, (ValueError, app_commands.CheckFailure)) else '실행을 완료하지 못했습니다. 봇 권한과 운영 로그를 확인하세요.'
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def run_scheduled(token):
    seconds = active_seconds()
    if seconds <= 0:
        log.warning('한국 시간 00:00~07:00 휴식 시간입니다. 프로세스를 종료합니다.')
        return
    async with client:
        try:
            await asyncio.wait_for(client.start(token), timeout=seconds)
        except asyncio.TimeoutError:
            log.warning('한국 시간 자정입니다. 연결을 닫고 프로세스를 종료합니다.')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    if OWNER_ID <= 0:
        raise SystemExit('OWNER_ID 환경변수에 본인의 Discord 사용자 ID를 설정하세요. 설정 전에는 누구도 명령어를 실행할 수 없습니다.')
    token = os.environ.get('DISCORD_TOKEN', '').strip()
    if not token:
        raise SystemExit('DISCORD_TOKEN 환경변수에 봇 토큰을 설정하세요. 채팅에 보내지 마세요.')
    asyncio.run(run_scheduled(token))
