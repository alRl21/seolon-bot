import sqlite3
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, AsyncMock, patch

import discord
from core import GUILD_ID, Store
from membership import JoinRequestView, ADMIN_CHAT_CHANNEL_ID, setup_admin_chat, ADMIN_CATEGORY


class CooldownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'db'
        self.store = Store(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ten_minute_boundary_survives_restart(self):
        self.assertEqual(self.store.reserve_membership_notice(1, 1000), (True, 0, False))
        store = Store(self.path)
        self.assertEqual(store.reserve_membership_notice(1, 1599), (False, 1, False))
        self.assertEqual(store.reserve_membership_notice(1, 1600), (True, 0, True))
        self.assertEqual(len(store.pending_members()), 1)

    def test_other_users_not_blocked(self):
        self.store.reserve_membership_notice(1, 1000)
        self.assertEqual(self.store.reserve_membership_notice(2, 1001), (True, 0, False))

    def test_simultaneous_clicks_only_reserve_one_notice(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.reserve_membership_notice(1, 1000), range(8)))
        self.assertEqual(sum(row[0] for row in results), 1)

    def test_old_database_migrates_without_losing_requests(self):
        path = Path(self.tmp.name) / 'old'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE membership_requests (user INTEGER PRIMARY KEY,requested_at TEXT,state TEXT)')
            db.execute("INSERT INTO membership_requests VALUES (123,'2026-10-27','pending')")
        store = Store(path)
        self.assertEqual(store.pending_members(), [(123, '2026-10-27')])
        self.assertEqual(store.reserve_membership_notice(123, 1000), (True, 0, False))


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_notice_goes_only_to_admin_chat_and_repeat_is_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            view = JoinRequestView(store)
            i = Mock()
            i.guild_id = GUILD_ID
            i.user.id = 123
            i.user.bot = False
            i.user.roles = []
            i.response.defer = AsyncMock()
            i.followup.send = AsyncMock()
            channel = Mock(spec=discord.TextChannel)
            channel.send = AsyncMock()
            i.guild.get_channel.return_value = channel
            admin, ordinary, bot_role = Mock(), Mock(), Mock()
            for rid, role in enumerate([admin, ordinary, bot_role], 10):
                role.id = rid
                role.mention = f'<@&{rid}>'
                role.permissions.administrator = role is not ordinary
                role.managed = role is bot_role
            i.guild.id = GUILD_ID
            i.guild.owner_id = 999
            i.guild.roles = [admin, ordinary, bot_role]
            with patch('core.time.time', return_value=1000):
                await view.request.callback(i)
                await view.request.callback(i)
            channel.send.assert_awaited_once()
            i.guild.get_channel.assert_called_with(ADMIN_CHAT_CHANNEL_ID)
            sent = channel.send.call_args.kwargs
            self.assertIn(admin.mention, sent['content'])
            self.assertNotIn(ordinary.mention, sent['content'])
            self.assertNotIn(bot_role.mention, sent['content'])
            self.assertFalse(sent['allowed_mentions'].everyone)
            self.assertEqual(sent['allowed_mentions'].roles, [admin])
            self.assertEqual(sent['allowed_mentions'].users[0].id, 999)
            with patch('core.time.time', return_value=1600):
                await view.request.callback(i)
            self.assertEqual(channel.send.await_count, 2)

    async def test_admin_channel_id_and_messages_preserved_private_category_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            everyone, clan, owner, bot = [Mock() for _ in range(4)]
            for rid, role in enumerate([everyone, clan, owner, bot], 1):
                role.id = rid
            store.put('entry:clan-role', clan.id)
            chat = Mock(spec=discord.TextChannel)
            chat.id = ADMIN_CHAT_CHANNEL_ID
            chat.name = '기존 관리자 대화'
            chat.overwrites = {everyone: discord.PermissionOverwrite(view_channel=True)}
            chat.delete = AsyncMock()
            category = Mock(spec=discord.CategoryChannel)
            category.id, category.name = 500, ADMIN_CATEGORY
            category.overwrites = {}
            live = [chat]
            async def edit_chat(**kwargs):
                chat.overwrites = kwargs['overwrites']
                chat.category_id = kwargs['category'].id
                chat.name = kwargs['name']
                return chat
            async def edit_category(**kwargs):
                category.overwrites = kwargs['overwrites']
                return category
            chat.edit = AsyncMock(side_effect=edit_chat)
            category.edit = AsyncMock(side_effect=edit_category)
            for c in [chat, category]:
                c.overwrites_for.side_effect = lambda r, obj=c: next((v for k, v in obj.overwrites.items() if k.id == r.id), discord.PermissionOverwrite())
            guild = Mock()
            guild.default_role, guild.me = everyone, bot
            guild.fetch_member = AsyncMock(return_value=owner)
            guild.get_role.return_value = clan
            async def fetch():
                return list(live)
            async def create(name, **kwargs):
                category.overwrites = kwargs['overwrites']
                live.append(category)
                return category
            guild.fetch_channels = AsyncMock(side_effect=fetch)
            guild.create_category = AsyncMock(side_effect=create)
            await setup_admin_chat(guild, owner.id, store, Mock())
            await setup_admin_chat(guild, owner.id, store, Mock())
            self.assertEqual(chat.id, ADMIN_CHAT_CHANNEL_ID)
            self.assertEqual(chat.category_id, 500)
            self.assertFalse(chat.overwrites_for(everyone).view_channel)
            self.assertFalse(chat.overwrites_for(clan).view_channel)
            chat.delete.assert_not_awaited()
            guild.create_category.assert_awaited_once()
