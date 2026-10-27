import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import discord
from core import Store, GUILD_ID
from operations_store import reserve_ticket, activate_ticket
from operations_ui import CloseTicketView
from ticket_archive import archive_ticket
from schedule import nightly_backup_due, KST

class ArchiveTests(unittest.IsolatedAsyncioTestCase):
    def scenario(self, folder):
        store=Store(Path(folder)/'db')
        store.put('support:category',100)
        store.put('support:log',200)
        tid=reserve_ticket(store,GUILD_ID,2)
        activate_ticket(store,tid,500)
        attachment=SimpleNamespace(id=12,filename='proof.txt',size=8,read=AsyncMock(return_value=b'evidence'))
        message=SimpleNamespace(id=501,author=SimpleNamespace(id=2,bot=False),content='문의 본문',
            attachments=[attachment],embeds=[],created_at=datetime.now(timezone.utc),type=discord.MessageType.default)
        channel=Mock(spec=discord.TextChannel)
        channel.id,channel.category_id,channel.topic=500,100,f'seolon-ticket:{tid}:2'
        channel.overwrites={}
        channel.edit=AsyncMock()
        channel.delete=AsyncMock()
        async def history(**kwargs):
            yield message
        channel.history=history
        guild=Mock()
        guild.filesize_limit=8_000_000
        guild._state._intents.message_content=True
        guild.me.id=999
        channel.guild=guild
        dest=Mock()
        dest.overwrites={}
        dest.overwrites_for.return_value=discord.PermissionOverwrite(view_channel=False)
        async def send(*args,**kwargs):
            with zipfile.ZipFile(kwargs['file'].fp) as z:
                self.assertEqual(z.read('attachments/12-proof.txt'),b'evidence')
                self.assertIn('문의 본문',z.read('conversation.txt').decode())
            channel.delete.assert_not_awaited()
            return SimpleNamespace(attachments=[1],jump_url='https://discord.com/channels/1/2/3')
        dest.send=AsyncMock(side_effect=send)
        guild.get_channel.return_value=dest
        guild.fetch_channel=AsyncMock(return_value=channel)
        return store,tid,channel,guild,dest,message

    async def test_archive_message_and_bytes_before_deleting_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            store,tid,channel,guild,dest,message=self.scenario(tmp)
            i=Mock()
            i.client.closing_for_backup=False
            i.guild_id,i.channel_id,i.guild,i.channel=GUILD_ID,500,guild,channel
            i.user.id=77
            i.user.guild_permissions.administrator=True
            i.response.defer=AsyncMock()
            i.followup.send=AsyncMock()
            await CloseTicketView(store).close.callback(i)
            channel.delete.assert_awaited_once()
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM tickets').fetchone()[0],'closed')
                self.assertEqual(db.execute('SELECT count(*) FROM ticket_archives').fetchone()[0],1)

    async def test_missing_message_content_never_archives_or_deletes(self):
        with tempfile.TemporaryDirectory() as tmp:
            store,tid,channel,guild,dest,message=self.scenario(tmp)
            message.content=''
            message.attachments=[]
            with self.assertRaisesRegex(ValueError,'Message Content Intent'):
                await archive_ticket(channel,tid,2,77,store,dest)
            dest.send.assert_not_awaited()
            channel.delete.assert_not_awaited()

    async def test_upload_failure_keeps_original_and_ticket_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            store,tid,channel,guild,dest,message=self.scenario(tmp)
            dest.send.side_effect=RuntimeError('upload failed')
            with self.assertRaises(RuntimeError):
                await archive_ticket(channel,tid,2,77,store,dest)
            channel.delete.assert_not_awaited()
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT status FROM tickets').fetchone()[0],'open')
                self.assertEqual(db.execute('SELECT count(*) FROM ticket_archives').fetchone()[0],0)

    def test_backup_only_in_final_minute_kst(self):
        for hour,minute,expected in [(7,0,False),(12,0,False),(18,0,False),(23,58,False),(23,59,True),(0,0,False)]:
            self.assertEqual(nightly_backup_due(datetime(2026,10,27,hour,minute,tzinfo=KST)),expected)
