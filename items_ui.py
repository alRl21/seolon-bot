import discord
from core import GUILD_ID
from private_screen import render
from items_store import item_rows,buy_item,use_theme,redeem_voucher


class StoreMenu(discord.ui.View):
    def __init__(self,store,user):
        super().__init__(timeout=300);self.store,self.user=store,user
    async def interaction_check(self,i):
        from titles_ui import available
        if i.user.id!=self.user:return False
        available(i);return True
    async def on_error(self,i,error,item):
        await render(i,content=str(error)[:500],view=StoreMenu(self.store,self.user))
    @discord.ui.button(label='칭호',style=discord.ButtonStyle.primary)
    async def titles(self,i,b):
        from titles_ui import show_series,member_choices
        await show_series(i,self.store,member_choices(self.store,self.user,'shop'),'shop')
    @discord.ui.button(label='아이템',style=discord.ButtonStyle.primary)
    async def items(self,i,b):await render(i,content='아이템 상점',view=ItemPicker(self.store,self.user,False))
    @discord.ui.button(label='내 가방',style=discord.ButtonStyle.secondary)
    async def bag(self,i,b):
        choices=item_rows(self.store,self.user)
        await render(i,content='내 가방' if choices else '아직 보유한 아이템이 없어요.',view=ItemPicker(self.store,self.user,True) if choices else StoreMenu(self.store,self.user))
    @discord.ui.button(label='내 프로필',style=discord.ButtonStyle.secondary)
    async def profile(self,i,b):
        from community_ui import show_profile
        await show_profile(i,self.store)


class ItemPicker(StoreMenu):
    def __init__(self,store,user,bag):
        super().__init__(store,user);self.clear_items()
        choices=item_rows(store,user if bag else None)
        select=discord.ui.Select(placeholder='사용할 아이템' if bag else '구매할 아이템',options=[discord.SelectOption(label=r['name'],value=str(r['id']),description=f"보유 {r['quantity']}개" if bag else f"{r['price']:,}눈송이") for r in choices[:25]])
        async def pick(i):
            row=next(r for r in item_rows(store) if r['id']==int(select.values[0]))
            desc=('관리자가 지급하는 특별 아이템이에요. 히든을 제외한 미보유 칭호라면 종류·가격·판매 상태와 관계없이 하나를 선택해 교환할 수 있어요.' if row['kind']=='voucher' else f"{row['price']:,}눈송이\n프로필 카드 색을 변경하는 영구 보유 테마예요.")
            await render(i,embed=discord.Embed(title=row['name'],description=desc,colour=row['value'] if row['kind']=='theme' else 0xB8C8D8),view=ItemAction(store,user,row,bag))
        select.callback=pick;self.add_item(select);add_back(self,store,user)


def add_back(view,store,user):
    button=discord.ui.Button(label='상점 메뉴')
    async def back(i):await render(i,content='상점 · 칭호와 아이템 중 선택하세요.',view=StoreMenu(store,user))
    button.callback=back;view.add_item(button)


class ItemAction(StoreMenu):
    def __init__(self,store,user,row,bag):
        super().__init__(store,user);self.clear_items()
        button=discord.ui.Button(label='사용하기' if bag else '구매하기',style=discord.ButtonStyle.primary)
        async def action(i):
            await render(i,content='처리 중이에요.')
            from titles_ui import TITLE_LOCK,available
            if bag and row['kind']=='voucher':
                from titles_store import rows
                owned={r['id'] for r in rows(store,GUILD_ID,user)}
                choices=[r for r in rows(store,GUILD_ID) if not r['hidden'] and r['id'] not in owned]
                if not choices:
                    await render(i,content='교환 가능한 미보유 칭호가 없어요. 교환권은 사용하지 않았어요.',view=StoreMenu(store,user));return
                await render(i,content='교환할 칭호를 선택하세요. 선택하면 교환권 1장이 사용됩니다.',view=VoucherPicker(store,user,row['id'],choices));return
            async with TITLE_LOCK:
                available(i)
                if bag:use_theme(store,user,row['id']);text='프로필 테마를 적용했어요.'
                else:
                    cost=buy_item(store,GUILD_ID,user,row['id']);text=f'{row["name"]} 구매 완료 · 눈송이 -{cost:,}개\n내 가방에서 사용할 수 있어요.'
            await render(i,content=text,view=StoreMenu(store,user))
        button.callback=action;self.add_item(button);add_back(self,store,user)


class VoucherPicker(StoreMenu):
    def __init__(self,store,user,item,choices):
        super().__init__(store,user);self.clear_items()
        select=discord.ui.Select(placeholder='칭호 교환 · 취소하려면 상점 메뉴',options=[discord.SelectOption(label=r['name'],value=str(r['id'])) for r in choices[:25]])
        async def pick(i):
            await i.response.defer()
            from titles_ui import TITLE_LOCK,role_safe,available
            from titles_store import get_title
            tid=int(select.values[0]);title=get_title(store,GUILD_ID,tid)
            role_safe(i.guild,i.guild.get_role(title['role']))
            async with TITLE_LOCK:
                available(i)
                redeem_voucher(store,GUILD_ID,user,item,tid)
                from collection_rewards import sync_hidden_roles
                await sync_hidden_roles(i.guild,store,{user})
            await render(i,content='칭호를 교환했어요. 내 칭호에서 착용할 수 있어요.',view=StoreMenu(store,user))
        select.callback=pick;self.add_item(select);add_back(self,store,user)
