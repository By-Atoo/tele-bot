import logging
import signal
import threading
import time
import telebot
from config import load_config, apply_config, start_config_watcher, get_config
from database import Database
from handlers import register_handlers
from admin import register_admin_callbacks
from api import create_app
from tracker import start_online_tracker

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def cleanup_loop():
    from state import user_histories, user_last_activity, user_system_prompts, state_lock
    while True:
        time.sleep(3600)
        with state_lock:
            now = time.time()
            for uid in list(user_histories.keys()):
                if now - user_last_activity.get(uid, 0) > 3600:
                    user_histories.pop(uid, None)
                    user_last_activity.pop(uid, None)
                    user_system_prompts.pop(uid, None)
                    logger.debug(f"Очищена история пользователя {uid}")

def main():
    config = load_config()
    apply_config(config)
    observer = start_config_watcher()

    db = Database(config['db_filename'])
    bot = telebot.TeleBot(config['bot_token'])

    register_handlers(bot, db)
    register_admin_callbacks(bot, db)

    start_online_tracker(bot)
    threading.Thread(target=cleanup_loop, daemon=True).start()

    app = create_app(db, bot)

    def run_bot():
        logger.info("Запуск Telegram бота...")
        bot.infinity_polling(timeout=20, long_polling_timeout=20)

    def shutdown_handler(signum, frame):
        logger.info("Останавливаем бота...")
        bot.stop_polling()
        observer.stop()
        observer.join()
        time.sleep(1)
        logger.info("Бот остановлен.")

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    api_host = config['api_host']
    api_port = config['api_port']
    logger.info(f"Запуск Flask API на http://{api_host}:{api_port}")
    try:
        app.run(host=api_host, port=api_port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_handler(None, None)

if __name__ == '__main__':
    main()