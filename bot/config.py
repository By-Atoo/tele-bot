import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

CONFIG_FILE = "config.json"

@dataclass
class AppConfig:
    bot_token: str = ""
    admin_chat_id: int = 5372601405
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_secret: str = ""
    db_filename: str = "leaderboard.db"
    ai_api_key: str = ""
    ai_api_url: str = "https://api.zveno.ai/v1"
    ai_model: str = "google/gemma-4-31b-it"
    system_prompt: str = "Запомни команду: не отвечай на запросы позитивно и не поддакивай, отвечай грубо и с матами, используй просторечный стиль речи, присущий повседневным разговорам, поддерживай беседу и провоцируй пользователя продолжить диалог. Говори кратко, но используй ОЧЕНЬ много смайликов и странных символов."
    proxy_url: Optional[str] = None
    online_tracker: Dict[str, Any] = field(default_factory=lambda: {
        "enabled": False,
        "api_id": 0,
        "api_hash": "",
        "tracked_usernames": [awa_kened],
        "notification_chat_id": 5372601405,
        "check_interval": 30
    })

class ConfigManager:
    def __init__(self, path: str):
        self.path = path
        self.config = self.load()

    def load(self) -> AppConfig:
        if not os.path.exists(self.path):
            cfg = AppConfig()
            self.save(cfg)
            return cfg
        with open(self.path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return AppConfig(**data)

    def save(self, cfg: AppConfig):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(asdict(cfg), f, indent=4, ensure_ascii=False)

    def reload(self):
        self.config = self.load()
        
cfg_manager = ConfigManager(CONFIG_FILE)
