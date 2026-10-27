import discord
from core import GUILD_ID, PROTECTED_CHANNEL_IDS
from membership import CLAN_ROLE_ID
from channel_policy import readonly_overwrites


def eligible(interaction):
    return (interaction.guild_id == GUILD_ID and not interaction.user.bot
            and any(r.id == CLAN_ROLE_ID for r in getattr(interaction.user, 'roles', [])))


class AttendanceView(discord.ui.View):
    def __init__(self, store):
        super().__init__(timeout=None)
        self.store = store

    async def interaction_check(self, interaction):
        if getattr(interaction.client, 'closing_for_backup', False) is True:
            await interaction.response.send_message('종료 전 백업 중이에요. 내일 오전 7시 이후 출석해 주세요.', ephemeral=True)
            return False
        if not eligible(interaction):
            await interaction.response.send_message('클랜원 역할을 받은 회원만 사용할 수 있어요.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='오늘 출석하기', style=discord.ButtonStyle.primary, custom_id='seolon:attendance:v1')
    async def attend(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        result = self.store.attend(interaction.guild_id, interaction.user.id)
        balance, days = self.store.profile(interaction.guild_id, interaction.user.id)
        if result is None:
            text = f'오늘은 이미 출석했어요.\n보유 눈송이 {balance:,}개\n다음 출석은 내일 오전 7시부터 가능해요.'
        else:
            reward, streak = result
            text = (f'⟡ 오늘의 출석 완료\n눈송이 +{reward:,}개\n'
                    f'연속 출석 {streak}일 · 누적 출석 {days}일\n보유 눈송이 {balance:,}개')
        await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label='내 눈송이', style=discord.ButtonStyle.secondary, custom_id='seolon:wallet:v1')
    async def wallet(self, interaction, button):
        balance, days = self.store.profile(interaction.guild_id, interaction.user.id)
        streak, today = self.store.attendance_status(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title='⟡ 나의 눈송이 기록', colour=0xB8C8D8)
        embed.add_field(name='보유 눈송이', value=f'{balance:,}개')
        embed.add_field(name='누적 출석', value=f'{days}일')
        embed.add_field(name='연속 출석', value=f'{streak}일')
        embed.add_field(name='오늘 출석', value='완료' if today else '아직 출석하지 않았어요', inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(self, interaction, error, item):
        import logging
        logging.getLogger('seolon').exception('출석 버튼 처리 실패', exc_info=error)
        text = '처리 중 문제가 생겼어요. 잠시 후 다시 눌러 주세요. 출석 보상은 중복 지급되지 않아요.'
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


async def setup_attendance(guild, owner_id, store, logger):
    if store.get('attendance:panel-version') == 1:
        return
    channels = await guild.fetch_channels()
    cid = store.get('text:﹒출석체크')
    choices = [c for c in channels if isinstance(c, discord.TextChannel) and (c.id == cid if cid else c.name == '﹒출석체크')]
    if len(choices) != 1:
        raise ValueError('출석체크 채널을 하나로 확인할 수 없습니다.')
    channel = choices[0]
    if channel.id in PROTECTED_CHANNEL_IDS or channel.category_id in PROTECTED_CHANNEL_IDS:
        raise ValueError('보호 채널은 출석 채널로 변경하지 않습니다.')
    owner = await guild.fetch_member(owner_id)
    role = guild.get_role(CLAN_ROLE_ID)
    if role is None:
        raise ValueError('클랜원 역할이 없습니다.')
    expected = readonly_overwrites(channel.overwrites, guild.default_role, role, owner, guild.me)
    await channel.edit(overwrites=expected, reason='출석은 버튼으로 이용하고 채널은 안내 전용으로 설정')
    embed = discord.Embed(title='── ⟡ 설온의 출석 기록 ⟡ ──', colour=0xB8C8D8,
        description='하루 한 번, 설온에 발자국을 남겨 주세요.\n출석하면 재화 **눈송이**를 받을 수 있어요.')
    embed.add_field(name='매일의 선물', value='출석할 때마다 눈송이 30개', inline=False)
    embed.add_field(name='꾸준함의 선물', value='연속 7일마다 +100개\n연속 30일마다 +500개\n보너스는 기본 보상에 추가되며, 겹치는 날에는 모두 지급돼요.', inline=False)
    embed.add_field(name='출석 안내', value='한국 시간 기준 하루 한 번 · 매일 자정 초기화\n봇 운영 시간 오전 7시~밤 12시\n하루를 빠지면 연속 기록은 다시 1일부터 시작해요.\n보유 눈송이와 누적 출석은 유지돼요.', inline=False)
    message_id = store.get('attendance:panel')
    message = None
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            pass
    if message:
        await message.edit(embed=embed, view=AttendanceView(store))
    else:
        message = await channel.send(embed=embed, view=AttendanceView(store))
        store.put('attendance:panel', message.id)
    fresh = next(c for c in await guild.fetch_channels() if c.id == channel.id)
    for target, value in expected.items():
        if fresh.overwrites_for(target).pair() != value.pair():
            raise RuntimeError('출석 채널 권한 확인 실패')
    store.put('attendance:panel-version', 1)
    logger.warning('눈송이 출석 시스템 적용 완료: 채널 %s · 패널 %s · 클랜원 역할 %s · 매일30/7일100/30일500', channel.id, message.id, role.id)
