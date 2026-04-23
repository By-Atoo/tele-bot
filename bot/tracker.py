import asyncio
import time
from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline
from config import cfg_manager
from bot import bot  # экземпляр aiogram бота

async def run_tracker():
    t = cfg_manager.config.online_tracker
    if not t.get("enabled"):
        return
    client = TelegramClient('session_tracker', t['api_id'], t['api_hash'])
    await client.start()
    last_status = {}
    login_time = {}
    while True:
        for u in t['tracked_usernames']:
            try:
                ent = await client.get_entity(u)
                online = isinstance(ent.status, UserStatusOnline)
                if u not in last_status:
                    last_status[u] = online
                    continue
                if online != last_status[u]:
                    if online:
                        login_time[u] = time.time()
                        await bot.send_message(t['notification_chat_id'], f"🟢 @{u} вошёл")
                    else:
                        if u in login_time:
                            sec = time.time() - login_time[u]
                            h, rem = divmod(sec, 3600)
                            m, s = divmod(rem, 60)
                            dur = f"{int(h)}ч {int(m)}м {int(s)}с"
                            await bot.send_message(t['notification_chat_id'], f"🔴 @{u} вышел (был онлайн {dur})")
                            del login_time[u]
                        else:
                            await bot.send_message(t['notification_chat_id'], f"🔴 @{u} вышел")
                    last_status[u] = online
            except Exception as e:
                pass
        await asyncio.sleep(t['check_interval'])