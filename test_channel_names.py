import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import discord

from channel_names import setup_mobile_channel_names
from core import Store


class MobileChannelNameTests(unittest.IsolatedAsyncioTestCase):
    async def test_renames_without_recreating_or_changing_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'db')
            channel=Mock(spec=discord.TextChannel)
            channel.id=123;channel.name='﹒공지사항';channel.category_id=999
            async def edit(**kwargs):channel.name=kwargs['name'];return channel
            channel.edit=AsyncMock(side_effect=edit)
            guild=Mock();guild.fetch_channels=AsyncMock(side_effect=[[channel],[channel]])
            logger=Mock()
            await setup_mobile_channel_names(guild,store,logger)
            self.assertEqual(channel.id,123);self.assertEqual(channel.name,'┊공지사항')
            channel.edit.assert_awaited_once()
            self.assertEqual(store.get('channels:mobile-prefix'),1)

    async def test_store_keys_migrate_to_visible_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';store=Store(path);store.put('text:﹒눈꽃상점',777)
            migrated=Store(path)
            self.assertEqual(migrated.get('text:┊눈꽃상점'),777)
            self.assertIsNone(migrated.get('text:﹒눈꽃상점'))
