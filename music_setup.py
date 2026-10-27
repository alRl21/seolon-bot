import discord
from membership import CLAN_ROLE_ID, view_overwrites

MUSIC_CHANNEL_ID = 1522946795217490141
UTILITY_CATEGORY = '── ⟡ 봇 ⟡ ──'

async def setup_music_channel(guild, owner_id, store, logger):
    if store.get('music:configured') == 2:
        return
    channels = await guild.fetch_channels()
    channel = next((c for c in channels if c.id == MUSIC_CHANNEL_ID), None)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError('기존 음악 채널을 확인할 수 없습니다.')
    owner = await guild.fetch_member(owner_id)
    clan = guild.get_role(CLAN_ROLE_ID)
    if clan is None:
        raise ValueError('클랜원 역할을 확인할 수 없습니다.')
    categories = [c for c in channels if isinstance(c, discord.CategoryChannel) and c.name == UTILITY_CATEGORY]
    if len(categories) > 1:
        raise ValueError('봇과 유틸리티 카테고리가 중복되어 있습니다.')
    category = categories[0] if categories else await guild.create_category(UTILITY_CATEGORY,
        overwrites=view_overwrites({}, guild.default_role, clan, owner, guild.me, 'main'),
        reason='소유자가 요청한 봇·유틸리티 공간 생성')
    store.put('utility:category', category.id)
    overwrites = view_overwrites(channel.overwrites, guild.default_role, clan, owner, guild.me, 'main')
    for target in [clan, owner, guild.me]:
        overwrites[target].send_messages = True
        overwrites[target].read_message_history = True
        overwrites[target].use_application_commands = True
    # Preserve existing integration-role access and restore the installed Haribo
    # role previously hidden by the administrator-only channel configuration.
    for role in guild.roles:
        if role.managed and ('하리보' in role.name or 'haribo' in role.name.lower()):
            value = overwrites.setdefault(role, discord.PermissionOverwrite())
            value.view_channel = value.send_messages = value.read_message_history = True
            value.embed_links = value.attach_files = True
    tts = next((c for c in channels if c.id == store.get('utility:tts')), None)
    if tts is None:
        matches = [c for c in channels if isinstance(c, discord.TextChannel) and c.category_id == category.id and c.name == '┊tts']
        if len(matches) > 1:
            raise ValueError('TTS 채널이 중복되어 있습니다.')
        tts = matches[0] if matches else await guild.create_text_channel('┊tts', category=category,
            overwrites=view_overwrites({}, guild.default_role, clan, owner, guild.me, 'main'),
            topic='음성방에서 마이크 대신 전달할 말을 적는 TTS 전용 채널',
            reason='소유자 요청: 사용하기 쉬운 TTS 공간 준비')
        store.put('utility:tts', tts.id)
    tts_permissions = view_overwrites(tts.overwrites, guild.default_role, clan, owner, guild.me, 'main')
    for target in [clan, owner, guild.me]:
        value = tts_permissions[target]
        value.send_messages = value.read_message_history = value.use_application_commands = True
    await tts.edit(overwrites=tts_permissions)
    command_channel = next((c for c in channels if isinstance(c, discord.TextChannel) and c.id == store.get('text:┊봇명령어')), None)
    if command_channel:
        await command_channel.edit(name='┊명령어', category=category, sync_permissions=False,
                                   reason='소유자 요청: 봇 명령 채널도 유틸리티 공간으로 정리')
    old_name = channel.name
    await channel.edit(name='┊음악', category=category, sync_permissions=False, overwrites=overwrites,
                       reason='소유자 요청: 기존 하리보 음악 채널을 봇·유틸리티 카테고리로 이동하고 클랜원 사용 허용')
    fresh = next(c for c in await guild.fetch_channels() if c.id == MUSIC_CHANNEL_ID)
    if (fresh.category_id != category.id or fresh.name != '┊음악' or
            fresh.overwrites_for(clan).view_channel is not True or
            fresh.overwrites_for(clan).send_messages is not True or
            fresh.overwrites_for(guild.default_role).view_channel is not False):
        raise RuntimeError('음악 채널 이동·접근권한 검증 실패')
    store.put('music:configured', 2)
    store.put('entry:gated:' + str(tts.id), tts.id)
    logger.warning('하리보 음악 채널 정리 완료: %s (%s) → %s · 클랜원 채팅/앱 명령 허용 · ID/메시지 유지',
                   fresh.name, fresh.id, category.name)


async def setup_tts_permissions(guild, store, logger):
    roles = [r for r in guild.roles if r.managed and getattr(r.tags, 'bot_id', None) == 1019061779357245521]
    if not roles:
        logger.warning('카미봇 초대 대기: 채널 준비 완료, 외부 봇은 아직 설치되지 않았습니다.')
        return
    channels = await guild.fetch_channels()
    targets = [c for c in channels if c.id == store.get('utility:tts') or
               (isinstance(c, discord.VoiceChannel) and c.category and c.category.name == '── ⟡ 목소리의 온도 ⟡ ──')]
    for channel in targets:
        for role in roles:
            value = channel.overwrites_for(role)
            value.view_channel = value.send_messages = value.read_message_history = True
            if isinstance(channel, discord.VoiceChannel):
                value.connect = value.speak = True
            await channel.set_permissions(role, overwrite=value, reason='소유자 요청: 카미봇 TTS 전용 채널·음성방 접근 허용')
    logger.warning('카미봇 TTS 접근권한 적용: %s개 텍스트/음성 채널 · 실제 읽기 연결은 /tts로 시작', len(targets))
