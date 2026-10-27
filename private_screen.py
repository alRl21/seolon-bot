"""One private message per member, edited by all first-party member menus."""
import time
import asyncio
import discord

_SESSIONS={}
_LOCK=asyncio.Lock()

async def render(interaction,content=None,embed=None,view=None):
    payload={'content':content,'embed':embed,'view':view}
    if interaction.response.is_done() is True:
        extras=getattr(interaction,'extras',{})
        origin=extras.get('seolon_screen_origin',interaction) if isinstance(extras,dict) else interaction
        await origin.edit_original_response(**payload)
        return
    private=getattr(getattr(getattr(interaction,'message',None),'flags',None),'ephemeral',False) is True
    if private:
        await interaction.response.edit_message(**payload)
        return
    async with _LOCK:
        await render_public(interaction,payload)


async def render_public(interaction,payload):
    now=time.monotonic()
    for key,(_,created) in list(_SESSIONS.items()):
        if now-created>840:_SESSIONS.pop(key,None)
    key=(interaction.guild_id,interaction.user.id)
    previous=_SESSIONS.get(key)
    if previous:
        try:
            await previous[0].edit_original_response(**payload)
        except discord.HTTPException:
            _SESSIONS.pop(key,None)
        else:
            # Acknowledge the public button without posting another private reply.
            interaction.extras['seolon_screen_origin']=previous[0]
            await interaction.response.defer()
            return
    await interaction.response.send_message(**payload,ephemeral=True)
    _SESSIONS[key]=(interaction,now)
