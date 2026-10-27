import unittest
from unittest.mock import Mock,AsyncMock
from private_screen import render,_SESSIONS

class ScreenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):_SESSIONS.clear()
    def interaction(self,user=1,private=False):
        i=Mock();i.guild_id=5;i.user.id=user;i.extras={};i.message.flags.ephemeral=private
        state={'done':False}
        i.response.is_done.side_effect=lambda:state['done']
        async def done(**kwargs):state['done']=True
        i.response.send_message=AsyncMock(side_effect=done)
        i.response.edit_message=AsyncMock(side_effect=done)
        i.response.defer=AsyncMock(side_effect=done)
        i.edit_original_response=AsyncMock()
        return i
    async def test_navigation_edits_private_message_instead_of_sending(self):
        first=self.interaction();await render(first,content='menu')
        first.response.send_message.assert_awaited_once()
        self.assertTrue(first.response.send_message.call_args.kwargs['ephemeral'])
        next_click=self.interaction(private=True);await render(next_click,content='detail')
        next_click.response.edit_message.assert_awaited_once()
        next_click.response.send_message.assert_not_awaited()
    async def test_reopening_public_panel_reuses_existing_screen_without_touching_public(self):
        first=self.interaction();await render(first,content='menu')
        second=self.interaction();await render(second,content='processing')
        await render(second,content='completed')
        self.assertEqual(first.edit_original_response.await_count,2)
        second.edit_original_response.assert_not_awaited()
        second.response.edit_message.assert_not_awaited()
        second.response.send_message.assert_not_awaited()
        second.response.defer.assert_awaited_once()
    async def test_other_member_receives_separate_private_screen(self):
        a=self.interaction(user=1);b=self.interaction(user=2)
        await render(a,content='one');await render(b,content='two')
        a.response.send_message.assert_awaited_once();b.response.send_message.assert_awaited_once()
        a.edit_original_response.assert_not_awaited()
