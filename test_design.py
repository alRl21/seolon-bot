import os
import tempfile
import unittest
from unittest.mock import Mock
from unittest.mock import patch

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
import discord
import bot


class DesignTests(unittest.TestCase):
    def guild(self, categories):
        guild = Mock()
        guild.categories = categories
        guild.get_channel.return_value = None
        return guild

    def test_empty_server_plan(self):
        plan = bot.plan(self.guild([]))
        self.assertEqual(len(plan), 21)
        self.assertTrue(all(obj is None for _, _, obj, _ in plan))

    def test_duplicate_blocks(self):
        cats = []
        for cid in [1, 2]:
            c = Mock(spec=discord.CategoryChannel)
            c.name = bot.LAYOUT[0][0]
            c.id = cid
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

    def test_missing_owner_blocks_everyone(self):
        interaction = Mock()
        interaction.guild_id = bot.GUILD_ID
        interaction.user.id = 123
        with patch.object(bot, 'OWNER_ID', 0):
            with self.assertRaises(bot.app_commands.CheckFailure):
                bot.require_owner(interaction)
