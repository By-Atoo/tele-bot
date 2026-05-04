import json
import logging
import os
import time
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from telebot import apihelper

logger = logging.getLogger(__name__)

CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {
    "bot_token": "",
    "admin_chat_id": 5372601405,
    "api_host": "",
    "api_port": 8080,
    "api_secret": "",
    "db_filename": "",
    "ai_api_key": "",
    "ai_api_url": "",
    "ai_model": "",
    "system_prompt": "",
    "proxy_url": None,
    "online_tracker": {
        "enabled": True,
        "api_id": 2040,
        "api_hash": "",
        "tracked_usernames": [],
        "notification_chat_id": 5372601405,
        "check_interval": 30
    }
}

_config = {}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        logger.info(f"Создан файл конфигурации {CONFIG_FILE}")
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def apply_config(config_dict):
    global _config
    _config = config_dict

    # Прокси
    if config_dict.get('proxy_url'):
        apihelper.proxy = {'https': config_dict['proxy_url']}
    else:
        apihelper.proxy = None

    # При перезагрузке сбрасываем AI-клиент, чтобы он пересоздался с новыми ключами
    # (используем импорт ai только при необходимости, чтобы избежать циклических импортов)
    try:
        import ai
        ai.ai_client = None
    except Exception:
        pass

    logger.info("Конфигурация применена.")

def get_config():
    return _config

class ConfigFileEventHandler(FileSystemEventHandler):
    last_reload = 0
    def on_modified(self, event):
        if event.src_path.endswith(CONFIG_FILE):
            now = time.time()
            if now - ConfigFileEventHandler.last_reload > 2:
                ConfigFileEventHandler.last_reload = now
                logger.info(f"Файл {CONFIG_FILE} изменён, перезагружаем конфигурацию...")
                try:
                    new_config = load_config()
                    apply_config(new_config)
                    logger.info("Конфигурация успешно перезагружена.")
                except Exception as e:
                    logger.error(f"Ошибка при перезагрузке конфигурации: {e}")

def start_config_watcher():
    observer = Observer()
    observer.schedule(ConfigFileEventHandler(), path='.', recursive=False)
    observer.start()
    return observer