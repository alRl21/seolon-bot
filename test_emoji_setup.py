import unittest
from unittest.mock import Mock,AsyncMock
import discord
from emoji_setup import selected_channels,setup_emoji_permissions,NQN_ID
from core import PROTECTED_CHANNEL_IDS

class EmojiTests(unittest.IsolatedAsyncioTestCase):
    def test_selection_excludes_protected_and_unlisted_channels(self):
        store=Mock();store.get.side_effect=lambda key:123
        ordinary=Mock(spec=discord.TextChannel);ordinary.id=123;ordinary.category_id=999
        secret=Mock(spec=discord.TextChannel);secret.id=444;secret.category_id=999
        protected=Mock(spec=discord.TextChannel);protected.id=next(iter(PROTECTED_CHANNEL_IDS));protected.category_id=999
        self.assertEqual(selected_channels([ordinary,secret,protected],store),[ordinary])
        ordinary.category_id=next(iter(PROTECTED_CHANNEL_IDS))
        self.assertEqual(selected_channels([ordinary],store),[])
    async def test_never_grants_administrator(self):
        store=Mock();store.get.return_value=None
        guild=Mock();role=Mock();role.managed=True;role.tags.bot_id=NQN_ID
        guild.fetch_roles=AsyncMock(return_value=[role])
        member=Mock();member.bot=True;member.guild_permissions.administrator=True
        guild.fetch_member=AsyncMock(return_value=member)
        with self.assertRaisesRegex(ValueError,'관리자 권한'):
            await setup_emoji_permissions(guild,store,Mock())
        guild.edit.assert_not_called()
    async def test_only_six_allowed_channels_are_edited_and_verified(self):
        import tempfile
        from pathlib import Path
        from core import Store
        from emoji_setup import CHAT_NAMES
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            clan=Mock();clan.id=44
            bot_role=Mock();bot_role.id=55;bot_role.managed=True;bot_role.tags.bot_id=NQN_ID
            channels=[]
            for cid,name in enumerate(CHAT_NAMES,100):
                store.put('text:'+name,cid)
                c=Mock(spec=discord.TextChannel);c.id=cid;c.name=name;c.category_id=90;c.overwrites={}
                c.permissions_for.return_value.view_channel=True
                c.permissions_for.return_value.send_messages=True
                async def edit(c=c,**kwargs):c.overwrites=kwargs['overwrites']
                c.edit=AsyncMock(side_effect=edit)
                c.overwrites_for.side_effect=lambda target,c=c:c.overwrites.get(target,discord.PermissionOverwrite())
                channels.append(c)
            protected=Mock(spec=discord.TextChannel);protected.id=next(iter(PROTECTED_CHANNEL_IDS));protected.category_id=90
            guild=Mock();guild.emojis=[]
            guild.fetch_roles=AsyncMock(return_value=[bot_role])
            member=Mock();member.bot=True;member.guild_permissions.administrator=False
            guild.fetch_member=AsyncMock(return_value=member)
            guild.get_role.return_value=clan
            guild.fetch_channels=AsyncMock(return_value=channels+[protected])
            await setup_emoji_permissions(guild,store,Mock())
            self.assertEqual(store.get('emoji:nqn-configured'),1)
            for c in channels:
                c.edit.assert_awaited_once()
                self.assertIs(c.overwrites[bot_role].manage_webhooks,True)
                self.assertIs(c.overwrites[clan].external_emojis,True)
                self.assertIsNone(c.overwrites[clan].view_channel)
                self.assertIsNone(c.overwrites[clan].manage_webhooks)
            protected.edit.assert_not_called()
