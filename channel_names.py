import discord

from core import PROTECTED_CHANNEL_IDS


MOBILE_PREFIX_VERSION = 1
# These protected IDs already have explicit owner-approved rename exceptions.
RENAME_ALLOWED_PROTECTED = frozenset({
    1523604140108939285,  # 관리자채팅
    1523611752837681192,  # 입장로그
    1522946795217490141,  # 음악
})


async def setup_mobile_channel_names(guild, store, logger):
    """Rename only the invisible leading glyph; never recreate/move channels."""
    if store.get('channels:mobile-prefix') == MOBILE_PREFIX_VERSION:
        return
    changed = []
    channels = await guild.fetch_channels()
    for channel in channels:
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            continue
        if not channel.name.startswith('﹒'):
            continue
        if (channel.id in PROTECTED_CHANNEL_IDS or channel.category_id in PROTECTED_CHANNEL_IDS) and channel.id not in RENAME_ALLOWED_PROTECTED:
            logger.warning('모바일 기호 변경 제외 · 보호 채널 %s (%s)', channel.id, channel.name)
            continue
        new_name = '┊' + channel.name[1:]
        await channel.edit(name=new_name, reason='소유자 요청: 모바일에서도 보이는 채널 구분 기호로 개선')
        changed.append((channel.id, channel.name, new_name))
    fresh = {c.id: c for c in await guild.fetch_channels()}
    if any(fresh[cid].name != new for cid, _old, new in changed):
        raise RuntimeError('모바일 채널 기호 변경 확인에 실패했습니다.')
    store.put('channels:mobile-prefix', MOBILE_PREFIX_VERSION)
    logger.warning('모바일 채널 기호 개선 완료 · ﹒ → ┊ · 기존 ID/메시지/권한 유지 · %s개', len(changed))
