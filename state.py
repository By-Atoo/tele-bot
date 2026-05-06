# state.py
from collections import defaultdict
from threading import Lock

state_lock = Lock()
user_states = {}
user_histories = defaultdict(list)
user_system_prompts = {}
user_last_activity = {}
bot_messages = defaultdict(list)
banned_users = set()
cooldowns = {}
