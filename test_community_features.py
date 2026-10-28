import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,AsyncMock,patch
import discord
from core import Store,GUILD_ID,PROTECTED_CHANNEL_IDS
from titles_store import initialise_titles,create_title,grant,purchase,rows,revoke
from collection_rewards import claim_completed,sync_hidden_roles,grant_owner_test_titles,ensure_collections
from community_store import initialise_community,create_party,join_party,party_members,create_poll,vote,counts,close_item
from items_store import initialise_items,item_rows,buy_item,redeem_voucher,use_theme,grant_voucher
from voice_rooms import cleanup_room,voice_update
from community_ui import ProfileTagsView,PROFILE_TAGS

class CommunityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'db')
        initialise_titles(self.s);initialise_community(self.s);initialise_items(self.s)
        with self.s.connect() as db:db.execute('INSERT INTO wallet VALUES (1,2,10000)')
    def tearDown(self):self.tmp.cleanup()
    def collection(self):
        titles=[create_title(self.s,1,str(n),50+n,'sale',100,False,9,'세트') for n in range(4)]
        grant_only=create_title(self.s,1,'지급전용',70,'grant',0,False,9,'기념')
        reward=create_title(self.s,1,'히든',60,'grant',0,True,9,'세트')
        with self.s.connect() as db:
            db.execute('UPDATE titles SET tier=5 WHERE id=?',(reward,))
            db.execute('INSERT INTO title_collections VALUES (?,?,?)',('세트',json.dumps(titles),reward))
        return titles,grant_only,reward
    def test_all_purchasable_and_one_grant_awards_then_new_title_revokes_hidden(self):
        titles,grant_only,reward=self.collection()
        for title in titles[:3]:purchase(self.s,1,2,title)
        self.assertNotIn(reward,{r['id'] for r in rows(self.s,1,2)})
        purchase(self.s,1,2,titles[3])
        self.assertNotIn(reward,{r['id'] for r in rows(self.s,1,2)})
        grant(self.s,1,2,grant_only,9)
        self.assertIn(reward,{r['id'] for r in rows(self.s,1,2)})
        with ThreadPoolExecutor(max_workers=6) as pool:self.assertTrue(all(not v for v in pool.map(lambda _:claim_completed(self.s,1,2),range(6))))
        self.assertEqual(self.s.profile(1,2)[0],9600)
        new_title=create_title(self.s,1,'새 구매형',80,'sale',100,False,9,'새 세트')
        self.assertEqual(claim_completed(self.s,1,2),[])
        self.assertNotIn(reward,{r['id'] for r in rows(self.s,1,2)})
        with self.s.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM collection_claims').fetchone()[0],0)
        purchase(self.s,1,2,new_title)
        self.assertIn(reward,{r['id'] for r in rows(self.s,1,2)})
    def test_grants_also_award_and_restart_preserves_collection(self):
        titles,grant_only,reward=self.collection()
        for title in titles:grant(self.s,1,2,title,9)
        self.assertNotIn(reward,{r['id'] for r in rows(self.s,1,2)})
        grant(self.s,1,2,grant_only,9)
        self.assertEqual(len(rows(Store(self.s.path),1,2)),6)
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
    def test_admin_granted_voucher_redeems_without_charging_and_no_duplicate_consumption(self):
        self.assertFalse(any(r['kind']=='voucher' for r in item_rows(self.s)))
        with self.s.connect() as db:voucher_id=db.execute("SELECT id FROM shop_items WHERE kind='voucher'").fetchone()[0]
        with self.assertRaises(ValueError):buy_item(self.s,1,2,voucher_id)
        grant_voucher(self.s,9,2,1)
        voucher=next(r for r in item_rows(self.s,2) if r['kind']=='voucher')
        title=create_title(self.s,1,'교환 대상',50,'sale',650,False,9)
        redeem_voucher(self.s,1,2,voucher['id'],title)
        self.assertEqual(self.s.profile(1,2)[0],10000)
        self.assertEqual(len(rows(self.s,1,2)),1)
        with self.assertRaises(ValueError):redeem_voucher(self.s,1,2,voucher['id'],title)
        self.assertEqual(item_rows(self.s,2),[])
    def test_voucher_redeems_grant_and_limited_but_hidden_is_condition_only(self):
        grant_voucher(self.s,9,2,3)
        voucher=next(r for r in item_rows(self.s,2) if r['kind']=='voucher')
        for n,kind,hidden in [(0,'grant',False),(1,'sale',True),(2,'limited',False)]:
            title=create_title(self.s,1,str(n),50+n,kind,100,hidden,9)
            if hidden:
                with self.assertRaises(ValueError):redeem_voucher(self.s,1,2,voucher['id'],title)
            else:redeem_voucher(self.s,1,2,voucher['id'],title)
        self.assertEqual(item_rows(self.s,2)[0]['quantity'],1)
        self.assertEqual(len(rows(self.s,1,2)),2)
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


class HiddenRoleSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_tag_picker_enforces_compact_role_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_community(s)
            profile_rows=[];rid=100
            with s.connect() as db:
                for kind,(_,names,_) in PROFILE_TAGS.items():
                    for position,name in enumerate(names,1):
                        db.execute('INSERT INTO profile_roles VALUES (?,?,?,?)',(kind,name,rid,position))
                        profile_rows.append((kind,name,rid,position));rid+=1
            member=SimpleNamespace(roles=[]);games=[(name,200+n) for n,name in enumerate(('발로란트','롤'))]
            view=ProfileTagsView(s,2,member,profile_rows,games)
            selects=[c for c in view.children if isinstance(c,discord.ui.Select)]
            self.assertEqual([c.max_values for c in selects],[1,2,1,2])
            self.assertEqual(len(profile_rows),14)

    async def test_legacy_series_rewards_become_one_global_hidden_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_titles(s)
            visible=[]
            for series,kind,start in [('설월의 시','sale',10),('인연의 서약','grant',20)]:
                for tier in range(1,5):
                    tid=create_title(s,GUILD_ID,f'{series}{tier}',start+tier,kind,100 if kind=='sale' else 0,False,9,series)
                    visible.append(tid)
                    with s.connect() as db:db.execute('UPDATE titles SET tier=? WHERE id=?',(tier,tid))
            old_a=create_title(s,GUILD_ID,'설월무극',31,'grant',0,True,9,'설월의 시')
            old_b=create_title(s,GUILD_ID,'연화불멸',32,'grant',0,True,9,'인연의 서약')
            with s.connect() as db:
                db.execute('UPDATE titles SET tier=5 WHERE id IN (?,?)',(old_a,old_b))
                db.execute('INSERT INTO title_collections VALUES (?,?,?)',('설월의 시',json.dumps(visible[:4]),old_a))
                db.execute('INSERT INTO title_collections VALUES (?,?,?)',('인연의 서약',json.dumps(visible[4:]),old_b))
            role_a=SimpleNamespace(id=31,name='⟡ 설월무극',colour=SimpleNamespace(value=0xC59A3C),edit=AsyncMock(),delete=AsyncMock())
            role_b=SimpleNamespace(id=32,name='⟡ 연화불멸',colour=SimpleNamespace(value=0xC59A3C),edit=AsyncMock(),delete=AsyncMock())
            guild=Mock();guild.id=GUILD_ID;guild.fetch_roles=AsyncMock(return_value=[role_a,role_b]);guild.create_role=AsyncMock()
            with patch('titles_ui.role_safe'):
                await ensure_collections(guild,s,Mock())
            catalog=rows(s,GUILD_ID);hidden=[r for r in catalog if r['hidden']]
            self.assertEqual([(r['name'],r['series']) for r in hidden],[('설연무궁','설온의 비록')])
            self.assertEqual(len([r for r in catalog if not r['hidden']]),8)
            with s.connect() as db:
                self.assertEqual(db.execute('SELECT series,reward FROM title_collections').fetchall(),[('전체 수집 보상',hidden[0]['id'])])
            role_a.edit.assert_awaited_once();role_b.delete.assert_awaited_once();guild.create_role.assert_not_awaited()

    async def test_hidden_role_is_added_and_removed_when_new_sale_title_appears(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_titles(s)
            sale=create_title(s,1,'구매형',10,'sale',100,False,9)
            given=create_title(s,1,'지급형',11,'grant',0,False,9)
            reward=create_title(s,1,'히든',12,'grant',0,True,9)
            with s.connect() as db:
                db.execute('UPDATE titles SET tier=5 WHERE id=?',(reward,))
                db.execute('INSERT INTO title_collections VALUES (?,?,?)',('세트',json.dumps([sale]),reward))
            with s.connect() as db:db.execute('INSERT INTO wallet VALUES (1,2,1000)')
            purchase(s,1,2,sale);grant(s,1,2,given,9)
            hidden_role=Mock();hidden_role.id=12
            member=Mock();member.id=2;member.roles=[];member.add_roles=AsyncMock();member.remove_roles=AsyncMock()
            guild=Mock();guild.id=1;guild.fetch_member=AsyncMock(return_value=member);guild.get_role.return_value=hidden_role
            with patch('titles_ui.role_safe'):
                await sync_hidden_roles(guild,s,{2})
            member.add_roles.assert_awaited_once()
            create_title(s,1,'새 구매형',13,'sale',100,False,9)
            claim_completed(s,1,2);member.roles=[hidden_role]
            with patch('titles_ui.role_safe'):
                await sync_hidden_roles(guild,s,{2})
            member.remove_roles.assert_awaited_once()

    async def test_owner_gets_all_current_titles_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_titles(s)
            create_title(s,1,'하나',10,'sale',100,False,9)
            create_title(s,1,'둘',11,'grant',0,False,9)
            role_a,role_b=Mock(),Mock();role_a.id,role_b.id=10,11
            member=Mock();member.roles=[];member.add_roles=AsyncMock()
            guild=Mock();guild.id=1;guild.fetch_member=AsyncMock(return_value=member)
            guild.get_role.side_effect=lambda rid:{10:role_a,11:role_b}[rid]
            with patch('titles_ui.role_safe'):
                await grant_owner_test_titles(guild,s,99,Mock())
                await grant_owner_test_titles(guild,s,99,Mock())
            self.assertEqual({r['id'] for r in rows(s,1,99)},{1,2})
            member.add_roles.assert_awaited_once()

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
