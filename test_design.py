import os
import tempfile
import unittest
from unittest.mock import Mock, AsyncMock
from unittest.mock import patch

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
import discord
import bot


class DesignTests(unittest.TestCase):
    def guild(self, categories):
        guild = Mock()
        guild.categories = categories
        guild.channels = list(categories)
        for category in categories:
            guild.channels.extend(getattr(category, 'text_channels', []))
        guild.get_channel.return_value = None
        return guild

    def test_empty_server_plan(self):
        plan = bot.plan(self.guild([]))
        self.assertEqual(len(plan), 22)
        self.assertTrue(all(obj is None for _, _, obj, _ in plan))

    def test_duplicate_blocks(self):
        cats = []
        for cid in [1, 2]:
            c = Mock(spec=discord.CategoryChannel)
            c.name = bot.LAYOUT[0][0]
            c.id = cid
            c.text_channels = []
            cats.append(c)
        with self.assertRaises(ValueError):
            bot.plan(self.guild(cats))

    def test_reuses_old_names(self):
        cat = Mock(spec=discord.CategoryChannel)
        cat.name = '01  ──  INFORMATION'
        cat.id = 1
        channel = Mock(spec=discord.TextChannel)
        channel.name = 'welcome'
        channel.id = 2
        channel.category_id = 1
        cat.text_channels = [channel]
        plan = bot.plan(self.guild([cat]))
        self.assertIs(plan[0][2], cat)
        self.assertIs(plan[1][2], channel)

    def channel(self, cid, name='welcome', category_id=1, public=True):
        c = Mock(spec=discord.TextChannel)
        c.id = cid
        c.name = name
        c.category_id = category_id
        c.permissions_for.return_value.view_channel = public
        c.overwrites = {}
        return c

    def test_all_four_protected_channels_excluded(self):
        guild = self.guild([])
        guild.channels = [self.channel(cid) for cid in bot.PROTECTED_CHANNEL_IDS]
        self.assertEqual(len(bot.PROTECTED_CHANNEL_IDS), 4)
        self.assertTrue(all(obj is None for k, n, obj, p in bot.plan(guild)))

    def test_parent_of_protected_channel_not_renamed(self):
        cat = Mock(spec=discord.CategoryChannel)
        cat.name = bot.LAYOUT[0][1][0]
        cat.id = 1
        cat.text_channels = [self.channel(next(iter(bot.PROTECTED_CHANNEL_IDS)))]
        self.assertIsNone(bot.plan(self.guild([cat]))[0][2])

    def test_surplus_public_archived_private_untouched(self):
        guild = self.guild([])
        public = self.channel(88, name='old-chat')
        private = self.channel(89, name='staff', public=False)
        guild.channels = [public, private]
        plan = bot.plan(guild)
        archived = [o for k, n, o, p in plan if k.startswith('archive:')]
        self.assertEqual(archived, [public])
        self.assertFalse(any(o is private for k, n, o, p in plan))

    def test_category_id_protects_descendants(self):
        child = self.channel(88, category_id=next(iter(bot.PROTECTED_CHANNEL_IDS)))
        self.assertTrue(bot.protected(child))

    def test_old_emoji_channel_reused(self):
        guild = self.guild([])
        channel = self.channel(55, name='📢┃공지사항')
        guild.channels = [channel]
        plan = bot.plan(guild)
        self.assertIs(next(o for k, n, o, p in plan if n == '┊공지사항'), channel)

    def test_preview_detects_changed_permissions(self):
        channel = self.channel(55)
        target = Mock()
        target.id = 333
        channel.overwrites = {target: discord.PermissionOverwrite(view_channel=False)}
        items = [('text:x', 'x', channel, 'parent')]
        before = bot.fingerprint(items)
        channel.overwrites = {target: discord.PermissionOverwrite(view_channel=True)}
        self.assertNotEqual(before, bot.fingerprint(items))

    def test_rejects_nonadmin(self):
        interaction = Mock()
        interaction.guild_id = bot.GUILD_ID
        interaction.permissions.administrator = False
        with self.assertRaises(bot.app_commands.CheckFailure):
            bot.require_admin(interaction)

    def test_only_configured_owner(self):
        interaction = Mock()
        interaction.guild_id = bot.GUILD_ID
        interaction.user.id = 123
        with patch.object(bot, 'OWNER_ID', 123):
            bot.require_owner(interaction)
            interaction.user.id = 456
            interaction.permissions.administrator = True
            with self.assertRaises(bot.app_commands.CheckFailure):
                bot.require_owner(interaction)


class EditProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_protected_edit_never_sent(self):
        for cid in bot.PROTECTED_CHANNEL_IDS:
            channel = Mock()
            channel.id = cid
            channel.edit = AsyncMock()
            with self.assertRaises(ValueError):
                await bot.update_existing(channel, 'new')
            channel.edit.assert_not_awaited()

    async def test_move_preserves_overwrites(self):
        channel = Mock()
        channel.id = 42
        channel.name = 'old'
        channel.category_id = 10
        channel.overwrites = {Mock(): discord.PermissionOverwrite(view_channel=False)}
        channel.edit = AsyncMock(return_value=channel)
        parent = Mock()
        parent.id = 20
        await bot.update_existing(channel, 'new', parent)
        kwargs = channel.edit.call_args.kwargs
        self.assertEqual(kwargs['overwrites'], channel.overwrites)
        self.assertFalse(kwargs['sync_permissions'])

    def test_missing_owner_blocks_everyone(self):
        interaction = Mock()
        interaction.guild_id = bot.GUILD_ID
        interaction.user.id = 123
        with patch.object(bot, 'OWNER_ID', 0):
            with self.assertRaises(bot.app_commands.CheckFailure):
                bot.require_owner(interaction)
