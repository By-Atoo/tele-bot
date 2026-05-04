import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import get_config
from utils import escape_markdown_v2, split_message, format_as_quote, safe_send
from ai import ask_ai, clear_all_histories, clear_user_history
import state
import messages

logger = logging.getLogger(__name__)


def register_handlers(bot, db):
    cfg = get_config()
    ADMIN_CHAT_ID = cfg['admin_chat_id']
    bot_messages = defaultdict(list)

    def is_admin(chat_id):
        return chat_id == ADMIN_CHAT_ID

    def send_and_remember(chat_id, text, **kwargs):
        msg = safe_send(bot, chat_id, text, **kwargs)
        if msg:
            bot_messages[chat_id].append(msg.message_id)
        return msg

    def send_to_admin(message_obj, text):
        user = message_obj.from_user
        user_info = f"👤 {user.first_name or ''} {user.last_name or ''}"
        if user.username:
            user_info += f" (@{user.username})"
        user_info += f" [ID:{user.id}]"
        adm_text = (
            f"📨 *Сообщение от пользователя:*\n"
            f"{escape_markdown_v2(user_info)}\n\n"
            f"💬 {escape_markdown_v2(text)}"
        )
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💬 Ответить", callback_data=f"reply_to_user_{user.id}"))
        try:
            send_and_remember(ADMIN_CHAT_ID, adm_text, reply_markup=markup, parse_mode='MarkdownV2')
            return True
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение админу: {e}")
            return False

    def find_user_by_username(username):
        users = db.get_all_users()
        for u in users:
            if u['username'] and u['username'].lower() == username.lower():
                return u
        return None

    def send_message_to_user(user_id, text):
        try:
            send_and_remember(user_id, escape_markdown_v2(text), parse_mode='MarkdownV2')
            return True
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            return False

    def send_broadcast(text, admin_chat_id=None):
        users = db.get_all_users()
        success = []
        fail = []
        for u in users:
            if send_message_to_user(u['user_id'], text):
                success.append(u)
            else:
                fail.append(u)
        if admin_chat_id:
            errors_list = ""
            if fail:
                errors_list = "Ошибки у:\n" + "\n".join(
                    f"`{u['user_id']}` {escape_markdown_v2(u.get('first_name', '') or '')}" for u in fail
                )
            report = messages.BROADCAST_STATS.format(
                success=len(success),
                fail=len(fail),
                errors_list=errors_list
            )
            send_and_remember(admin_chat_id, report, parse_mode='MarkdownV2')
        return success, fail

    def restart_bot(bot_ref):
        try:
            bot_ref.stop_polling()
        except Exception as e:
            logger.warning(f"Не удалось остановить поллинг: {e}")
        finally:
            python = sys.executable
            args = [python] + sys.argv
            logger.info(f"Перезапуск: {args}")
            os.execv(python, args)

    # =================== КОМАНДЫ ===================
    @bot.message_handler(commands=['start'])
    def start_cmd(m):
        db.save_user(m.from_user)
        send_and_remember(m.chat.id, messages.START_MSG)

    @bot.message_handler(commands=['help'])
    def help_cmd(m):
        db.save_user(m.from_user)
        send_and_remember(m.chat.id, messages.HELP_MSG, parse_mode='MarkdownV2')

    @bot.message_handler(commands=['reset'])
    def reset_cmd(m):
        db.save_user(m.from_user)
        if is_admin(m.chat.id):
            clear_all_histories()
            send_and_remember(m.chat.id, messages.RESET_ADMIN)
        else:
            clear_user_history(m.from_user.id)
            send_and_remember(m.chat.id, messages.RESET_USER)

    @bot.message_handler(commands=['promt'])
    def prompt_cmd(m):
        db.save_user(m.from_user)
        user_id = m.from_user.id
        parts = m.text.strip().split(maxsplit=1)
        if len(parts) == 1:
            sent = send_and_remember(m.chat.id, messages.PROMPT_ASK)
            with state.state_lock:
                state.user_states[m.chat.id] = {
                    'action': 'awaiting_prompt',
                    'command_msg_id': m.message_id,
                    'hint_msg_id': sent.message_id
                }
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
        user_id = m.from_user.id
        with state.state_lock:
            state.user_system_prompts.pop(user_id, None)
        clear_user_history(user_id)
        send_and_remember(m.chat.id, messages.PROMPT_CLEAR_DEFAULT)

    @bot.message_handler(commands=['clear'])
    def clear_chat_cmd(m):
        db.save_user(m.from_user)
        chat_id = m.chat.id
        mids = bot_messages.get(chat_id, [])
        if not mids:
            send_and_remember(chat_id, "Нет сохранённых сообщений для удаления.")
            return
        deleted = 0
        for mid in mids:
            try:
                bot.delete_message(chat_id, mid)
                deleted += 1
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение {mid}: {e}")
        bot_messages[chat_id] = []
        send_and_remember(chat_id, f"✅ Удалено {deleted} сообщений.")

    @bot.message_handler(commands=['cancel'])
    def cancel_cmd(m):
        db.save_user(m.from_user)
        with state.state_lock:
            st = state.user_states.pop(m.chat.id, None)
        if st:
            action = st.get('action')
            if action in ('awaiting_admin_message', 'awaiting_prompt', 'awaiting_reply', 'awaiting_broadcast'):
                try:
                    bot.delete_message(m.chat.id, st['hint_msg_id'])
                except:
                    pass
                send_and_remember(m.chat.id, messages.CANCEL_DONE)
            else:
                send_and_remember(m.chat.id, messages.CANCEL_NOTHING)
        else:
            send_and_remember(m.chat.id, messages.CANCEL_NOTHING)
        try:
            bot.delete_message(m.chat.id, m.message_id)
        except:
            pass

    @bot.message_handler(commands=['mA'])
    def contact_admin(m):
        db.save_user(m.from_user)
        if not ADMIN_CHAT_ID:
            send_and_remember(m.chat.id, "⚠️ Админ не настроен.")
            return
        parts = m.text.strip().split(maxsplit=1)
        if len(parts) == 1:
            sent = send_and_remember(m.chat.id, messages.MA_ASK)
            with state.state_lock:
                state.user_states[m.chat.id] = {
                    'action': 'awaiting_admin_message',
                    'command_msg_id': m.message_id,
                    'hint_msg_id': sent.message_id
                }
            return
        user_msg = parts[1].strip()
        if not user_msg:
            send_and_remember(m.chat.id, messages.MA_EMPTY)
            return
        if send_to_admin(m, user_msg):
            send_and_remember(m.chat.id, messages.MA_SENT)
            try:
                bot.delete_message(m.chat.id, m.message_id)
            except:
                pass
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
    def admin_message_cmd(m):
        db.save_user(m.from_user)
        if not is_admin(m.chat.id):
            send_and_remember(m.chat.id, messages.ADMIN_DENIED)
            return
        parts = m.text.strip().split(maxsplit=2)
        if len(parts) < 2:
            send_and_remember(m.chat.id, messages.M_USAGE, parse_mode='MarkdownV2')
            return
        target = parts[1].strip()
        message_text = parts[2].strip() if len(parts) > 2 else None

        if target.lower() == 'all':
            if not message_text:
                sent = send_and_remember(m.chat.id, messages.BROADCAST_ASK)
                with state.state_lock:
                    state.user_states[m.chat.id] = {
                        'action': 'awaiting_broadcast',
                        'command_msg_id': m.message_id,
                        'hint_msg_id': sent.message_id
                    }
                return
            send_broadcast(message_text, admin_chat_id=m.chat.id)
        else:
            try:
                target_id = int(target)
                user = db.get_user_by_id(target_id)
                if user:
                    display = f"@{user['username']} [ID:{target_id}]" if user['username'] else f"ID:{target_id}"
                else:
                    display = f"ID:{target_id}"
            except ValueError:
                target = target.lstrip('@')
                user = find_user_by_username(target)
                if not user:
                    send_and_remember(m.chat.id, messages.M_USER_NOT_FOUND)
                    return
                target_id = user['user_id']
                display = f"@{user['username']} [ID:{target_id}]" if user['username'] else f"ID:{target_id}"

            if not message_text:
                sent = send_and_remember(m.chat.id,
                                         f"✉️ Введите сообщение для {display}.\nДля отмены — /cancel")
                with state.state_lock:
                    state.user_states[m.chat.id] = {
                        'action': 'awaiting_reply',
                        'target_id': target_id,
                        'target_display': display,
                        'command_msg_id': m.message_id,
                        'hint_msg_id': sent.message_id
                    }
                return

            if send_message_to_user(target_id, message_text):
                safe_display = escape_markdown_v2(display)
                send_and_remember(m.chat.id, messages.M_SENT_USER.format(user_display=safe_display),
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
        for i, u in enumerate(users, 1):
            name = u['first_name'] or ""
            if u['last_name']:
                name += f" {u['last_name']}"
            username = f"@{u['username']}" if u['username'] else "нет username"
            last_seen = datetime.fromtimestamp(u['last_seen']).strftime("%d.%m.%y %H:%M")
            text += f"{i}\\. `{u['user_id']}` \\- {escape_markdown_v2(name)} \\({escape_markdown_v2(username)}\\)\n"
            text += f"   🕒 *Последняя активность:* {escape_markdown_v2(last_seen)}\n\n"
            if len(text) > 3500:
                send_and_remember(m.chat.id, text, parse_mode='MarkdownV2')
                text = ""
        if text:
            send_and_remember(m.chat.id, text, parse_mode='MarkdownV2')

    @bot.message_handler(commands=['top'])
    def top_msg(m):
        db.save_user(m.from_user)
        records = db.get_top_records(20)
        if not records:
            send_and_remember(m.chat.id, "Пусто.")
            return
        text = "🏆 ТАБЛИЦА ЛИДЕРОВ 🏆\n\n"
        for i, r in enumerate(records, 1):
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
        try:
            bot.send_message(m.chat.id, messages.RELOAD_OK, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение перед перезапуском: {e}")
        time.sleep(0.5)
        restart_bot(bot)

    # =================== ТЕКСТОВЫЕ СООБЩЕНИЯ ===================
    @bot.message_handler(content_types=['text'])
    def handle_text(message):
        if message.text and message.text.startswith('/'):
            return

        db.save_user(message.from_user)

        with state.state_lock:
            st = state.user_states.get(message.chat.id)
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
                try:
                    bot.delete_message(message.chat.id, st['command_msg_id'])
                    bot.delete_message(message.chat.id, st['hint_msg_id'])
                    bot.delete_message(message.chat.id, message.message_id)
                except:
                    pass
                with state.state_lock:
                    state.user_states.pop(message.chat.id, None)
                return

            elif action == 'awaiting_prompt':
                new_prompt = message.text.strip()
                if not new_prompt:
                    send_and_remember(message.chat.id, messages.PROMPT_EMPTY + " Для отмены /cancel")
                    return
                with state.state_lock:
                    state.user_system_prompts[message.from_user.id] = new_prompt
                clear_user_history(message.from_user.id)
                send_and_remember(message.chat.id, messages.PROMPT_SET)
                try:
                    bot.delete_message(message.chat.id, st['command_msg_id'])
                    bot.delete_message(message.chat.id, st['hint_msg_id'])
                    bot.delete_message(message.chat.id, message.message_id)
                except:
                    pass
                with state.state_lock:
                    state.user_states.pop(message.chat.id, None)
                return

            elif action == 'awaiting_reply':
                reply_text = message.text.strip()
                if not reply_text:
                    send_and_remember(message.chat.id, "⚠️ Сообщение не может быть пустым.")
                    return
                target_id = st['target_id']
                display = st.get('target_display', f"ID:{target_id}")
                if send_message_to_user(target_id, reply_text):
                    safe_display = escape_markdown_v2(display)
                    send_and_remember(message.chat.id,
                                      messages.M_SENT_USER.format(user_display=safe_display),
                                      parse_mode='MarkdownV2')
                else:
                    send_and_remember(message.chat.id, messages.MA_FAIL)
                try:
                    bot.delete_message(message.chat.id, st['command_msg_id'])
                    bot.delete_message(message.chat.id, st['hint_msg_id'])
                    bot.delete_message(message.chat.id, message.message_id)
                except:
                    pass
                with state.state_lock:
                    state.user_states.pop(message.chat.id, None)
                return

            elif action == 'awaiting_broadcast':
                broadcast_text = message.text.strip()
                if not broadcast_text:
                    send_and_remember(message.chat.id, "⚠️ Сообщение не может быть пустым.")
                    return
                send_broadcast(broadcast_text, admin_chat_id=message.chat.id)
                try:
                    bot.delete_message(message.chat.id, st['command_msg_id'])
                    bot.delete_message(message.chat.id, st['hint_msg_id'])
                    bot.delete_message(message.chat.id, message.message_id)
                except:
                    pass
                with state.state_lock:
                    state.user_states.pop(message.chat.id, None)
                return

            elif action in ('search', 'ai_logs_search', 'edit'):
                return

        # Уведомление админу о новом текстовом сообщении
        if ADMIN_CHAT_ID:
            user = message.from_user
            user_str = f"@{user.username}" if user.username else user.first_name
            safe_user = escape_markdown_v2(user_str)
            preview = escape_markdown_v2(message.text[:100])
            if len(message.text) > 100:
                preview += '…'
            text = messages.NOTIFY_TEXT.format(user=safe_user, preview=preview)
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("💬 Ответить", callback_data=f"reply_to_user_{user.id}"))
            try:
                send_and_remember(ADMIN_CHAT_ID, text, reply_markup=markup, parse_mode='MarkdownV2')
            except Exception as e:
                logger.exception(f"Не удалось отправить уведомление админу: {e}")

        bot.send_chat_action(message.chat.id, 'typing')
        ai_answer = ask_ai(message, message.text, db)
        formatted = format_as_quote(ai_answer)
        for part in split_message(formatted):
            send_and_remember(message.chat.id, part, parse_mode='MarkdownV2')

    # =================== МЕДИА ===================
    @bot.message_handler(content_types=['photo', 'audio', 'document', 'voice', 'video', 'sticker'])
    def handle_media(message):
        db.save_user(message.from_user)
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
        except Exception as e:
            logger.error(f"Не удалось переслать медиа админу: {e}")

        user = message.from_user
        user_str = f"@{user.username}" if user.username else user.first_name
        safe_user = escape_markdown_v2(user_str)
        text = messages.NOTIFY_MEDIA.format(user=safe_user, media_type=message.content_type)
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("💬 Ответить", callback_data=f"reply_to_user_{user.id}"))
        try:
            send_and_remember(ADMIN_CHAT_ID, text, reply_markup=markup, parse_mode='MarkdownV2')
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление админу: {e}")

        send_and_remember(message.chat.id, "Слабо текстом? 😏")

    # =================== АДМИНСКИЕ СОСТОЯНИЯ ===================
    @bot.message_handler(func=lambda m: m.chat.id in state.user_states and is_admin(m.chat.id))
    def handle_state_message(message):
        with state.state_lock:
            user_state = state.user_states.get(message.chat.id)
        if not user_state:
            return
        action = user_state.get('action')
        if action == 'search':
            query = message.text.strip()
            if len(query) < 2:
                send_and_remember(message.chat.id, "Введите минимум 2 символа для поиска.")
                return
            records = db.search_records_by_name(query)
            if not records:
                send_and_remember(message.chat.id,
                                  f"Ничего не найдено по запросу `{escape_markdown_v2(query)}`\\.",
                                  parse_mode='MarkdownV2')
            else:
                from admin import show_records_list
                show_records_list(bot, db, message.chat.id, records=records)
            with state.state_lock:
                state.user_states.pop(message.chat.id, None)
        elif action == 'ai_logs_search':
            query = message.text.strip()
            if not query:
                send_and_remember(message.chat.id, "Пустой запрос.")
                return
            with state.state_lock:
                state.user_states[message.chat.id] = {'action': 'ai_logs_view', 'search_query': query, 'page': 0}
            from admin import show_ai_logs
            show_ai_logs(bot, db, message.chat.id, page=0, search_query=query)
        elif action == 'edit':
            record_id = user_state['record_id']
            field = user_state['field']
            process_edit_value(message, record_id, field)

    def process_edit_value(message, record_id, field):
        if not is_admin(message.chat.id):
            return
        new_value = message.text.strip()
        if field in ('score', 'duration'):
            try:
                new_value = int(new_value)
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
            with state.state_lock:
                state.user_states.pop(message.chat.id, None)