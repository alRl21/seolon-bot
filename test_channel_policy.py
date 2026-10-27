import unittest
from unittest.mock import Mock
import discord
from channel_policy import readonly_overwrites, WRITE_PERMISSIONS

class PolicyTests(unittest.TestCase):
    def test_secondary_role_cannot_bypass_readonly_visibility_and_other_bits_preserved(self):
        everyone, clan, owner, bot, other, admin = [Mock() for _ in range(6)]
        for rid, target in enumerate([everyone, clan, owner, bot, other, admin], 1):
            target.id = rid
            target.permissions = discord.Permissions.none()
        admin.permissions.administrator = True
        existing = {clan: discord.PermissionOverwrite(view_channel=True),
                    other: discord.PermissionOverwrite(send_messages=True, manage_messages=False),
                    admin: discord.PermissionOverwrite(send_messages=True)}
        result = readonly_overwrites(existing, everyone, clan, owner, bot)
        for target in [everyone, clan, other]:
            for key in WRITE_PERMISSIONS:
                self.assertIs(getattr(result[target], key), False)
        for target in [owner, bot, admin]:
            self.assertIs(result[target].send_messages, True)
        self.assertIs(result[clan].view_channel, True)
        self.assertIsNone(result[everyone].view_channel)
        self.assertIs(result[other].manage_messages, False)
        self.assertIs(existing[other].send_messages, True)
