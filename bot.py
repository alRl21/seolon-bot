import asyncio
import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands

from core import GUILD_ID, LAYOUT, Store

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


def require_admin(interaction):
    require_owner(interaction)
    if interaction.guild_id != GUILD_ID or not interaction.permissions.administrator:
        raise app_commands.CheckFailure('설온 서버 관리자만 사용할 수 있습니다.')


def unique(items, names):
    hits = [x for x in items if x.name in names]
    if len(hits) > 1:
        raise ValueError('같은 용도의 항목이 여러 개입니다: ' + ', '.join(f'{x.name} ({x.id})' for x in hits))
    return hits[0] if hits else None


def plan(guild):
    result = []
    for name, aliases, channels in LAYOUT:
        key = 'category:' + name
        saved = guild.get_channel(store.get(key) or 0)
        cat = saved or unique(guild.categories, [name] + aliases)
        if cat and not isinstance(cat, discord.CategoryChannel):
            raise ValueError('기록된 카테고리 종류가 다릅니다.')
        result.append((key, name, cat, None))
        # Reuse channels only inside the selected category; do not relocate unrelated channels.
        for cname, calias in channels:
            ckey = 'text:' + cname
            obj = guild.get_channel(store.get(ckey) or 0)
            if not obj:
                obj = unique(cat.text_channels if cat else [], [cname] + calias)
            if obj and (not isinstance(obj, discord.TextChannel) or not cat or obj.category_id != cat.id):
                raise ValueError(f'{cname}: 기존 위치가 바뀌어 적용을 멈췄습니다.')
            result.append((ckey, cname, obj, key))
    return result


def fingerprint(items):
    return [(k, n, o.id if o else None, o.name if o else None,
             getattr(o, 'category_id', None)) for k, n, o, parent in items]


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
                            if obj.name != name:
                                await obj.edit(name=name, reason='설온 디자인 적용')
                        else:
                            obj = await guild.create_category(name, reason='설온 디자인 적용')
                        parents[key] = obj
                    else:
                        if obj:
                            if obj.name != name:
                                # Name only: never synchronize or replace existing overwrites.
                                await obj.edit(name=name, reason='설온 디자인 적용')
                        else:
                            obj = await guild.create_text_channel(name, category=parents[parent],
                                                                  reason='설온 디자인 적용')
                    store.put(key, obj.id)
                    completed += 1
                await interaction.followup.send(
                    f'디자인 적용 완료 · {completed}개 항목 확인\n기존 메시지·권한·채널을 삭제하지 않았습니다.\n'
                    '음성방과 운영진 비공개 공간은 기존 구성을 유지했습니다.\n'
                    '출석: /출석 · 내 기록: /여권\n상점·AI·자동 음성방·제재 기능은 아직 연결되지 않았습니다.',
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
    items = plan(interaction.guild)
    text = []
    for key, name, obj, parent in items:
        action = '생성' if not obj else ('유지' if obj.name == name else f'이름 변경: {obj.name}')
        text.append(f'{"  " if parent else ""}{name} · {action}')
    embed = discord.Embed(title='설온 · 디자인 미리보기', description='\n'.join(text), color=0xA7C7E7)
    embed.set_footer(text='관리자 전용 · 삭제 없음 · 중복이 발견되면 중단 · 버튼은 3분간 유효')
    await interaction.response.send_message(embed=embed, view=ApplyView(interaction.user.id, fingerprint(items)), ephemeral=True)


@client.tree.command(name='출석', description='하루 한 번 출석하고 눈꽃을 받습니다', guild=scope)
async def attendance(interaction: discord.Interaction):
    result = store.attend(interaction.guild_id, interaction.user.id)
    if result is None:
        text = '오늘은 이미 출석했어요. 한국 시간 자정 이후 다시 만나요.'
    else:
        reward, streak = result
        text = f'❄ 출석 완료! 눈꽃 +{reward}\n연속 출석 {streak}일'
    await interaction.response.send_message(text, ephemeral=True)


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
    await interaction.response.send_message('설온 봇 v0.1\n사용 가능: /디자인 /출석 /여권 /구조기록\n'
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


if __name__ == '__main__':
    if OWNER_ID <= 0:
        raise SystemExit('OWNER_ID 환경변수에 본인의 Discord 사용자 ID를 설정하세요. 설정 전에는 누구도 명령어를 실행할 수 없습니다.')
    token = os.environ.get('DISCORD_TOKEN', '').strip()
    if not token:
        raise SystemExit('DISCORD_TOKEN 환경변수에 봇 토큰을 설정하세요. 채팅에 보내지 마세요.')
    client.run(token)
