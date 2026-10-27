import io
import json
from datetime import datetime

import discord

from core import LAYOUT, VOICE_NAMES, PROTECTED_CHANNEL_IDS
from rebuild import reset_targets, keep_ids, inventory_signature, guarded_delete


class ResetView(discord.ui.View):
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

    @discord.ui.button(label='보호 채널 외 삭제 후 새로 만들기', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.require_admin(interaction)
        if interaction.user.id != self.owner:
            await interaction.response.send_message('초기화 목록을 연 소유자만 실행할 수 있습니다.', ephemeral=True)
            return
        if self.used or self.lock.locked():
            await interaction.response.send_message('이미 실행 중이거나 사용한 버튼입니다.', ephemeral=True)
            return
        self.used = True
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.lock:
            made = 0
            deleted = 0
            try:
                guild = interaction.guild
                original = await guild.fetch_channels()
                if inventory_signature(original) != self.signature:
                    raise ValueError('미리보기 이후 채널이나 권한이 바뀌었습니다. /초기화를 다시 실행하세요.')
                targets = reset_targets(original)
                kept = keep_ids(original)
                new_count = sum(1 + len(channels) for _, _, channels in LAYOUT) + 3
                if len(original) + new_count > 500:
                    raise ValueError('새 채널을 먼저 준비할 여유가 없습니다. 채널 수 500개 제한 때문에 삭제하지 않고 중단합니다.')
                if not guild.me.guild_permissions.administrator:
                    raise ValueError('초기화에는 설온지기의 서버 관리자 권한이 필요합니다.')
                self.data.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
                path = self.data / f'reset-structure-{stamp}.json'
                path.write_text(json.dumps({'kept_ids': sorted(kept), 'delete_ids': [c.id for c in targets],
                                            'structure': inventory_signature(original)},
                                           ensure_ascii=False, indent=2), encoding='utf-8')
                # Create the complete replacement before deleting any existing channel.
                # All deletions below use the frozen original IDs, never the new ones.
                fresh = {}
                for name, _, channels in LAYOUT:
                    category = await guild.create_category(name, reason='설온 초기화: 새 디자인 준비')
                    made += 1
                    self.store.put('category:' + name, category.id)
                    for cname, _ in channels:
                        voice = cname in VOICE_NAMES
                        kwargs = {'category': category, 'reason': '설온 초기화: 새 채널 준비'}
                        if cname in {'┊시작하기', '┊공지사항', '┊이용규칙', '┊이벤트'}:
                            kwargs['overwrites'] = {
                                guild.default_role: discord.PermissionOverwrite(send_messages=False),
                                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)}
                        method = guild.create_voice_channel if voice else guild.create_text_channel
                        channel = await method(cname, **kwargs)
                        fresh[cname] = channel
                        made += 1
                        self.store.put(('voice:' if voice else 'text:') + cname, channel.id)
                ops = await guild.create_category('── ⟡ 설온 운영실 ⟡ ──', overwrites={
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                    interaction.user: discord.PermissionOverwrite(view_channel=True),
                    guild.me: discord.PermissionOverwrite(view_channel=True)}, reason='설온 초기화: 소유자 전용 운영실')
                made += 1
                for name in ['┊운영회의', '┊관리기록']:
                    fresh[name] = await guild.create_text_channel(name, category=ops, reason='설온 초기화: 운영 공간 준비')
                    made += 1
                # Community servers cannot delete the active rules/updates channel.
                # Repoint only settings that refer to explicitly confirmed deletion targets.
                deleting = {c.id for c in targets}
                updates = {}
                for attr, destination in [('rules_channel', '┊이용규칙'), ('public_updates_channel', '┊관리기록'),
                                          ('system_channel', '┊시작하기'), ('afk_channel', '┊자리비움'),
                                          ('safety_alerts_channel', '┊관리기록')]:
                    current = getattr(guild, attr, None)
                    if current is not None and current.id in deleting:
                        updates[attr] = fresh[destination]
                if updates:
                    await guild.edit(**updates, reason='초기화 삭제 대상에 연결된 서버 채널 설정 이전')
                await interaction.followup.send(
                    f'새 디자인 {made}개 항목 준비 완료. 확인한 기존 채널 {len(targets)}개를 삭제합니다.', ephemeral=True)
                for channel in targets:
                    if isinstance(channel, discord.CategoryChannel):
                        # A pinned channel might have been moved by another admin during the run.
                        current_channels = await guild.fetch_channels()
                        live_keep = keep_ids(current_channels)
                    else:
                        live_keep = kept
                    await guarded_delete(channel, kept | live_keep)
                    deleted += 1
                # A reset must not be reported as complete if any original target survived.
                remaining = await guild.fetch_channels()
                remaining_ids = {c.id for c in remaining}
                if deleting & remaining_ids:
                    raise RuntimeError('일부 기존 항목이 남아 있습니다. 구조기록을 확인하세요.')
                if not PROTECTED_CHANNEL_IDS <= remaining_ids:
                    raise RuntimeError('보호 채널 확인에 실패했습니다. 추가 작업을 중단합니다.')
                old_pinned = [c for c in original if c.id in PROTECTED_CHANNEL_IDS]
                new_pinned = [c for c in remaining if c.id in PROTECTED_CHANNEL_IDS]
                if inventory_signature(old_pinned) != inventory_signature(new_pinned):
                    raise RuntimeError('보호 채널의 속성이 외부에서 바뀌었습니다. 추가 작업을 중단합니다.')
                await interaction.followup.send(
                    f'초기화 완료 · 기존 항목 {deleted}개 삭제 · 새 항목 {made}개 생성\n'
                    '보호 채널 4개와 해당 카테고리는 유지했습니다. 회원·역할·출석·눈꽃 잔액도 유지합니다.\n'
                    '자동 음성방 생성과 상점 교환은 아직 구현되지 않았습니다.', ephemeral=True)
            except Exception as exc:
                self.logger.exception('Reset stopped')
                await interaction.followup.send(
                    f'초기화를 중단했습니다: {type(exc).__name__}: {str(exc)[:700]}\n'
                    f'새 항목 {made}개 생성, 기존 항목 {deleted}개 삭제까지 진행됐습니다.\n'
                    '삭제한 메시지는 복원되지 않습니다. /구조기록으로 현재 상태를 확인하세요.', ephemeral=True)
        self.stop()

    async def on_error(self, interaction, error, item):
        self.logger.error('Reset button error: %s', type(error).__name__)
        text = '지정한 소유자 계정과 관리자 권한을 확인하세요.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


async def show_reset(interaction, store, data, lock, require_admin, logger):
    require_admin(interaction)
    await interaction.response.defer(ephemeral=True, thinking=True)
    channels = await interaction.guild.fetch_channels()
    kept = keep_ids(channels)
    if interaction.channel_id not in kept:
        raise ValueError('삭제 중에도 결과를 받을 수 있도록, 지정한 보호 채널 중 문자 채널에서 /초기화를 실행하세요.')
    targets = reset_targets(channels)
    rows = ['설온 초기화: 삭제한 메시지·사진·파일은 복구할 수 없습니다.', '', '유지 항목']
    rows.extend(f'{c.name} ({c.id})' for c in channels if c.id in kept)
    rows.extend(['', f'삭제 항목 {len(targets)}개'])
    rows.extend(f'{c.name} ({c.id}) · {c.type}' for c in targets)
    rows.extend(['', '새 디자인'])
    for name, _, entries in LAYOUT:
        rows.append(name)
        rows.extend('  ' + n for n, _ in entries)
    rows.extend(['── ⟡ 설온 운영실 ⟡ ──', '  ┊운영회의', '  ┊관리기록', '',
                 '기존 회원·역할·출석·눈꽃 잔액은 삭제하지 않습니다.',
                 '커뮤니티 규칙·업데이트 등 서버 채널 연결 설정은 삭제 대상인 경우 새 채널로 이전합니다.'])
    file = discord.File(io.BytesIO('\n'.join(rows).encode('utf-8')), filename='seolon-reset-targets.txt')
    embed = discord.Embed(title='설온 · 전체 재구성 최종 확인', color=0xD39BA6,
                          description=f'보호 채널 4개와 그 카테고리를 제외한 기존 항목 **{len(targets)}개**를 삭제합니다.\n'
                          '기존 대화·사진·파일은 복구할 수 없습니다.\n첨부 파일의 실제 삭제 목록을 확인하세요.\n'
                          '새 채널 준비가 완료된 뒤 기존 항목을 삭제합니다. 버튼은 3분간 유효합니다.')
    await interaction.followup.send(embed=embed, file=file,
                                    view=ResetView(interaction.user.id, inventory_signature(channels),
                                                   store, data, lock, require_admin, logger), ephemeral=True)
