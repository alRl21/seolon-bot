"""Explicit, owner-confirmed reset. Never invoked by ordinary design updates."""
from core import PROTECTED_CHANNEL_IDS
import discord


def keep_ids(channels):
    """Protect pinned channels, children of pinned categories, and their parents."""
    keep = set(PROTECTED_CHANNEL_IDS)
    for channel in channels:
        if getattr(channel, 'category_id', None) in PROTECTED_CHANNEL_IDS:
            keep.add(channel.id)
    for channel in channels:
        if channel.id in keep and getattr(channel, 'category_id', None):
            keep.add(channel.category_id)
    return keep


def reset_targets(channels):
    ids = {c.id for c in channels}
    missing = PROTECTED_CHANNEL_IDS - ids
    if missing:
        raise ValueError('보호 채널을 모두 확인할 수 없어 초기화를 중단합니다: ' + ', '.join(map(str, sorted(missing))))
    keep = keep_ids(channels)
    return sorted([c for c in channels if c.id not in keep],
                  key=lambda c: (isinstance(c, discord.CategoryChannel), c.id))


def inventory_signature(channels):
    return sorted((c.id, c.name, str(c.type), getattr(c, 'category_id', None),
                   tuple(sorted((t.id, *[p.value for p in v.pair()]) for t, v in c.overwrites.items())))
                  for c in channels)


async def guarded_delete(channel, kept):
    if channel.id in kept or channel.id in PROTECTED_CHANNEL_IDS:
        raise ValueError(f'보호 항목 {channel.id} 삭제를 차단했습니다.')
    if getattr(channel, 'category_id', None) in PROTECTED_CHANNEL_IDS:
        raise ValueError('보호 카테고리의 하위 채널 삭제를 차단했습니다.')
    await channel.delete(reason='설온 소유자가 목록 확인 후 승인한 채널 초기화')
