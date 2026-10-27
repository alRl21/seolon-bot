import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import Mock, MagicMock, AsyncMock

import discord
from core import Store, PROTECTED_CHANNEL_IDS
from membership import view_overwrites, safe_clan_role, entry_mode, GateView, JoinRequestView, ENTRY_LOG_CHANNEL_ID
from rebuild import inventory_signature
from core import GUILD_ID


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.everyone, self.clan, self.owner, self.bot, self.other = [Mock() for _ in range(5)]

    def test_entrance_visible_before_membership_hidden_after(self):
        result = view_overwrites({}, self.everyone, self.clan, self.owner, self.bot, 'entry')
        self.assertIs(result[self.everyone].view_channel, True)
        self.assertIs(result[self.clan].view_channel, False)
        self.assertIs(result[self.bot].view_channel, True)

    def test_main_hidden_before_membership_visible_after(self):
        result = view_overwrites({}, self.everyone, self.clan, self.owner, self.bot, 'main')
        self.assertIs(result[self.everyone].view_channel, False)
        self.assertIs(result[self.clan].view_channel, True)

    def test_other_allow_cannot_override_entrance_deny(self):
        existing = {self.other: discord.PermissionOverwrite(view_channel=True, send_messages=False)}
        result = view_overwrites(existing, self.everyone, self.clan, self.owner, self.bot, 'entry')
        self.assertIsNone(result[self.other].view_channel)
        self.assertIs(result[self.other].send_messages, False)
        self.assertIs(existing[self.other].view_channel, True)

    def test_readonly_entrance_leaves_bot_able_to_post(self):
        result = view_overwrites({}, self.everyone, self.clan, self.owner, self.bot, 'entry', readonly=True)
        self.assertIs(result[self.everyone].send_messages, False)
        self.assertIs(result[self.bot].send_messages, True)

    def test_protection_default_and_explicit_visibility_exception(self):
        guild = Mock()
        guild.channels = []
        store = Mock()
        store.get.return_value = None
        for cid in PROTECTED_CHANNEL_IDS:
            channel = Mock(spec=discord.TextChannel)
            channel.id = cid
            channel.category_id = None
            channel.category = None
            channel.name = 'preserved'
            self.assertEqual(entry_mode(channel, guild, store), 'entry' if cid == ENTRY_LOG_CHANNEL_ID else 'admin')

    def test_private_operations_space_never_opened_to_clan(self):
        guild = Mock()
        store = Mock()
        store.get.return_value = None
        c = Mock(spec=discord.TextChannel)
        c.id = 123
        c.category_id = 456
        c.category.name = '── ⟡ 설온 운영실 ⟡ ──'
        self.assertEqual(entry_mode(c, guild, store), 'private')

    def test_role_cannot_carry_admin_privileges(self):
        role = Mock()
        role.permissions = discord.Permissions.none()
        self.assertTrue(safe_clan_role(role))
        role.permissions.manage_roles = True
        self.assertFalse(safe_clan_role(role))


class MembershipPersistenceTests(unittest.TestCase):
    def test_pending_duplicate_and_approval_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'db'
            store = Store(path)
            self.assertTrue(store.request_membership(123))
            self.assertFalse(store.request_membership(123))
            store = Store(path)
            self.assertEqual([row[0] for row in store.pending_members()], [123])
            store.membership_state(123, 'approved')
            self.assertEqual(store.pending_members(), [])

    def test_request_never_increases_balance(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            store.request_membership(123)
            self.assertEqual(store.profile(1, 123), (0, 0))


class GateExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, legacy_high_role=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'db')
        guild = Mock()
        everyone, clan, owner, bot = [MagicMock() for _ in range(4)]
        for i, target in enumerate([everyone, clan, owner, bot], 1):
            target.id = i
        clan.permissions = discord.Permissions.none()
        clan.managed = False
        clan.__ge__.return_value = False
        guild.default_role = everyone
        everyone.permissions = discord.Permissions.none()
        guild.me = bot
        guild.roles = []
        if legacy_high_role:
            old_role = MagicMock()
            old_role.name = '클랜원'
            old_role.id = 555
            old_role.permissions = discord.Permissions.none()
            old_role.managed = False
            old_role.__ge__.return_value = True
            guild.roles = [old_role]
            bot.roles = [bot]
            guild.fetch_roles = AsyncMock(return_value=[clan, bot])
        guild.system_channel_flags = discord.SystemChannelFlags()
        guild.create_role = AsyncMock(return_value=clan)
        guild.edit = AsyncMock()
        live = []

        def make_channel(cid, name, category=None, is_category=False, overwrites=None):
            c = Mock(spec=discord.CategoryChannel if is_category else discord.TextChannel)
            c.id, c.name = cid, name
            c.category, c.category_id = category, category.id if category else None
            c.type = discord.ChannelType.category if is_category else discord.ChannelType.text
            c.overwrites = overwrites if overwrites is not None else {everyone: discord.PermissionOverwrite(view_channel=True)}
            c.permissions_for.return_value.view_channel = True
            c.overwrites_for.side_effect = lambda t: next((v for k, v in c.overwrites.items() if k.id == t.id), discord.PermissionOverwrite())
            async def edit(**kwargs):
                c.overwrites = kwargs['overwrites']
                if 'name' in kwargs:
                    c.name = kwargs['name']
                if 'category' in kwargs:
                    c.category = kwargs['category']
                    c.category_id = c.category.id
                return c
            c.edit = AsyncMock(side_effect=edit)
            message = Mock()
            message.id = 9999
            c.send = AsyncMock(return_value=message)
            return c

        pins = [make_channel(cid, 'protected') for cid in PROTECTED_CHANNEL_IDS]
        main = make_channel(100, '﹒도란도란')
        live.extend(pins + [main])
        guild.channels = live
        guild.get_channel.side_effect = lambda cid: next((c for c in live if c.id == cid), None)
        async def fetch():
            return list(live)
        guild.fetch_channels = AsyncMock(side_effect=fetch)
        async def category(name, **kwargs):
            c = make_channel(1000 + len(live), name, is_category=True, overwrites=kwargs['overwrites'])
            live.append(c)
            return c
        async def text(name, **kwargs):
            c = make_channel(1000 + len(live), name, kwargs['category'], overwrites=kwargs['overwrites'])
            live.append(c)
            return c
        guild.create_category = AsyncMock(side_effect=category)
        guild.create_text_channel = AsyncMock(side_effect=text)
        interaction = Mock()
        interaction.guild = guild
        interaction.user = owner
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        view = GateView(owner.id, inventory_signature(live), store, Path(tmp.name), asyncio.Lock(), lambda i: None, Mock())
        await view.apply(interaction)
        return pins, main, store, everyone, clan, interaction

    async def test_application_hides_protected_from_newcomers_and_clan(self):
        pins, main, store, everyone, clan, interaction = await self.scenario()
        self.assertIn('입장 설정 완료', interaction.followup.send.call_args.args[0])
        for pin in pins:
            self.assertEqual(pin.overwrites_for(everyone).view_channel, pin.id == ENTRY_LOG_CHANNEL_ID)
            self.assertFalse(pin.overwrites_for(clan).view_channel)
        self.assertFalse(main.overwrites_for(everyone).view_channel)
        self.assertTrue(main.overwrites_for(clan).view_channel)
        self.assertEqual(store.get('entry:active'), 1)

    async def test_pinned_name_location_and_messages_are_not_changed(self):
        pins, main, store, everyone, clan, interaction = await self.scenario()
        self.assertIn('입장 설정 완료', interaction.followup.send.call_args.args[0])
        for pin in pins:
            self.assertEqual(set(pin.edit.call_args.kwargs), {'overwrites', 'reason'})
            self.assertEqual(pin.overwrites_for(everyone).view_channel, pin.id == ENTRY_LOG_CHANNEL_ID)
            self.assertFalse(pin.overwrites_for(clan).view_channel)
            if pin.id == ENTRY_LOG_CHANNEL_ID:
                self.assertEqual(pin.name, '﹒입장로그')
                self.assertIsNotNone(pin.category_id)
            else:
                self.assertEqual(pin.name, 'protected')
                self.assertIsNone(pin.category_id)
        self.assertEqual(store.get('entry:protected-admin-only'), 1)

    async def test_public_application_button_never_grants_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            view = JoinRequestView(store)
            i = Mock()
            i.guild_id = GUILD_ID
            i.user.bot = False
            i.user.id = 123
            i.user.roles = []
            i.user.add_roles = AsyncMock()
            i.response.send_message = AsyncMock()
            i.response.defer = AsyncMock()
            i.followup.send = AsyncMock()
            admin_channel = Mock(spec=discord.TextChannel)
            admin_channel.send = AsyncMock()
            i.guild.get_channel.return_value = admin_channel
            i.guild.roles = []
            i.guild.owner_id = 999
            await view.request.callback(i)
            self.assertEqual(store.pending_members()[0][0], 123)
            i.user.add_roles.assert_not_awaited()

    async def test_unmanageable_old_role_uses_new_safe_role(self):
        pins, main, store, everyone, clan, interaction = await self.scenario(legacy_high_role=True)
        self.assertIn('입장 설정 완료', interaction.followup.send.call_args.args[0])
        self.assertEqual(store.get('entry:clan-role'), clan.id)
        self.assertEqual(interaction.guild.create_role.call_args.kwargs['name'], '✧ 클랜원')
        self.assertEqual(interaction.guild.create_role.call_args.kwargs['permissions'].value, 0)
