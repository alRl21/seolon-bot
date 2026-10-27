import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import discord
from core import Store, GUILD_ID
from operations_store import reserve_ticket, activate_ticket, active_ticket, close_ticket, backup_database
from operations_ui import private_permissions, CloseTicketView, TicketModal, OperationsWorker

class OperationsStoreTests(unittest.TestCase):
    def test_ticket_concurrency_close_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db')
            def reserve(_):
                try: return reserve_ticket(s,1,2)
                except sqlite3.IntegrityError: return None
            with ThreadPoolExecutor(max_workers=6) as pool:
                ids=list(pool.map(reserve,range(6)))
            self.assertEqual(sum(x is not None for x in ids),1)
            tid=next(x for x in ids if x is not None)
            activate_ticket(s,tid,500)
            self.assertEqual(active_ticket(Store(s.path),1,2),(tid,500,'open'))
            self.assertTrue(close_ticket(s,500,99))
            self.assertFalse(close_ticket(s,500,99))
            self.assertIsNone(active_ticket(s,1,2))
            self.assertIsNotNone(reserve_ticket(s,1,2))

    def test_backup_includes_committed_wal_and_atomic_attendance_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db')
            s.attend(1,2)
            s.attend(1,2)
            dest=Path(tmp)/'snapshot'
            backup_database(s,dest)
            restored=Store(dest)
            self.assertEqual(restored.profile(1,2),(30,1))
            with restored.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM audit_events').fetchone()[0],1)
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

class OperationsPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_acl_does_not_grant_clan_or_other_roles(self):
        guild=Mock()
        owner=Mock()
        rules=private_permissions(guild,owner)
        self.assertEqual(set(rules),{guild.default_role,guild.me,owner})
        self.assertIs(rules[guild.default_role].view_channel,False)
        self.assertIs(rules[owner].view_channel,True)
        self.assertIs(rules[guild.me].attach_files,True)

    async def test_non_admin_cannot_close_ticket(self):
        i=Mock()
        i.guild_id=GUILD_ID
        i.user.id=123
        i.user.guild_permissions.administrator=False
        i.response.send_message=AsyncMock()
        view=CloseTicketView(Mock())
        await view.close.callback(i)
        self.assertTrue(i.response.send_message.call_args.kwargs['ephemeral'])

    async def test_audit_retry_keeps_undelivered_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db')
            s.attend(1,2)
            channel=Mock()
            channel.overwrites = {}
            channel.overwrites_for.return_value = discord.PermissionOverwrite(view_channel=False)
            channel.send=AsyncMock(side_effect=RuntimeError('network failure'))
            guild=Mock()
            guild.get_channel.return_value=channel
            worker=OperationsWorker(Mock(),s,Path(tmp))
            with self.assertRaises(RuntimeError):
                await worker.flush(guild)
            with s.connect() as db:
                self.assertEqual(db.execute('SELECT delivered FROM audit_events').fetchone()[0],0)
