import discord

from core import LAYOUT
from membership import ADMIN_PROTECTED_IDS, OPS_CATEGORY

ORDER_REQUEST = 2026102702


def category_order(channels, store):
    indexed = {c.id: c for c in channels}
    if not ADMIN_PROTECTED_IDS <= indexed.keys():
        raise ValueError('관리자 전용 보호 채널 3개를 모두 확인할 수 없습니다.')
    categories = sorted([c for c in channels if isinstance(c, discord.CategoryChannel)], key=lambda c: (c.position, c.id))
    by_id = {c.id: c for c in categories}
    anchor_ids = set()
    for cid in ADMIN_PROTECTED_IDS:
        c = indexed[cid]
        parent = getattr(c, 'category_id', None)
        if cid in by_id:
            anchor_ids.add(cid)
        elif parent is not None:
            anchor_ids.add(parent)
    entry_id = store.get('entry:category')
    if entry_id not in by_id:
        raise ValueError('입장 공간 카테고리를 확인할 수 없습니다.')
    if entry_id in anchor_ids:
        raise ValueError('관리자 전용 보호 채널이 입장 공간에 있습니다. 보호 채널은 이동하지 않고 정렬을 중단합니다.')
    block_ids = [entry_id]
    block_ids += [store.get('category:' + name) for name, _, _ in LAYOUT]
    block_ids += [c.id for c in categories if c.name == OPS_CATEGORY]
    block = []
    seen = set()
    for cid in block_ids:
        if cid in by_id and cid not in anchor_ids and cid not in seen:
            block.append(by_id[cid])
            seen.add(cid)
    remaining = [c for c in categories if c.id not in seen]
    # Uncategorized protected channels are always displayed above categories by Discord.
    insert_at = max((i + 1 for i, c in enumerate(remaining) if c.id in anchor_ids), default=0)
    return remaining[:insert_at] + block + remaining[insert_at:]


async def arrange_categories(guild, store, http, logger, force=False):
    if not force and store.get('order:completed') == ORDER_REQUEST:
        return
    if not store.get('entry:active'):
        return
    channels = await guild.fetch_channels()
    desired = category_order(channels, store)
    current = sorted([c for c in channels if isinstance(c, discord.CategoryChannel)], key=lambda c: (c.position, c.id))
    if [c.id for c in current] != [c.id for c in desired]:
        # The same Discord bulk-position endpoint used by CategoryChannel.move.
        # One atomic position-only update avoids sequential moves racing the gateway cache.
        await http.bulk_channel_update(guild.id, [{'id': c.id, 'position': i} for i, c in enumerate(desired)],
                                       reason='소유자 요청: 보호 구역 바로 아래 입장 공간과 본 채널 순서 정렬')
    after = await guild.fetch_channels()
    actual = sorted([c for c in after if isinstance(c, discord.CategoryChannel)], key=lambda c: (c.position, c.id))
    if [c.id for c in actual] != [c.id for c in desired]:
        raise RuntimeError('카테고리 순서 확인에 실패했습니다. /채널정렬로 다시 확인할 수 있습니다.')
    store.put('order:completed', ORDER_REQUEST)
    logger.warning('채널 순서 정렬 완료: %s', ' → '.join(c.name for c in actual))
