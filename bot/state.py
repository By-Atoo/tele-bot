from collections import defaultdict
from threading import Lock

state_lock = Lock()
user_states = {}                     # {chat_id: {action: ..., ...}}
user_histories = defaultdict(list)   # {user_id: [messages]}
user_system_prompts = {}             # {user_id: str}
user_last_activity = {}              # {user_id: timestamp}