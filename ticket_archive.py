import io
import json
import zipfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import discord
from core import PROTECTED_CHANNEL_IDS


async def archive_ticket(channel, tid, user_id, actor, store, destination):
    if channel.id in PROTECTED_CHANNEL_IDS or channel.category_id in PROTECTED_CHANNEL_IDS:
        raise ValueError('보호 채널은 문의 종료로 삭제하지 않습니다.')
    if channel.topic != f'seolon-ticket:{tid}:{user_id}' or channel.category_id != store.get('support:category'):
        raise ValueError('등록된 문의 채널의 ID·분류를 확인할 수 없습니다.')
    messages = []
    async for message in channel.history(limit=None, oldest_first=True):
        messages.append(message)
        if len(messages) > 2500:
            raise ValueError('대화가 길어 자동 보관 한도를 넘었습니다. 원본 채널을 유지합니다.')
    if (channel.guild._state._intents.message_content is not True and any(not m.author.bot for m in messages)):
        raise ValueError('Message Content Intent가 꺼져 있어 대화 전체를 보관할 수 없습니다. 원본 채널을 유지합니다.')
    # Discord withholds member text without Message Content access. Never delete
    # a conversation on the basis of an incomplete transcript.
    for m in messages:
        if (not m.author.bot and not m.content and not m.attachments and not m.embeds
                and m.type == discord.MessageType.default):
            raise ValueError('대화 내용을 읽을 수 없어 채널을 보존했습니다. Developer Portal에서 설온지기의 Message Content Intent를 켜야 합니다.')
    limit = min(getattr(channel.guild,'filesize_limit',8_000_000), 8_000_000) - 100_000
    rows = []
    attachment_data = []
    total_attachment_bytes = 0
    for m in messages:
        rows.append({'id':m.id,'author_id':m.author.id,'author':str(m.author),'at':m.created_at.isoformat(),
                     'content':m.content,'embeds':[e.to_dict() for e in m.embeds],
                     'attachments':[{'id':a.id,'filename':a.filename,'size':a.size} for a in m.attachments]})
        for a in m.attachments:
            if a.size > limit:
                raise ValueError('첨부파일이 보관 한도를 넘어 문의 채널을 유지했습니다. 파일을 별도로 보관한 뒤 처리해야 합니다.')
            total_attachment_bytes += a.size
            if total_attachment_bytes > 32_000_000:
                raise ValueError('첨부파일 총량이 자동 보관 한도를 넘어 원본 채널을 유지합니다.')
            data = await a.read()
            attachment_data.append((f'attachments/{a.id}-{Path(a.filename).name}',data))
    payload = json.dumps({'ticket':tid,'user':user_id,'closed_by':actor,'messages':rows},ensure_ascii=False,indent=2).encode()
    text = '\n\n'.join(f"[{r['at']}] {r['author']} ({r['author_id']})\n{r['content']}\n" +
                       '\n'.join(e.get('title','')+'\n'+e.get('description','') for e in r['embeds']) for r in rows).encode()
    entries = [('conversation.json',payload),('conversation.txt',text)] + attachment_data
    # Partition into individually verifiable uploads. Original attachment bytes
    # are copied, rather than relying on links that expire after channel deletion.
    parts=[]
    current=[]
    size=0
    for name,data in entries:
        if len(data)>limit:
            raise ValueError('대화 기록이 보관 한도를 넘어서 채널을 유지했습니다.')
        if current and size+len(data)>limit:
            parts.append(current); current=[]; size=0
        current.append((name,data)); size+=len(data)
    if current: parts.append(current)
    folder=store.path.parent / 'ticket-archives'
    folder.mkdir(parents=True,exist_ok=True)
    links=[]
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    for index,entries in enumerate(parts,1):
        path=folder/f'ticket-{tid}-{now:%Y%m%d-%H%M%S-%f}-{index}.zip'
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name,data in entries: z.writestr(name,data)
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                raise RuntimeError('대화 기록 파일 검증 실패. 채널을 보존합니다.')
        result=await destination.send(f'문의 #{tid:04d} 종료 기록 · 작성자 ID {user_id} · 처리자 ID {actor}\n보관 파일 {index}/{len(parts)}',
                                      file=discord.File(path),allowed_mentions=discord.AllowedMentions.none())
        if len(result.attachments)!=1:
            raise RuntimeError('관리자 채널에 기록 파일이 저장되지 않아 삭제하지 않았습니다.')
        links.append(result.jump_url)
    latest=[m async for m in channel.history(limit=1)]
    if latest and (not messages or latest[0].id != messages[-1].id):
        raise ValueError('보관 중 새 메시지가 있어 채널을 유지했습니다. 종료 버튼을 다시 눌러 주세요.')
    with store.connect() as db:
        db.execute('INSERT OR REPLACE INTO ticket_archives(ticket,channel,actor,created_at,links) VALUES (?,?,?,?,?)',
                   (tid,channel.id,actor,now.isoformat(),json.dumps(links)))
    return links
