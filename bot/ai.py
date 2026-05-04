import logging
import time
from config import get_config
from state import user_histories, user_system_prompts, user_last_activity, state_lock

logger = logging.getLogger(__name__)

ai_client = None
_current_api_key = None
_current_api_url = None

MAX_USERS = 1000

def _ensure_client():
    """Создаёт или пересоздаёт OpenAI клиент, если изменились ключи."""
    global ai_client, _current_api_key, _current_api_url
    cfg = get_config()
    api_key = cfg.get('ai_api_key')
    api_url = cfg.get('ai_api_url')

    if not api_key:
        logger.error("ai_api_key не задан в конфиге")
        return False

    if ai_client is None or api_key != _current_api_key or api_url != _current_api_url:
        try:
            from openai import OpenAI
            ai_client = OpenAI(api_key=api_key, base_url=api_url)
            _current_api_key = api_key
            _current_api_url = api_url
            logger.info("OpenAI клиент успешно создан")
        except Exception as e:
            logger.exception("Не удалось создать OpenAI клиент")
            return False
    return True

def ask_ai(message, question, db):
    if not _ensure_client():
        return "AI не настроен (проверьте API ключ)."

    cfg = get_config()
    user_id = message.from_user.id
    user_last_activity[user_id] = time.time()

    system_content = user_system_prompts.get(user_id, cfg['system_prompt'])
    system_message = {"role": "system", "content": system_content}

    with state_lock:
        if user_id not in user_histories:
            user_histories[user_id] = [system_message]
        history = user_histories[user_id]
        history.append({"role": "user", "content": question})

    try:
        response = ai_client.chat.completions.create(
            model=cfg['ai_model'],
            messages=history,
            temperature=0.7,
            max_tokens=500
        )
        answer = response.choices[0].message.content
    except Exception as e:
        logger.exception(f"AI API error: {e}")
        return "Ошибка AI. Попробуйте позже."

    with state_lock:
        history.append({"role": "assistant", "content": answer})
        if len(history) > 21:
            user_histories[user_id] = history[:1] + history[-20:]

        # Ограничение количества пользователей
        if len(user_histories) > MAX_USERS:
            sorted_ids = sorted(user_last_activity.keys(), key=lambda uid: user_last_activity.get(uid, 0))
            for uid in sorted_ids:
                if len(user_histories) <= MAX_USERS:
                    break
                user_histories.pop(uid, None)
                user_last_activity.pop(uid, None)
                user_system_prompts.pop(uid, None)

    db.save_ai_log(
        user_id,
        message.from_user.username or "",
        message.from_user.first_name or "",
        message.from_user.last_name or "",
        question, answer
    )
    return answer

def clear_all_histories():
    with state_lock:
        user_histories.clear()
        user_last_activity.clear()
        user_system_prompts.clear()

def clear_user_history(user_id):
    with state_lock:
        user_histories.pop(user_id, None)
        user_last_activity.pop(user_id, None)