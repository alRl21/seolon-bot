import discord
from core import PROTECTED_CHANNEL_IDS

READONLY_NAMES = frozenset({'﹒시작하기', '﹒공지사항', '﹒이용규칙', '﹒역할선택',
                            '﹒역할받기', '﹒입장로그', '﹒이벤트', '﹒눈꽃상점'})
WRITE_PERMISSIONS = ('send_messages', 'send_messages_in_threads', 'create_public_threads',
                     'create_private_threads', 'send_voice_messages', 'send_polls')
POLICY_VERSION = 2026102701
ENTRY_LOG_ID = 1523611752837681192


def readonly_overwrites(existing, everyone, clan, owner, bot):
    result = {target: discord.PermissionOverwrite.from_pair(*value.pair()) for target, value in existing.items()}
    # Role allows take precedence over role denies: deny every ordinary override,
    # while retaining administrator writers and explicit owner/bot access.
    for target in set(result) | {everyone, clan, owner, bot}:
        value = result.setdefault(target, discord.PermissionOverwrite())
        admin = getattr(getattr(target, 'permissions', None), 'administrator', False) is True
        writer = target.id in {owner.id, bot.id} or admin
        for permission in WRITE_PERMISSIONS:
            setattr(value, permission, writer)
    return result


async def setup_channel_policy(guild, owner_id, store, logger):
    if store.get('channels:readonly-policy') == POLICY_VERSION:
        return
    owner = await guild.fetch_member(owner_id)
    clan = guild.get_role(1522946198984593541)
    if clan is None:
        raise ValueError('지정한 클랜원 역할을 찾을 수 없습니다.')
    channels = await guild.fetch_channels()
    selected = [c for c in channels if isinstance(c, discord.TextChannel)
                and c.name in READONLY_NAMES
                and (c.id not in PROTECTED_CHANNEL_IDS or c.id == ENTRY_LOG_ID)
                and (c.category_id not in PROTECTED_CHANNEL_IDS)]
    expected = {}
    for c in selected:
        overwrite = readonly_overwrites(c.overwrites, guild.default_role, clan, owner, guild.me)
        expected[c.id] = overwrite
        await c.edit(overwrites=overwrite, reason='소유자 요청: 공지·안내·역할 채널은 관리자만 작성')
    fresh = {c.id: c for c in await guild.fetch_channels()}
    for cid, wanted in expected.items():
        c = fresh[cid]
        for target, permission in wanted.items():
            actual = c.overwrites_for(target)
            if actual.pair() != permission.pair():
                raise RuntimeError(f'{cid}: 읽기 전용 권한 검증 실패')
    store.put('channels:readonly-policy', POLICY_VERSION)
    logger.warning('읽기 전용 채널 설정 완료: %s · 일반 메시지/스레드/음성메시지/투표 작성 차단 · 보기 권한 유지',
                   ', '.join(c.name for c in selected))
