# handlers.py
import logging, os, sys, time
from datetime import datetime
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import get_config
from utils import escape_markdown_v2, split_message, format_as_quote, safe_send
from ai import ask_ai, clear_all_histories, clear_user_history
import state, messages

logger = logging.getLogger(__name__)

def register_handlers(bot, db):
    cfg = get_config()
    ADMIN_CHAT_ID = cfg['admin_chat_id']

    def is_admin(chat_id):
        return chat_id == ADMIN_CHAT_ID

    def send_and_remember(chat_id, text, **kwargs):
        msg = safe_send(bot, chat_id, text, **kwargs)
        if msg:
            state.bot_messages[chat_id].append(msg.message_id)
        return msg

    def remember_user_message(chat_id, message_id):
        state.bot_messages[chat_id].append(message_id)

    def send_to_admin(message_obj, text):
        user = message_obj.from_user
        user_info = f"👤 {user.first_name or ''} {user.last_name or ''}"
        if user.username: user_info += f" (@{user.username})"
        user_info += f" [ID:{user.id}]"
        adm_text = (f"📨 *Сообщение от пользователя:*\n"
                    f"{escape_markdown_v2(user_info)}\n\n💬 {escape_markdown_v2(text)}")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💬 Ответить", callback_data=f"reply_to_user_{user.id}"))
        try:
            send_and_remember(ADMIN_CHAT_ID, adm_text, reply_markup=markup, parse_mode='MarkdownV2')
            return True
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
            return False

    def find_user_by_username(username):
        users = db.get_all_users()
        for u in users:
            if u['username'] and u['username'].lower() == username.lower():
                return u
        return None

    def send_message_to_user(user_id, text, sender_display=None, reply_to_user_id=None):
        """
        Отправляет сообщение пользователю. Если указан sender_display, добавляет префикс.
        Если передан reply_to_user_id, добавляет кнопку «Ответить» с callback на отправителя.
        """
        if sender_display:
            full_text = f"📨 *От {escape_markdown_v2(sender_display)}:*\n{escape_markdown_v2(text)}"
        else:
            full_text = escape_markdown_v2(text)

        kwargs = {'parse_mode': 'MarkdownV2'}
        if reply_to_user_id:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("💬 Ответить", callback_data=f"reply_to_user_{reply_to_user_id}"))
            kwargs['reply_markup'] = markup

        try:
            send_and_remember(user_id, full_text, **kwargs)
            return True
        except Exception as e:
            logger.error(f"Failed to send to {user_id}: {e}")
            return False

    def send_broadcast(text, admin_chat_id=None):
        users = db.get_all_users()
        success, fail = [], []
        for u in users:
            if send_message_to_user(u['user_id'], text): success.append(u)
            else: fail.append(u)
        if admin_chat_id:
            errors_list = ""
            if fail:
                errors_list = "Ошибки у:\n" + "\n".join(
                    f"`{u['user_id']}` {escape_markdown_v2(u.get('first_name','') or '')}" for u in fail)
            report = messages.BROADCAST_STATS.format(success=len(success), fail=len(fail), errors_list=errors_list)
            send_and_remember(admin_chat_id, report, parse_mode='MarkdownV2')
        return success, fail

    def restart_bot(bot_ref):
        try:
            bot_ref.stop_polling()
        except: pass
        finally:
            python = sys.executable
            args = [python] + sys.argv
            logger.info(f"Restarting: {args}")
            os.execv(python, args)

    def check_ban(user_id):
        return user_id in state.banned_users

    def check_cooldown(user_id):
        if user_id not in state.cooldowns:
            return True, 0
        _, ts = state.cooldowns[user_id]
        now = time.time()
        if now - ts >= 600:
            with state.state_lock: del state.cooldowns[user_id]
            return True, 0
        return False, int(600 - (now - ts))

    def clear_chat(chat_id, send_start=False):
        with state.state_lock:
            mids = state.bot_messages.pop(chat_id, [])
        deleted = 0
        for mid in mids:
            try:
                bot.delete_message(chat_id, mid)
                deleted += 1
            except: pass
        if send_start:
            send_and_remember(chat_id, messages.START_MSG)
        return deleted

    # ---------- Commands ----------
    @bot.message_handler(commands=['start'])
    def start_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        send_and_remember(m.chat.id, messages.START_MSG)

    @bot.message_handler(commands=['help'])
    def help_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        send_and_remember(m.chat.id, messages.HELP_MSG, parse_mode='MarkdownV2')

    @bot.message_handler(commands=['reset'])
    def reset_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        if is_admin(m.chat.id):
            clear_all_histories()
            send_and_remember(m.chat.id, messages.RESET_ADMIN)
        else:
            clear_user_history(m.from_user.id)
            send_and_remember(m.chat.id, messages.RESET_USER)

    @bot.message_handler(commands=['promt'])
    def prompt_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        user_id = m.from_user.id
        parts = m.text.strip().split(maxsplit=1)
        if len(parts) == 1:
            sent = send_and_remember(m.chat.id, messages.PROMPT_ASK)
            with state.state_lock:
                state.user_states[m.chat.id] = {'action':'awaiting_prompt','command_msg_id':m.message_id,'hint_msg_id':sent.message_id}
            return
        new_prompt = parts[1].strip()
        if not new_prompt:
            send_and_remember(m.chat.id, messages.PROMPT_EMPTY)
            return
        with state.state_lock:
            state.user_system_prompts[user_id] = new_prompt
        clear_user_history(user_id)
        send_and_remember(m.chat.id, messages.PROMPT_SET)

    @bot.message_handler(commands=['promt_default'])
    def prompt_default_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        user_id = m.from_user.id
        with state.state_lock: state.user_system_prompts.pop(user_id, None)
        clear_user_history(user_id)
        send_and_remember(m.chat.id, messages.PROMPT_CLEAR_DEFAULT)

    @bot.message_handler(commands=['promt_check'])
    def prompt_check_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        user_id = m.from_user.id
        with state.state_lock:
            prompt = state.user_system_prompts.get(user_id, cfg['system_prompt'])
        full = escape_markdown_v2(prompt)
        send_and_remember(m.chat.id, messages.PROMPT_CHECK.format(prompt=full), parse_mode='MarkdownV2')

    @bot.message_handler(commands=['clear'])
    def clear_chat_cmd(m):
        db.save_user(m.from_user)
        deleted = clear_chat(m.chat.id)
        if deleted == 0:
            send_and_remember(m.chat.id, "Нет сохранённых сообщений для удаления.")
        else:
            send_and_remember(m.chat.id, f"✅ Удалено {deleted} сообщений.")

    @bot.message_handler(commands=['clear_all'])
    def clear_all_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.ADMIN_DENIED)
            return
        users = db.get_all_users()
        total = 0
        for u in users:
            total += clear_chat(u['user_id'], send_start=True)
        total += clear_chat(m.chat.id, send_start=True)
        send_and_remember(m.chat.id, f"✅ Удалено {total} сообщений по всем чатам и отправлено приветствие.")

    @bot.message_handler(commands=['cancel'])
    def cancel_cmd(m):
        db.save_user(m.from_user)
        with state.state_lock: st = state.user_states.pop(m.chat.id, None)
        if st:
            action = st.get('action')
            if action in ('awaiting_admin_message','awaiting_prompt','awaiting_reply','awaiting_broadcast','awaiting_ai_logs_clear_user'):
                try: bot.delete_message(m.chat.id, st['hint_msg_id'])
                except: pass
                send_and_remember(m.chat.id, messages.CANCEL_DONE)
            else:
                send_and_remember(m.chat.id, messages.CANCEL_NOTHING)
        else:
            send_and_remember(m.chat.id, messages.CANCEL_NOTHING)
        try: bot.delete_message(m.chat.id, m.message_id)
        except: pass

    @bot.message_handler(commands=['mA','ma'])
    def contact_admin(m):
        db.save_user(m.from_user)
        if not ADMIN_CHAT_ID:
            send_and_remember(m.chat.id, "⚠️ Не настроено.")
            return
        parts = m.text.strip().split(maxsplit=1)
        if len(parts) == 1:
            sent = send_and_remember(m.chat.id, messages.MA_ASK)
            with state.state_lock:
                state.user_states[m.chat.id] = {'action':'awaiting_admin_message','command_msg_id':m.message_id,'hint_msg_id':sent.message_id}
            return
        user_msg = parts[1].strip()
        if not user_msg:
            send_and_remember(m.chat.id, messages.MA_EMPTY)
            return
        if send_to_admin(m, user_msg):
            send_and_remember(m.chat.id, messages.MA_SENT)
            try: bot.delete_message(m.chat.id, m.message_id)
            except: pass
        else:
            send_and_remember(m.chat.id, messages.MA_FAIL)

    @bot.message_handler(commands=['admin'])
    def admin_panel(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.ADMIN_DENIED)
            return
        from admin import show_admin_main
        show_admin_main(bot, m.chat.id)

    @bot.message_handler(commands=['m'])
    def message_cmd(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        parts = m.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            send_and_remember(m.chat.id, messages.M_USAGE, parse_mode='MarkdownV2')
            return
        target = parts[1].strip()
        message_text = parts[2].strip() if len(parts) > 2 else None

        if target.lower() == 'all':
            if not is_admin(m.chat.id):
                send_and_remember(m.chat.id, messages.ADMIN_DENIED)
                return
            if not message_text:
                sent = send_and_remember(m.chat.id, messages.BROADCAST_ASK)
                with state.state_lock:
                    state.user_states[m.chat.id] = {'action':'awaiting_broadcast','command_msg_id':m.message_id,'hint_msg_id':sent.message_id}
                return
            send_broadcast(message_text, admin_chat_id=m.chat.id)
            return

        try:
            target_id = int(target)
            user = db.get_user_by_id(target_id)
            display = f"@{user['username']} [ID:{target_id}]" if user and user['username'] else f"ID:{target_id}"
        except ValueError:
            target = target.lstrip('@')
            user = find_user_by_username(target)
            if not user:
                send_and_remember(m.chat.id, messages.M_USER_NOT_FOUND)
                return
            target_id = user['user_id']
            display = f"@{user['username']} [ID:{target_id}]" if user['username'] else f"ID:{target_id}"

        if not is_admin(m.chat.id):
            can_send, cd = check_cooldown(m.from_user.id)
            if not can_send:
                send_and_remember(m.chat.id, messages.M_COOLDOWN_ACTIVE.format(time_left=cd//60))
                return

        if not message_text:
            sent = send_and_remember(m.chat.id, f"✉️ Введите сообщение для {display}.\nДля отмены — /cancel")
            with state.state_lock:
                state.user_states[m.chat.id] = {'action':'awaiting_reply','target_id':target_id,'target_display':display,
                                                'command_msg_id':m.message_id,'hint_msg_id':sent.message_id}
            return

        sender_display = f"@{m.from_user.username}" if m.from_user.username else m.from_user.first_name
        sender_id = m.from_user.id

        if send_message_to_user(target_id, message_text, sender_display=sender_display, reply_to_user_id=sender_id):
            if not is_admin(m.chat.id):
                with state.state_lock: state.cooldowns[sender_id] = (target_id, time.time())
            if ADMIN_CHAT_ID:
                adm_notify = f"📨 Переслано:\n{escape_markdown_v2(sender_display)} [ID:{sender_id}] → {escape_markdown_v2(display)}\n💬 {escape_markdown_v2(message_text)}"
                send_and_remember(ADMIN_CHAT_ID, adm_notify, parse_mode='MarkdownV2')
            send_and_remember(m.chat.id, messages.M_SENT_USER.format(user_display=escape_markdown_v2(display)),
                              parse_mode='MarkdownV2')
        else:
            send_and_remember(m.chat.id, messages.MA_FAIL)

    @bot.message_handler(commands=['users'])
    def list_users_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.ADMIN_DENIED)
            return
        users = db.get_all_users()
        if not users:
            send_and_remember(m.chat.id, "😕 Нет пользователей.")
            return
        text = "👥 *Список пользователей:*\n\n"
        for i, u in enumerate(users,1):
            name = u['first_name'] or ""
            if u['last_name']: name += f" {u['last_name']}"
            username = f"@{u['username']}" if u['username'] else "нет username"
            last_seen = datetime.fromtimestamp(u['last_seen']).strftime("%d.%m.%y %H:%M")
            text += (f"{i}\\. `{u['user_id']}` \\- {escape_markdown_v2(name)} \\({escape_markdown_v2(username)}\\)\n"
                     f"   🕒 *Последняя активность:* {escape_markdown_v2(last_seen)}\n\n")
            if len(text) > 3500:
                send_and_remember(m.chat.id, text, parse_mode='MarkdownV2')
                text = ""
        if text: send_and_remember(m.chat.id, text, parse_mode='MarkdownV2')

    @bot.message_handler(commands=['top'])
    def top_msg(m):
        db.save_user(m.from_user)
        if check_ban(m.from_user.id):
            send_and_remember(m.chat.id, messages.BAN_MESSAGE)
            return
        records = db.get_top_records(20)
        if not records:
            send_and_remember(m.chat.id, "Пусто.")
            return
        text = "🏆 ТАБЛИЦА ЛИДЕРОВ 🏆\n\n"
        for i,r in enumerate(records,1):
            text += f"{i}. {r['name']} — {r['score']} очков ({r['duration']} сек.)\n"
        send_and_remember(m.chat.id, text)

    @bot.message_handler(commands=['reload'])
    def reload_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.ADMIN_DENIED)
            return
        try:
            from config import load_config, apply_config
            new_config = load_config()
            apply_config(new_config)
            clear_all_histories()
        except Exception as e:
            send_and_remember(m.chat.id, f"❌ Ошибка перезагрузки конфига: {e}")
            return
        try: bot.send_message(m.chat.id, messages.RELOAD_OK, parse_mode='MarkdownV2')
        except: pass
        time.sleep(0.5)
        restart_bot(bot)

    @bot.message_handler(commands=['ban'])
    def ban_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.BAN_NOT_ALLOWED)
            return
        parts = m.text.strip().split()
        if len(parts) != 2:
            send_and_remember(m.chat.id, "Использование: /ban user_id")
            return
        target = parts[1]
        try: target_id = int(target)
        except ValueError:
            target = target.lstrip('@')
            user = find_user_by_username(target)
            if not user:
                send_and_remember(m.chat.id, messages.BAN_USER_NOT_FOUND)
                return
            target_id = user['user_id']
        if target_id == m.from_user.id:
            send_and_remember(m.chat.id, messages.BAN_YOURSELF)
            return
        if target_id in state.banned_users:
            send_and_remember(m.chat.id, messages.BAN_ALREADY)
        else:
            with state.state_lock: state.banned_users.add(target_id)
            send_and_remember(m.chat.id, messages.BAN_SUCCESS)

    @bot.message_handler(commands=['unban'])
    def unban_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.BAN_NOT_ALLOWED)
            return
        parts = m.text.strip().split()
        if len(parts) != 2:
            send_and_remember(m.chat.id, "Использование: /unban user_id")
            return
        target = parts[1]
        try: target_id = int(target)
        except ValueError:
            target = target.lstrip('@')
            user = find_user_by_username(target)
            if not user:
                send_and_remember(m.chat.id, messages.BAN_USER_NOT_FOUND)
                return
            target_id = user['user_id']
        with state.state_lock:
            if target_id in state.banned_users:
                state.banned_users.remove(target_id)
                send_and_remember(m.chat.id, messages.UNBAN_SUCCESS)
            else:
                send_and_remember(m.chat.id, messages.UNBAN_NOT_BANNED)

    @bot.message_handler(commands=['id_i'])
    def id_check_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.ADMIN_DENIED)
            return
        parts = m.text.strip().split()
        if len(parts) != 2:
            send_and_remember(m.chat.id, "Использование: /id_i user_id или @username")
            return
        target = parts[1].lstrip('@')
        try: target_id = int(target)
        except ValueError:
            user = find_user_by_username(target)
            if not user:
                send_and_remember(m.chat.id, messages.BAN_USER_NOT_FOUND)
                return
            target_id = user['user_id']
        user = db.get_user_by_id(target_id)
        if not user:
            send_and_remember(m.chat.id, "❌ Пользователь не найден в базе.")
            return
        logs_count = db.count_ai_logs(str(target_id))
        banned = target_id in state.banned_users
        info = (f"👤 *Информация о пользователе*\n\n"
                f"ID: `{user['user_id']}`\n"
                f"Username: @{user['username'] or 'не задан'}\n"
                f"Имя: {user['first_name'] or ''} {user['last_name'] or ''}\n"
                f"Первый вход: {datetime.fromtimestamp(user['first_seen']).strftime('%d.%m.%Y %H:%M')}\n"
                f"Последний вход: {datetime.fromtimestamp(user['last_seen']).strftime('%d.%m.%Y %H:%M')}\n"
                f"Запросов к AI: {logs_count}\n"
                f"Забанен: {'да' if banned else 'нет'}")
        send_and_remember(m.chat.id, info, parse_mode='MarkdownV2')

    # ---------- Text messages (AI) ----------
    @bot.message_handler(content_types=['text'])
    def handle_text(message):
        if message.text and message.text.startswith('/'): return
        remember_user_message(message.chat.id, message.message_id)

        db.save_user(message.from_user)
        user_id = message.from_user.id
        if check_ban(user_id):
            send_and_remember(message.chat.id, messages.BAN_MESSAGE)
            return

        with state.state_lock: st = state.user_states.get(message.chat.id)
        if st:
            action = st.get('action')
            if action == 'awaiting_admin_message':
                text = message.text.strip()
                if not text:
                    send_and_remember(message.chat.id, messages.MA_EMPTY + " Для отмены /cancel")
                    return
                if send_to_admin(message, text):
                    send_and_remember(message.chat.id, messages.MA_SENT)
                else:
                    send_and_remember(message.chat.id, messages.MA_FAIL)
                cleanup_state(message, st)
                return

            elif action == 'awaiting_prompt':
                new_prompt = message.text.strip()
                if not new_prompt:
                    send_and_remember(message.chat.id, messages.PROMPT_EMPTY + " Для отмены /cancel")
                    return
                with state.state_lock: state.user_system_prompts[user_id] = new_prompt
                clear_user_history(user_id)
                send_and_remember(message.chat.id, messages.PROMPT_SET)
                cleanup_state(message, st)
                return

            elif action == 'awaiting_reply':
                reply_text = message.text.strip()
                if not reply_text:
                    send_and_remember(message.chat.id, "⚠️ Сообщение не может быть пустым.")
                    return
                target_id = st['target_id']
                display = st.get('target_display', f"ID:{target_id}")
                sender_display = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
                sender_id = message.from_user.id

                with state.state_lock:
                    if target_id in state.cooldowns:
                        prev_target, _ = state.cooldowns[target_id]
                        if prev_target == sender_id: del state.cooldowns[target_id]

                if send_message_to_user(target_id, reply_text, sender_display=sender_display, reply_to_user_id=sender_id):
                    if not is_admin(message.chat.id):
                        with state.state_lock: state.cooldowns[sender_id] = (target_id, time.time())
                    if ADMIN_CHAT_ID:
                        adm_notify = f"📨 Переслано:\n{escape_markdown_v2(sender_display)} [ID:{sender_id}] → {escape_markdown_v2(display)}\n💬 {escape_markdown_v2(reply_text)}"
                        send_and_remember(ADMIN_CHAT_ID, adm_notify, parse_mode='MarkdownV2')
                    send_and_remember(message.chat.id, messages.M_SENT_USER.format(user_display=escape_markdown_v2(display)),
                                      parse_mode='MarkdownV2')
                else:
                    send_and_remember(message.chat.id, messages.MA_FAIL)
                cleanup_state(message, st)
                return

            elif action == 'awaiting_broadcast':
                broadcast_text = message.text.strip()
                if not broadcast_text:
                    send_and_remember(message.chat.id, "⚠️ Сообщение не может быть пустым.")
                    return
                send_broadcast(broadcast_text, admin_chat_id=message.chat.id)
                cleanup_state(message, st)
                return

            elif action == 'awaiting_ai_logs_clear_user':
                target = message.text.strip().lstrip('@')
                user = None
                if target.isdigit():
                    user = db.get_user_by_id(int(target))
                else:
                    user = find_user_by_username(target)
                if not user:
                    send_and_remember(message.chat.id, "❌ Пользователь не найден.")
                    with state.state_lock: state.user_states.pop(message.chat.id, None)
                    return
                db.delete_ai_logs_by_user(user['user_id'])
                send_and_remember(message.chat.id, f"✅ Логи пользователя @{user['username'] or user['user_id']} удалены.")
                with state.state_lock: state.user_states.pop(message.chat.id, None)
                return

        if ADMIN_CHAT_ID:
            user = message.from_user
            user_str = f"@{user.username}" if user.username else user.first_name
            safe_user = escape_markdown_v2(user_str)
            preview = escape_markdown_v2(message.text[:100])
            if len(message.text) > 100: preview += '…'
            text = messages.NOTIFY_TEXT.format(user=safe_user, preview=preview)
            markup = InlineKeyboardMarkup()
            logs = db.get_ai_logs(limit=1, search_query=str(user.id))
            if logs:
                markup.add(InlineKeyboardButton(messages.VIEW_LOG, callback_data=f"ai_logs_full_{logs[0]['id']}"))
            try: send_and_remember(ADMIN_CHAT_ID, text, reply_markup=markup, parse_mode='MarkdownV2')
            except: pass

        bot.send_chat_action(message.chat.id, 'typing')
        ai_answer = ask_ai(message, message.text, db)
        formatted = format_as_quote(ai_answer)
        for part in split_message(formatted):
            send_and_remember(message.chat.id, part, parse_mode='MarkdownV2')

    def cleanup_state(message, st):
        try:
            bot.delete_message(message.chat.id, st.get('command_msg_id'))
            bot.delete_message(message.chat.id, st.get('hint_msg_id'))
            bot.delete_message(message.chat.id, message.message_id)
        except: pass
        with state.state_lock: state.user_states.pop(message.chat.id, None)

    # ---------- Media ----------
    @bot.message_handler(content_types=['photo','audio','document','voice','video','sticker'])
    def handle_media(message):
        remember_user_message(message.chat.id, message.message_id)
        db.save_user(message.from_user)
        if check_ban(message.from_user.id):
            send_and_remember(message.chat.id, messages.BAN_MESSAGE)
            return
        try:
            if message.content_type == 'photo':
                bot.send_photo(ADMIN_CHAT_ID, message.photo[-1].file_id,
                               caption=f"От @{message.from_user.username}" if message.from_user.username else f"От {message.from_user.first_name}")
            elif message.content_type == 'audio':
                bot.send_audio(ADMIN_CHAT_ID, message.audio.file_id, caption=message.caption)
            elif message.content_type == 'document':
                bot.send_document(ADMIN_CHAT_ID, message.document.file_id, caption=message.caption)
            elif message.content_type == 'voice':
                bot.send_voice(ADMIN_CHAT_ID, message.voice.file_id)
            elif message.content_type == 'video':
                bot.send_video(ADMIN_CHAT_ID, message.video.file_id, caption=message.caption)
            elif message.content_type == 'sticker':
                bot.send_sticker(ADMIN_CHAT_ID, message.sticker.file_id)
        except Exception as e: logger.error(f"Media forward error: {e}")

        user = message.from_user
        user_str = f"@{user.username}" if user.username else user.first_name
        safe_user = escape_markdown_v2(user_str)
        text = messages.NOTIFY_MEDIA.format(user=safe_user, media_type=message.content_type)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💬 Ответить", callback_data=f"reply_to_user_{user.id}"))
        try: send_and_remember(ADMIN_CHAT_ID, text, reply_markup=markup, parse_mode='MarkdownV2')
        except: pass
        send_and_remember(message.chat.id, "Слабо текстом? 😏")

    # ---------- Admin-only states ----------
    @bot.message_handler(func=lambda m: m.chat.id in state.user_states and is_admin(m.chat.id))
    def handle_state_message(message):
        with state.state_lock: user_state = state.user_states.get(message.chat.id)
        if not user_state: return
        action = user_state.get('action')
        if action == 'search':
            query = message.text.strip()
            if len(query) < 2:
                send_and_remember(message.chat.id, "Введите минимум 2 символа для поиска.")
                return
            records = db.search_records_by_name(query)
            if not records:
                send_and_remember(message.chat.id, f"Ничего не найдено по запросу `{escape_markdown_v2(query)}`\\.",
                                  parse_mode='MarkdownV2')
            else:
                from admin import show_records_list
                show_records_list(bot, db, message.chat.id, records=records)
            with state.state_lock: state.user_states.pop(message.chat.id, None)
        elif action == 'ai_logs_search':
            query = message.text.strip()
            if not query:
                send_and_remember(message.chat.id, "Пустой запрос.")
                return
            with state.state_lock: state.user_states[message.chat.id] = {'action':'ai_logs_view','search_query':query,'page':0}
            from admin import show_ai_logs
            show_ai_logs(bot, db, message.chat.id, page=0, search_query=query)
        elif action == 'edit':
            record_id = user_state['record_id']
            field = user_state['field']
            process_edit_value(message, record_id, field)

    def process_edit_value(message, record_id, field):
        if not is_admin(message.chat.id): return
        new_value = message.text.strip()
        if field in ('score','duration'):
            try: new_value = int(new_value)
            except ValueError:
                send_and_remember(message.chat.id, "❌ Ошибка: введите целое число.")
                return
        try:
            db.update_record(record_id, field, new_value)
            send_and_remember(message.chat.id, f"✅ Поле `{field}` обновлено.", parse_mode='MarkdownV2')
            from admin import show_edit_menu
            show_edit_menu(bot, db, message.chat.id, record_id)
        except Exception as e:
            send_and_remember(message.chat.id, f"❌ Ошибка при обновлении: {e}")
        finally:
            with state.state_lock: state.user_states.pop(message.chat.id, None)
