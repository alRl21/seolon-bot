import unittest
from unittest.mock import Mock, AsyncMock

import discord
import asyncio
import tempfile
from pathlib import Path
from core import PROTECTED_CHANNEL_IDS
from rebuild import keep_ids, reset_targets, guarded_delete, inventory_signature
from reset_ui import ResetView


def channel(cid, parent=None, category=False):
    c = Mock(spec=discord.CategoryChannel if category else discord.TextChannel)
    c.id = cid
    c.name = f'channel-{cid}'
    c.type = discord.ChannelType.category if category else discord.ChannelType.text
    c.category_id = parent
    c.overwrites = {}
    c.delete = AsyncMock()
    return c


class RebuildTests(unittest.TestCase):
    def setUp(self):
        self.pins = [channel(cid, parent=100) for cid in PROTECTED_CHANNEL_IDS]

    def test_pinned_and_parent_are_kept(self):
        original = self.pins + [channel(100, category=True), channel(200), channel(300, category=True)]
        self.assertEqual({c.id for c in reset_targets(original)}, {200, 300})

    def test_missing_pin_stops_before_delete(self):
        with self.assertRaises(ValueError):
            reset_targets(self.pins[:-1] + [channel(200)])

    def test_children_of_pinned_category_are_kept(self):
        cid = next(iter(PROTECTED_CHANNEL_IDS))
        original = self.pins + [channel(200, parent=cid)]
        self.assertIn(200, keep_ids(original))
        self.assertNotIn(200, {c.id for c in reset_targets(original)})

    def test_category_deleted_after_children(self):
        original = self.pins + [channel(2, category=True), channel(9, parent=2)]
        self.assertEqual([c.id for c in reset_targets(original)], [9, 2])

    def test_new_channel_is_not_in_frozen_target_list(self):
        original = self.pins + [channel(200)]
        targets = reset_targets(original)
        original.append(channel(300))
        self.assertEqual([c.id for c in targets], [200])

    def test_preview_invalidated_by_moves(self):
        before = inventory_signature(self.pins)
        self.pins[0].category_id = 999
        self.assertNotEqual(before, inventory_signature(self.pins))


class DeleteGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_pinned_id_blocks_actual_delete(self):
        for cid in PROTECTED_CHANNEL_IDS:
            c = channel(cid)
            with self.assertRaises(ValueError):
                await guarded_delete(c, set())
            c.delete.assert_not_awaited()

    async def test_parent_blocks_actual_delete(self):
        c = channel(100, category=True)
        with self.assertRaises(ValueError):
            await guarded_delete(c, {100})
        c.delete.assert_not_awaited()

    async def test_unprotected_target_deleted_once(self):
        c = channel(999)
        await guarded_delete(c, set(PROTECTED_CHANNEL_IDS))
        c.delete.assert_awaited_once()


class ResetExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def make_scenario(self, fail_create=False):
        pins = [channel(cid, 100) for cid in PROTECTED_CHANNEL_IDS]
        old = pins + [channel(100, category=True), channel(200), channel(300, category=True)]
        live = list(old)
        for c in old:
            async def remove(*, reason, obj=c):
                live.remove(obj)
            c.delete.side_effect = remove
        guild = Mock()
        guild.me.guild_permissions.administrator = True
        for attr in ['rules_channel', 'public_updates_channel', 'system_channel', 'afk_channel', 'safety_alerts_channel']:
            setattr(guild, attr, None)
        async def fetch():
            return list(live)
        guild.fetch_channels = AsyncMock(side_effect=fetch)
        async def create(name, **kwargs):
            cid = 1000 + len(live)
            c = channel(cid, getattr(kwargs.get('category'), 'id', None), category='category' not in kwargs)
            c.name = name
            live.append(c)
            return c
        guild.create_category = AsyncMock(side_effect=create)
        guild.create_text_channel = AsyncMock(side_effect=RuntimeError('creation failed') if fail_create else create)
        guild.create_voice_channel = AsyncMock(side_effect=create)
        interaction = Mock()
        interaction.guild = guild
        interaction.user.id = 123
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        view = ResetView(123, inventory_signature(old), Mock(), Path(tmp.name), asyncio.Lock(), lambda i: None, Mock())
        await view.confirm.callback(interaction)
        return pins, old, live, interaction

    async def test_full_reset_keeps_pins_and_parent(self):
        pins, old, live, interaction = await self.make_scenario()
        self.assertTrue(PROTECTED_CHANNEL_IDS <= {c.id for c in live})
        self.assertIn(100, {c.id for c in live})
        self.assertNotIn(200, {c.id for c in live})
        self.assertNotIn(300, {c.id for c in live})
        for pin in pins:
            pin.delete.assert_not_awaited()
        self.assertIn('초기화 완료', interaction.followup.send.call_args.args[0])

    async def test_failed_creation_does_not_delete_anything(self):
        pins, old, live, interaction = await self.make_scenario(fail_create=True)
        for c in old:
            c.delete.assert_not_awaited()
        self.assertIn('초기화를 중단', interaction.followup.send.call_args.args[0])
