import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,AsyncMock
import discord
from core import Store,GUILD_ID,PROTECTED_CHANNEL_IDS
from titles_store import initialise_titles,create_title,grant,purchase,rows,revoke
from collection_rewards import claim_completed
from community_store import initialise_community,create_party,join_party,party_members,create_poll,vote,counts,close_item
from items_store import initialise_items,item_rows,buy_item,redeem_voucher,use_theme
from voice_rooms import cleanup_room,voice_update

class CommunityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'db')
        initialise_titles(self.s);initialise_community(self.s);initialise_items(self.s)
        with self.s.connect() as db:db.execute('INSERT INTO wallet VALUES (1,2,10000)')
    def tearDown(self):self.tmp.cleanup()
    def collection(self):
        titles=[create_title(self.s,1,str(n),50+n,'sale',100,False,9,'세트') for n in range(4)]
        reward=create_title(self.s,1,'히든',60,'grant',0,True,9,'세트')
        with self.s.connect() as db:
            db.execute('UPDATE titles SET tier=5 WHERE id=?',(reward,))
            db.execute('INSERT INTO title_collections VALUES (?,?,?)',('세트',json.dumps(titles),reward))
        return titles,reward
    def test_four_owned_auto_awards_hidden_once_in_purchase_transaction(self):
        titles,reward=self.collection()
        for title in titles[:3]:purchase(self.s,1,2,title)
        self.assertNotIn(reward,{r['id'] for r in rows(self.s,1,2)})
        purchase(self.s,1,2,titles[3])
        self.assertIn(reward,{r['id'] for r in rows(self.s,1,2)})
        with ThreadPoolExecutor(max_workers=6) as pool:self.assertTrue(all(not v for v in pool.map(lambda _:claim_completed(self.s,1,2),range(6))))
        self.assertEqual(self.s.profile(1,2)[0],9600)
        revoke(self.s,1,2,reward,9)
        self.assertEqual(claim_completed(self.s,1,2),[])
        with self.s.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM collection_claims').fetchone()[0],1)
    def test_grants_also_award_and_restart_preserves_collection(self):
        titles,reward=self.collection()
        for title in titles:grant(self.s,1,2,title,9)
        self.assertEqual(len(rows(Store(self.s.path),1,2)),5)
        self.assertEqual(self.s.profile(1,2)[0],10000)
    def test_party_last_slot_is_atomic_and_cannot_join_after_start(self):
        pid=create_party(self.s,1,'롤',200,2,'',now=100)
        with self.s.connect() as db:db.execute("UPDATE parties SET status='open' WHERE id=?",(pid,))
        def attempt(user):
            try:join_party(self.s,pid,user,now=101);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(attempt,[2,3])),1)
        self.assertEqual(len(party_members(self.s,pid)),2)
        with self.assertRaises(ValueError):join_party(self.s,pid,4,now=200)
        with self.assertRaises(ValueError):close_item(self.s,'parties',pid,77)
        close_item(self.s,'parties',pid,1)
    def test_poll_single_vote_changes_and_deadline_enforced(self):
        pid=create_poll(self.s,1,'언제',['토요일','일요일'],1,now=100)
        with self.s.connect() as db:db.execute("UPDATE polls SET status='open' WHERE id=?",(pid,))
        vote(self.s,pid,2,0,now=101);vote(self.s,pid,2,1,now=102)
        self.assertEqual(counts(self.s,pid),{1:1})
        with self.assertRaises(ValueError):vote(self.s,pid,3,1,now=3700)
        with self.assertRaises(ValueError):vote(self.s,pid,3,9,now=103)
    def test_creation_rate_limits_and_capacity_validation(self):
        create_party(self.s,1,'롤',200,2,'',now=100)
        with self.assertRaises(ValueError):create_party(self.s,1,'롤',300,2,'',now=101)
        with self.assertRaises(ValueError):create_party(self.s,2,'롤',300,21,'',now=101)
        with self.assertRaises(ValueError):create_poll(self.s,2,'x',['같음','같음'],1,now=100)
    def test_item_purchase_voucher_redeem_and_no_duplicate_consumption(self):
        voucher=next(r for r in item_rows(self.s) if r['kind']=='voucher')
        title=create_title(self.s,1,'교환 대상',50,'sale',650,False,9)
        buy_item(self.s,1,2,voucher['id']);redeem_voucher(self.s,1,2,voucher['id'],title)
        self.assertEqual(self.s.profile(1,2)[0],9350)
        self.assertEqual(len(rows(self.s,1,2)),1)
        with self.assertRaises(ValueError):redeem_voucher(self.s,1,2,voucher['id'],title)
        self.assertEqual(item_rows(self.s,2),[])
    def test_voucher_never_redeems_grant_hidden_or_limited(self):
        voucher=next(r for r in item_rows(self.s) if r['kind']=='voucher')
        buy_item(self.s,1,2,voucher['id'])
        for n,kind,hidden in [(0,'grant',False),(1,'sale',True),(2,'limited',False)]:
            title=create_title(self.s,1,str(n),50+n,kind,100,hidden,9)
            with self.assertRaises(ValueError):redeem_voucher(self.s,1,2,voucher['id'],title)
        self.assertEqual(item_rows(self.s,2)[0]['quantity'],1)
    def test_permanent_theme_no_double_charge_and_applies(self):
        theme=next(r for r in item_rows(self.s) if r['kind']=='theme')
        buy_item(self.s,1,2,theme['id']);use_theme(self.s,2,theme['id'])
        with self.assertRaises(ValueError):buy_item(self.s,1,2,theme['id'])
        self.assertEqual(self.s.profile(1,2)[0],9700)
        with self.s.connect() as db:self.assertEqual(db.execute('SELECT colour FROM profile_styles').fetchone()[0],theme['value'])
    def test_item_inventory_backed_up_with_wallet(self):
        from operations_store import backup_database
        item=item_rows(self.s)[0];buy_item(self.s,1,2,item['id'])
        dest=Path(self.tmp.name)/'snapshot';backup_database(self.s,dest)
        self.assertEqual(item_rows(Store(dest),2)[0]['quantity'],1)

class VoiceRoomTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_only_bot_owned_empty_room_never_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_community(s)
            channel=Mock(spec=discord.VoiceChannel);channel.id=500;channel.category_id=100;channel.members=[];channel.delete=AsyncMock()
            await cleanup_room(channel,s);channel.delete.assert_not_awaited()
            with s.connect() as db:db.execute('INSERT INTO temp_voices VALUES (500,2,100)')
            channel.members=[SimpleNamespace(bot=False)]
            await cleanup_room(channel,s);channel.delete.assert_not_awaited()
            channel.members=[]
            await cleanup_room(channel,s);channel.delete.assert_awaited_once()
            pin=Mock(spec=discord.VoiceChannel);pin.id=next(iter(PROTECTED_CHANNEL_IDS));pin.category_id=100;pin.members=[];pin.delete=AsyncMock()
            with s.connect() as db:db.execute('INSERT INTO temp_voices VALUES (?,2,100)',(pin.id,))
            await cleanup_room(pin,s);pin.delete.assert_not_awaited()
    async def test_trigger_creates_and_records_room_before_moving_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_community(s);s.put('voice:＋ 새로운 방',77)
            guild=Mock();guild.id=GUILD_ID;guild.roles=[]
            clan=Mock();clan.id=1522946198984593541;guild.get_role.return_value=clan
            guild.get_channel.return_value=None
            category=Mock();category.id=100;category.channels=[]
            trigger=Mock();trigger.id=77;trigger.category=category
            room=Mock(spec=discord.VoiceChannel);room.id=500;room.category_id=100;room.members=[]
            guild.create_voice_channel=AsyncMock(return_value=room)
            guild.fetch_member=AsyncMock(return_value=Mock())
            member=Mock();member.guild=guild;member.bot=False;member.id=2;member.display_name='테스트';member.roles=[clan]
            async def move(target,**kwargs):
                with s.connect() as db:self.assertEqual(db.execute('SELECT owner FROM temp_voices WHERE channel=500').fetchone()[0],2)
            member.move_to=AsyncMock(side_effect=move)
            await voice_update(member,SimpleNamespace(channel=None),SimpleNamespace(channel=trigger),s)
            member.move_to.assert_awaited_once()
            permissions=guild.create_voice_channel.call_args.kwargs['overwrites']
            self.assertIs(permissions[guild.default_role].view_channel,False)
            self.assertIs(permissions[clan].connect,True)

class CommunityViewsTests(unittest.IsolatedAsyncioTestCase):
    async def test_views_construct_with_persistent_custom_ids(self):
        from community_ui import Hub,PartyPanel,PartyView,PollPanel,PollView
        for cls in [Hub,PartyPanel,PartyView,PollPanel,PollView]:
            view=cls(Mock())
            self.assertTrue(view.is_persistent(),cls.__name__)
        from items_ui import StoreMenu
        view=StoreMenu(Mock(),2)
        self.assertIn('칭호',[c.label for c in view.children])
        self.assertIn('아이템',[c.label for c in view.children])
