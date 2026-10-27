import discord
from core import PROTECTED_CHANNEL_IDS
from membership import CLAN_ROLE_ID

NQN_ID=559426966151757824
CHAT_NAMES=('﹒도란도란','﹒사진과기록','﹒취향공유','﹒파티모집','﹒게임이야기','﹒봇명령어')
BOT_PERMISSIONS=('view_channel','send_messages','read_message_history','manage_messages',
                 'embed_links','attach_files','external_emojis','manage_webhooks','send_messages_in_threads')


def selected_channels(channels,store):
    ids={store.get('text:'+name) for name in CHAT_NAMES}
    return [c for c in channels if isinstance(c,discord.TextChannel) and c.id in ids
            and c.id not in PROTECTED_CHANNEL_IDS and c.category_id not in PROTECTED_CHANNEL_IDS]


async def setup_emoji_permissions(guild,store,logger):
    if store.get('emoji:nqn-configured')==1:return
    roles=await guild.fetch_roles()
    bot_roles=[r for r in roles if r.managed and getattr(r.tags,'bot_id',None)==NQN_ID]
    if len(bot_roles)!=1:
        logger.warning('NQN 초대 대기 · 정확한 봇 연동 역할을 아직 확인하지 못했습니다.')
        return
    member=await guild.fetch_member(NQN_ID)
    if not member.bot or member.guild_permissions.administrator:
        raise ValueError('NQN에 관리자 권한이 있으면 채널별 제한이 불가능합니다. NQN의 관리자 권한을 해제해야 합니다.')
    bot_role=bot_roles[0]
    clan=guild.get_role(CLAN_ROLE_ID)
    if clan is None:raise ValueError('클랜원 역할을 찾을 수 없습니다.')
    channels=await guild.fetch_channels()
    targets=selected_channels(channels,store)
    if len(targets)!=len(CHAT_NAMES):
        raise ValueError('NQN을 사용할 대화/명령어 채널 6개를 모두 확인하지 못했습니다.')
    for channel in targets:
        if not channel.permissions_for(clan).view_channel or not channel.permissions_for(clan).send_messages:
            raise ValueError('클랜원 대화용 채널이 아닌 항목이 있어 이모지 설정을 중단합니다.')
    expected={}
    for channel in targets:
        overwrites=channel.overwrites
        bot_value=overwrites.setdefault(bot_role,discord.PermissionOverwrite())
        for permission in BOT_PERMISSIONS:setattr(bot_value,permission,True)
        user_value=overwrites.setdefault(clan,discord.PermissionOverwrite())
        user_value.external_emojis=True
        user_value.embed_links=True
        user_value.attach_files=True
        await channel.edit(overwrites=overwrites,reason='소유자 요청: NQN 이모지 재전송을 일반 대화·봇 명령 채널에 허용')
        expected[channel.id]=overwrites
    fresh={c.id:c for c in await guild.fetch_channels()}
    for cid,wanted in expected.items():
        for target,permission in wanted.items():
            if fresh[cid].overwrites_for(target).pair()!=permission.pair():
                raise RuntimeError('NQN 채널 접근권한 검증 실패')
    store.put('emoji:nqn-configured',1)
    logger.warning('서버 등록 이모지 수: %s · 사용 가능 목록은 NQN /my_emotes',len(guild.emojis))
    logger.warning('NQN 이모지 권한 설정 완료 · 봇 ID %s · 채널: %s · 공지/입장/문의/관리자/음악/TTS 채널 수정 없음',
                   NQN_ID,', '.join(c.name for c in targets))
