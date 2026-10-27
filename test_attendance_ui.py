import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest.mock import Mock, AsyncMock
from core import Store, GUILD_ID
from attendance_ui import AttendanceView, eligible
from membership import CLAN_ROLE_ID

class AttendanceUITests(unittest.IsolatedAsyncioTestCase):
    async def test_member_buttons_reward_once_and_wallet_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'db')
            i = Mock()
            i.guild_id = GUILD_ID
            i.user.id = 123
            i.user.bot = False
            i.user.roles = [Mock(id=CLAN_ROLE_ID)]
            i.response.defer = AsyncMock()
            i.response.send_message = AsyncMock()
            i.followup.send = AsyncMock()
            view = AttendanceView(store)
            self.assertTrue(await view.interaction_check(i))
            await view.attend.callback(i)
            await view.attend.callback(i)
            self.assertEqual(store.profile(GUILD_ID, 123), (30, 1))
            self.assertIn('이미 출석', i.followup.send.call_args.args[0])
            self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
            await view.wallet.callback(i)
            self.assertTrue(i.response.send_message.call_args.kwargs['ephemeral'])
            i.user.roles = []
            self.assertFalse(await view.interaction_check(i))
            i.guild_id = 999
            self.assertFalse(eligible(i))

    async def test_status_gap_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'db'
            s = Store(p)
            day = date(2026, 1, 1)
            s.attend(1, 2, day)
            self.assertEqual(Store(p).attendance_status(1, 2, day), (1, True))
            self.assertEqual(s.attendance_status(1, 2, day + timedelta(days=1)), (1, False))
            self.assertEqual(s.attendance_status(1, 2, day + timedelta(days=2)), (0, False))
