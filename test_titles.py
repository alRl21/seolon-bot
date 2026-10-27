import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock,AsyncMock,patch
import discord
from core import Store,GUILD_ID
from titles_store import initialise_titles,create_title,configure_sale,purchase,grant,revoke,rows,equipped
from titles_ui import wear,admin,ADMIN_COMMANDS

class TitleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'db');initialise_titles(self.s)
        self.t=create_title(self.s,1,'첫눈',50,'sale',100,False,9)
        with self.s.connect() as db:
            for user in [2,3]: db.execute('INSERT INTO wallet VALUES (?,?,?)',(1,user,1000))
    def tearDown(self):self.tmp.cleanup()
    def test_parallel_duplicate_charged_once_and_restart_preserves_inventory(self):
        def buy(_):
            try: purchase(self.s,1,2,self.t);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(buy,range(8))),1)
        self.assertEqual(self.s.profile(1,2)[0],900)
        self.assertEqual(len(rows(Store(self.s.path),1,2)),1)
    def test_limited_last_item_cannot_oversell(self):
        with self.s.connect() as db:db.execute("UPDATE titles SET kind='limited' WHERE id=?",(self.t,))
        configure_sale(self.s,1,self.t,100,None,1,9)
        def buy(user):
            try:purchase(self.s,1,user,self.t);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sum(pool.map(buy,[2,3])),1)
        self.assertEqual(sum(self.s.profile(1,u)[0] for u in [2,3]),1900)
    def test_grant_only_never_sells_and_duplicate_grant_is_idempotent(self):
        tid=create_title(self.s,1,'설온의 시작',51,'grant',0,True,9)
        with self.assertRaises(ValueError):configure_sale(self.s,1,tid,10,None,None,9)
        with self.assertRaises(ValueError):purchase(self.s,1,2,tid)
        self.assertTrue(grant(self.s,1,2,tid,9));self.assertFalse(grant(self.s,1,2,tid,9))
        self.assertEqual(self.s.profile(1,2)[0],1000)
    def test_expiry_and_stop_preserve_owned_titles(self):
        configure_sale(self.s,1,self.t,100,9999999999,None,9)
        with self.assertRaises(ValueError):purchase(self.s,1,2,self.t,now=9999999999)
        purchase(self.s,1,2,self.t,now=100)
        with self.s.connect() as db:db.execute('UPDATE titles SET selling=0')
        equipped(self.s,1,2,self.t)
        self.assertEqual(len(rows(self.s,1,2)),1)
        with self.assertRaises(ValueError):purchase(self.s,1,3,self.t)
    def test_insufficient_wallet_no_side_effects(self):
        with self.assertRaises(ValueError):purchase(self.s,1,77,self.t)
        self.assertEqual(rows(self.s,1,77),[])
        self.assertEqual(rows(self.s,1)[0]['sold'],0)
    def test_revoke_clears_equipped_without_refunding(self):
        purchase(self.s,1,2,self.t);equipped(self.s,1,2,self.t);revoke(self.s,1,2,self.t,9)
        self.assertEqual(rows(self.s,1,2),[])
        self.assertEqual(self.s.profile(1,2)[0],900)
        with self.s.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM title_equipped').fetchone()[0],0)

class RoleWearTests(unittest.IsolatedAsyncioTestCase):
    async def test_equipping_changes_only_registered_title_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_titles(s)
            a=create_title(s,GUILD_ID,'a',10,'grant',0,False,9)
            b=create_title(s,GUILD_ID,'b',11,'grant',0,False,9)
            grant(s,GUILD_ID,2,a,9);grant(s,GUILD_ID,2,b,9)
            old,new,clan,other=[Mock() for _ in range(4)]
            for r,rid in zip([old,new,clan,other],[10,11,1522946198984593541,90]):r.id=rid
            member=Mock();member.id=2;member.roles=[clan,other,old]
            member.remove_roles=AsyncMock();member.add_roles=AsyncMock()
            fresh=Mock();fresh.roles=[clan,other,new]
            i=Mock();i.user.id=2;i.guild.fetch_member=AsyncMock(side_effect=[member,fresh]);i.guild.get_role.return_value=new
            with patch('titles_ui.role_safe'):
                await wear(i,s,b)
            member.remove_roles.assert_awaited_once_with(old,reason='보유 칭호 변경 · 장식 역할만 변경')
            member.add_roles.assert_awaited_once_with(new,reason='보유 칭호 착용')
    async def test_regular_member_not_allowed_admin_commands(self):
        i=Mock();i.guild_id=GUILD_ID;i.user.id=2;i.permissions.administrator=False;i.client.closing_for_backup=False
        with self.assertRaises(discord.app_commands.CheckFailure):admin(i)
        i.permissions.administrator=True;admin(i)

class PricingAndSeriesTests(unittest.TestCase):
    def test_first_title_requires_fifteen_daily_attendances(self):
        from datetime import date,timedelta
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_titles(s)
            title=create_title(s,1,'첫눈',50,'sale',650,False,9,'겨울')
            start=date(2026,1,1)
            for n in range(14):s.attend(1,2,start+timedelta(days=n))
            self.assertEqual(s.profile(1,2)[0],620)
            with self.assertRaises(ValueError):purchase(s,1,2,title)
            s.attend(1,2,start+timedelta(days=14))
            purchase(s,1,2,title)
            self.assertEqual(s.profile(1,2)[0],0)
            self.assertEqual(rows(s,1,2)[0]['series'],'겨울')

    def test_series_persists_and_never_changes_grant_only_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db');initialise_titles(s)
            title=create_title(s,1,'설온의 시작',50,'grant',0,True,9,'기념')
            grant(s,1,2,title,9)
            restored=Store(s.path)
            self.assertEqual(rows(restored,1,2)[0]['series'],'기념')
            with self.assertRaises(ValueError):purchase(restored,1,3,title)
