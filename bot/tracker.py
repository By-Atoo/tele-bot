import asyncio
import logging
import threading
import time
from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline
from config import get_config

logger = logging.getLogger(__name__)

def start_online_tracker(bot):
    tracker_cfg = get_config().get('online_tracker', {})
    if not tracker_cfg.get('enabled', False):
        logger.info("Мониторинг онлайн-статуса отключён.")
        return

    api_id = tracker_cfg.get('api_id')
    api_hash = tracker_cfg.get('api_hash')
    tracked_usernames = tracker_cfg.get('tracked_usernames', [])
    notification_chat_id = tracker_cfg.get('notification_chat_id')
    check_interval = tracker_cfg.get('check_interval', 30)

    if not api_id or not api_hash or not tracked_usernames:
        logger.warning("Неполная конфигурация online_tracker. Трекер не запущен.")
        return

    async def tracker_task():
        client = TelegramClient('online_tracker_session', api_id, api_hash)
        await client.start()
        logger.info(f"Трекер онлайн-статуса запущен. Отслеживаем: {tracked_usernames}")

        prev_status = {username: None for username in tracked_usernames}
        login_time = {}

        try:
            while True:
                for username in tracked_usernames:
                    try:
                        entity = await client.get_entity(username)
                        is_online = isinstance(entity.status, UserStatusOnline)

                        if prev_status[username] is not None and prev_status[username] != is_online:
                            if is_online:
                                status_text = "🟢 вошёл в сеть"
                                login_time[username] = time.time()
                            else:
                                if username in login_time:
                                    duration_sec = time.time() - login_time[username]
                                    hours, remainder = divmod(duration_sec, 3600)
                                    minutes, seconds = divmod(remainder, 60)
                                    status_text = f"🔴 вышел из сети (был онлайн: {int(hours)}ч {int(minutes)}м {int(seconds)}с)"
                                    del login_time[username]
                                else:
                                    status_text = "🔴 вышел из сети"
                            try:
                                await client.send_message(
                                    notification_chat_id,
                                    f"👤 @{username} {status_text}"
                                )
                            except Exception as e:
                                logger.error(f"Не удалось отправить уведомление в Telegram: {e}")
                            logger.info(f"@{username} {status_text}")

                        prev_status[username] = is_online
                    except Exception as e:
                        logger.error(f"Ошибка при проверке @{username}: {e}")

                await asyncio.sleep(check_interval)
        finally:
            await client.disconnect()

    def run_async_loop():
        asyncio.run(tracker_task())

    threading.Thread(target=run_async_loop, daemon=True).start()