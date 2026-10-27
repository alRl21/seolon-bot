import unittest
from unittest.mock import Mock

import discord
from core import LAYOUT
from membership import ADMIN_PROTECTED_IDS
from arrange import category_order


def category(cid, position):
    c = Mock(spec=discord.CategoryChannel)
    c.id, c.position, c.name = cid, position, 'category-' + str(cid)
    return c


class ArrangeTests(unittest.TestCase):
    def scenario(self, parent):
        first, second, entry, protected = category(1, 0), category(2, 1), category(3, 3), category(4, 2)
        pins = []
        for cid in ADMIN_PROTECTED_IDS:
            c = Mock(spec=discord.TextChannel)
            c.id, c.category_id = cid, parent
            pins.append(c)
        values = {'entry:category': 3, 'category:' + LAYOUT[0][0]: 1, 'category:' + LAYOUT[1][0]: 2}
        store = Mock()
        store.get.side_effect = values.get
        return [first, second, protected, entry] + pins, store

    def test_new_block_starts_immediately_after_protected_category(self):
        channels, store = self.scenario(4)
        self.assertEqual([c.id for c in category_order(channels, store)], [4, 3, 1, 2])
        self.assertTrue(all(c.category_id == 4 for c in channels if isinstance(c, discord.TextChannel)))

    def test_uncategorized_protection_keeps_entrance_at_top_category(self):
        channels, store = self.scenario(None)
        self.assertEqual([c.id for c in category_order(channels, store)], [3, 1, 2, 4])

    def test_missing_protected_channel_refuses_change(self):
        channels, store = self.scenario(4)
        with self.assertRaises(ValueError):
            category_order(channels[:-1], store)
