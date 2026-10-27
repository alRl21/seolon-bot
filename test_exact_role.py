import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
import discord
from core import Store, PROTECTED_CHANNEL_IDS
from membership import correct_clan_role, CLAN_ROLE_ID, ENTRY_LOG_CHANNEL_ID

class ExactRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_high_role_controls_visibility_without_replacing_or_granting(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            everyone, old, role, owner, bot = [Mock() for _ in range(5)]
            for target, rid in zip([everyone, old, role, owner, bot], [1, 2, CLAN_ROLE_ID, 4, 5]):
                target.id = rid
            role.permissions.administrator = False
            role.managed = True  # Channel ACLs do not require the role to be assignable.
            channels = []
            modes = {}
            for cid in [*PROTECTED_CHANNEL_IDS, 900]:
                c = Mock(spec=discord.TextChannel)
                c.id = cid
                c.overwrites = {old: discord.PermissionOverwrite(view_channel=True)}
                async def edit(c=c, **kwargs):
                    c.overwrites = kwargs['overwrites']
                c.edit = AsyncMock(side_effect=edit)
                c.overwrites_for.side_effect = lambda target, c=c: c.overwrites.get(target, discord.PermissionOverwrite())
                channels.append(c)
                modes[cid] = 'entry' if cid == ENTRY_LOG_CHANNEL_ID else ('main' if cid == 900 else 'admin')
            guild = Mock()
            guild.default_role, guild.me = everyone, bot
            guild.fetch_roles = AsyncMock(return_value=[old, role])
            guild.fetch_member = AsyncMock(return_value=owner)
            guild.fetch_channels = AsyncMock(return_value=channels)
            store.put('entry:clan-role', old.id)
            with patch('membership.entry_mode', side_effect=lambda c, *args: modes[c.id]):
                await correct_clan_role(guild, owner.id, store, Mock())
                await correct_clan_role(guild, owner.id, store, Mock())
            self.assertEqual(store.get('entry:clan-role'), CLAN_ROLE_ID)
            for c in channels:
                c.edit.assert_awaited_once()
                self.assertIs(c.overwrites_for(role).view_channel, modes[c.id] == 'main')
                self.assertIsNone(c.overwrites_for(old).view_channel)
            guild.create_role.assert_not_called()
            role.edit.assert_not_called()
