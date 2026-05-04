import io
import json
import random
import logging
from datetime import datetime

from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import get_config
from utils import escape_markdown_v2, split_message, safe_send
import state

logger = logging.getLogger(__name__)

def is_admin(chat_id, admin_id):
    return chat_id == admin_id

def show_admin_main(bot, chat_id):
    cfg = get_config()
    if not is_admin(chat_id, cfg['admin_chat_id']):
        return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🐍 Змейка", callback_data="admin_category_snake"),
        InlineKeyboardButton("🤖 ИИ и логи", callback_data="admin_category_ai"),
        InlineKeyboardButton("📤 Экспорт", callback_data="admin_category_export"),
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_category_users"),
        InlineKeyboardButton("📡 Трекер", callback_data="admin_category_tracker")
    )
    safe_send(bot, chat_id, "🔐 *Админ‑панель*\nВыберите категорию:", reply_markup=markup, parse_mode='MarkdownV2')

def register_admin_callbacks(bot, db):
    cfg = get_config()
    ADMIN_CHAT_ID = cfg['admin_chat_id']

    def admin_only(call):
        return is_admin(call.message.chat.id, ADMIN_CHAT_ID)

    # ---- Меню категорий ----
    @bot.callback_query_handler(func=lambda call: call.data.startswith('admin_category_'))
    def handle_category(call):
        if not admin_only(call):
            bot.answer_callback_query(call.id, "⛔ Отказано")
            return
        cat = call.data.replace('admin_category_', '')
        if cat == 'snake':
            show_snake_menu(call.message.chat.id)
        elif cat == 'ai':
            show_ai_menu(call.message.chat.id)
        elif cat == 'export':
            show_export_menu(call.message.chat.id)
        elif cat == 'users':
            show_users_menu(call.message.chat.id)
        elif cat == 'tracker':
            show_tracker_menu(call.message.chat.id)
        bot.answer_callback_query(call.id)

    def show_snake_menu(chat_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("📋 Все рекорды", callback_data="admin_list"),
            InlineKeyboardButton("🔍 Поиск по имени", callback_data="admin_search"),
            InlineKeyboardButton("➕ Тестовый рекорд", callback_data="admin_add_test"),
            InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
            InlineKeyboardButton("🗑️ Удалить все", callback_data="admin_delete_all_confirm"),
            InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main")
        )
        safe_send(bot, chat_id, "🐍 *Управление игрой «Змейка»*", reply_markup=markup, parse_mode='MarkdownV2')

    def show_ai_menu(chat_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("🤖 Логи ИИ", callback_data="admin_ai_logs"),
            InlineKeyboardButton("🔍 Поиск в логах", callback_data="admin_ai_logs_search"),
            InlineKeyboardButton("🗑️ Очистить логи", callback_data="admin_ai_logs_clear"),
            InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main")
        )
        safe_send(bot, chat_id, "🤖 *ИИ и логирование*", reply_markup=markup, parse_mode='MarkdownV2')

    def show_export_menu(chat_id):
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("💾 Экспорт рекордов \\(JSON\\)", callback_data="admin_export_json"),
            InlineKeyboardButton("📤 Экспорт логов ИИ", callback_data="admin_export_ai_logs"),
            InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main")
        )
        safe_send(bot, chat_id, "📤 *Экспорт данных*", reply_markup=markup, parse_mode='MarkdownV2')

    def show_users_menu(chat_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("👥 Список пользователей", callback_data="admin_list_users"))
        markup.add(InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main"))
        safe_send(bot, chat_id, "👥 *Пользователи*", reply_markup=markup, parse_mode='MarkdownV2')

    def show_tracker_menu(chat_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main"))
        safe_send(bot, chat_id, "📡 *Трекер онлайн‑статуса* \\(управление в разработке\\)", reply_markup=markup, parse_mode='MarkdownV2')

    # ---- Назад в главное меню ----
    @bot.callback_query_handler(func=lambda call: call.data == 'admin_back_to_main')
    def back_to_main(call):
        if not admin_only(call):
            bot.answer_callback_query(call.id, "⛔ Нет прав")
            return
        show_admin_main(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)

    # ---- Список пользователей (с кнопками ответа) ----
    @bot.callback_query_handler(func=lambda call: call.data == 'admin_list_users')
    def list_users_callback(call):
        if not admin_only(call):
            bot.answer_callback_query(call.id, "⛔ Нет прав")
            return
        users = db.get_all_users()
        if not users:
            safe_send(bot, call.message.chat.id, "😕 Пока нет ни одного пользователя.")
            bot.answer_callback_query(call.id)
            return

        text = "👥 *Список пользователей:*\n\n"
        markup = InlineKeyboardMarkup(row_width=2)
        for i, u in enumerate(users, 1):
            name = u['first_name'] or ""
            if u['last_name']:
                name += f" {u['last_name']}"
            username = f"@{u['username']}" if u['username'] else "нет username"
            last_seen = datetime.fromtimestamp(u['last_seen']).strftime("%d.%m.%y %H:%M")
            text += f"{i}\\. `{u['user_id']}` \\- {escape_markdown_v2(name)} \\({escape_markdown_v2(username)}\\)\n"
            text += f"   🕒 *Последняя активность:* {escape_markdown_v2(last_seen)}\n\n"
            markup.add(InlineKeyboardButton(f"💬 Ответить {u['user_id']}", callback_data=f"reply_to_user_{u['user_id']}"))
        markup.add(InlineKeyboardButton("🔙 В меню", callback_data="admin_back_to_main"))

        if len(text) > 4000:
            for chunk in split_message(text):
                safe_send(bot, call.message.chat.id, chunk, parse_mode='MarkdownV2')
            safe_send(bot, call.message.chat.id, "Кнопки управления:", reply_markup=markup)
        else:
            safe_send(bot, call.message.chat.id, text, reply_markup=markup, parse_mode='MarkdownV2')
        bot.answer_callback_query(call.id)

    # ---- Кнопка «Ответить» (универсальная) ----
    @bot.callback_query_handler(func=lambda call: call.data.startswith('reply_to_user_'))
    def reply_button_callback(call):
        if not admin_only(call):
            bot.answer_callback_query(call.id, "⛔ Нет прав")
            return
        target_id = int(call.data.split('_')[-1])
        user = db.get_user_by_id(target_id)
        if user:
            display = f"@{user['username']} [ID:{target_id}]" if user['username'] else f"ID:{target_id}"
        else:
            display = f"ID:{target_id}"
        safe_send(bot, call.message.chat.id,
                  f"📝 Введите сообщение для {display}.\nДля отмены — /cancel")
        with state.state_lock:
            state.user_states[call.message.chat.id] = {
                'action': 'awaiting_reply',
                'target_id': target_id,
                'target_display': display
            }
        bot.answer_callback_query(call.id)

    # ---- Остальные callback'и (admin_*, ai_logs_*) ----
    @bot.callback_query_handler(func=lambda call: call.data.startswith('admin_') or call.data.startswith('ai_logs_'))
    def handle_legacy_callbacks(call):
        if not admin_only(call):
            bot.answer_callback_query(call.id, "⛔ Нет прав")
            return
        data = call.data

        if data == 'admin_list':
            show_records_list(call.message.chat.id, page=0)
        elif data == 'admin_search':
            safe_send(bot, call.message.chat.id, "Введите имя для поиска:")
            state.user_states[call.message.chat.id] = {'action': 'search'}
        elif data == 'admin_add_test':
            add_test_record(call.message.chat.id)
        elif data == 'admin_stats':
            show_stats(call.message.chat.id)
        elif data == 'admin_delete_all_confirm':
            confirm_delete_all(call.message.chat.id)
        elif data == 'admin_export_json':
            export_records_json(call.message.chat.id)
        elif data == 'admin_export_ai_logs':
            json_str = export_ai_logs_json(db)
            file = io.BytesIO(json_str.encode('utf-8'))
            file.name = f"ai_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            bot.send_document(call.message.chat.id, file, caption="📁 Экспорт логов ИИ")
        elif data == 'admin_ai_logs':
            state.user_states.pop(call.message.chat.id, None)
            show_ai_logs(call.message.chat.id, page=0)
        elif data.startswith('ai_logs_page_'):
            st = state.user_states.get(call.message.chat.id, {})
            search_query = st.get('search_query') if st.get('action') == 'ai_logs_view' else None
            parts = data.split('_')
            page = int(parts[-1])
            show_ai_logs(call.message.chat.id, page=page, search_query=search_query)
        elif data.startswith('ai_logs_full_'):
            log_id = int(data.split('_')[-1])
            log = db.get_ai_log_by_id(log_id)
            if log:
                user_info = f"{log['first_name']} {log['last_name']}".strip()
                if log['username']:
                    user_info += f" (@{log['username']})"
                user_info += f" [ID:{log['user_id']}]"
                date_str = datetime.fromtimestamp(log['timestamp']).strftime("%d.%m.%y %H:%M")
                full_text = (
                    f"📄 *Полный лог ID {log_id}*\n"
                    f"`{date_str}`\n"
                    f"👤 {escape_markdown_v2(user_info)}\n\n"
                    f"💬 *Вопрос:*\n{escape_markdown_v2(log['message'])}\n\n"
                    f"🤖 *Ответ:*\n{escape_markdown_v2(log['response'])}"
                )
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton(f"💬 Ответить {log['user_id']}", callback_data=f"reply_to_user_{log['user_id']}"))
                for part in split_message(full_text):
                    if part == split_message(full_text)[-1]:
                        safe_send(bot, call.message.chat.id, part, reply_markup=markup, parse_mode='MarkdownV2')
                    else:
                        safe_send(bot, call.message.chat.id, part, parse_mode='MarkdownV2')
            else:
                bot.answer_callback_query(call.id, "Запись не найдена.")
        elif data == 'admin_ai_logs_search':
            safe_send(bot, call.message.chat.id, "Введите @username или ID для поиска:")
            state.user_states[call.message.chat.id] = {'action': 'ai_logs_search'}
        elif data == 'admin_ai_logs_clear':
            db.delete_ai_logs()
            bot.answer_callback_query(call.id, "✅ Логи ИИ очищены.")
            show_ai_logs(call.message.chat.id, page=0)
        elif data.startswith('admin_page_'):
            page = int(data.split('_')[-1])
            show_records_list(call.message.chat.id, page)
        elif data.startswith('admin_edit_'):
            record_id = int(data.split('_')[-1])
            show_edit_menu(call.message.chat.id, record_id)
        elif data.startswith('admin_edit_field_'):
            parts = data.split('_')
            record_id = int(parts[3])
            field = parts[4]
            ask_for_new_value(call.message.chat.id, record_id, field)
        elif data.startswith('admin_delete_one_'):
            record_id = int(data.split('_')[-1])
            delete_one_record(call.message.chat.id, record_id)
        elif data == 'admin_delete_all_yes':
            db.delete_all_records()
            safe_send(bot, call.message.chat.id, "✅ Все рекорды удалены.")
            show_admin_main(bot, call.message.chat.id)
        elif data == 'admin_delete_all_no':
            safe_send(bot, call.message.chat.id, "Операция отменена.")
            show_admin_main(bot, call.message.chat.id)
        elif data == 'admin_back_to_menu':
            show_admin_main(bot, call.message.chat.id)
        bot.answer_callback_query(call.id)

    # ---- Вспомогательные функции для callback'ов ----
    def show_records_list(chat_id, page=0, records=None, per_page=5):
        if records is None:
            records = db.get_all_records()
        total = len(records)
        start = page * per_page
        end = start + per_page
        page_records = records[start:end]
        if not page_records:
            safe_send(bot, chat_id, "Нет записей.")
            return

        text = f"📋 **Рекорды \\(стр\\. {page+1} из { (total+per_page-1)//per_page if total else 1 }\\)**\n\n"
        for r in page_records:
            date_str = datetime.fromtimestamp(r['timestamp']).strftime("%d.%m.%y %H:%M")
            text += f"`ID: {r['id']}`\n👤 {r['name']} | 🍎 {r['score']} | ⏱️ {r['duration']} сек.\n_{date_str}_\n\n"
        markup = InlineKeyboardMarkup(row_width=2)
        if page > 0:
            markup.add(InlineKeyboardButton("◀ Назад", callback_data=f"admin_page_{page-1}"))
        if end < total:
            markup.add(InlineKeyboardButton("Вперед ▶", callback_data=f"admin_page_{page+1}"))
        for r in page_records:
            markup.add(InlineKeyboardButton(f"✏️ Редактировать ID {r['id']} \\({r['name']}\\)", callback_data=f"admin_edit_{r['id']}"))
        markup.add(InlineKeyboardButton("🔙 В меню", callback_data="admin_back_to_main"))
        safe_send(bot, chat_id, text, reply_markup=markup, parse_mode='MarkdownV2')

    def show_edit_menu(chat_id, record_id):
        rec = db.get_record_by_id(record_id)
        if not rec:
            safe_send(bot, chat_id, "Запись не найдена.")
            return
        text = f"✏️ **Редактирование ID {rec['id']}**\n\n👤 Имя: `{rec['name']}`\n🍎 Очки: `{rec['score']}`\n⏱️ Время: `{rec['duration']}` сек.\n\nЧто хотите изменить?"
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("📝 Имя", callback_data=f"admin_edit_field_{record_id}_name"))
        markup.add(InlineKeyboardButton("🔢 Очки", callback_data=f"admin_edit_field_{record_id}_score"))
        markup.add(InlineKeyboardButton("⏱️ Время", callback_data=f"admin_edit_field_{record_id}_duration"))
        markup.add(InlineKeyboardButton("🗑️ Удалить запись", callback_data=f"admin_delete_one_{record_id}"))
        markup.add(InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_list"))
        safe_send(bot, chat_id, text, reply_markup=markup, parse_mode='MarkdownV2')

    def ask_for_new_value(chat_id, record_id, field):
        field_names = {'name': 'имя', 'score': 'очки', 'duration': 'время \\(' + 'сек' + '\\)'}
        safe_send(bot, chat_id, f"Введите новое значение для поля **{field_names[field]}** \\(ID {record_id}\\):", parse_mode='MarkdownV2')
        state.user_states[chat_id] = {'action': 'edit', 'record_id': record_id, 'field': field}

    def add_test_record(chat_id):
        test_names = ["Test", "Test2", "Атощко", "Atoo_o", "Anonymous"]
        name = random.choice(test_names) + str(random.randint(1, 99))
        score = random.randint(50, 500)
        duration = random.randint(20, 120)
        db.save_record(name, score, duration)
        safe_send(bot, chat_id, f"✅ Добавлен тестовый рекорд: {name} — {score} очков \\({duration} сек\\.\\)")
        show_admin_main(bot, chat_id)

    def show_stats(chat_id):
        stats = db.get_statistics()
        text = (f"📊 **Статистика**\n\n"
                f"Всего рекордов: {stats['count']}\n"
                f"Средний счёт: {stats['avg']}\n"
                f"Максимальный счёт: {stats['max']}\n"
                f"Минимальный счёт: {stats['min']}\n")
        safe_send(bot, chat_id, text, parse_mode='MarkdownV2')

    def export_records_json(chat_id):
        records = db.get_all_records()
        data = []
        for r in records:
            data.append({
                'id': r['id'],
                'name': r['name'],
                'score': r['score'],
                'duration': r['duration'],
                'timestamp': r['timestamp'],
                'date': datetime.fromtimestamp(r['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
            })
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        file = io.BytesIO(json_str.encode('utf-8'))
        file.name = "leaderboard_export.json"
        bot.send_document(chat_id, file, caption="📁 Экспорт всех рекордов в JSON")

    def export_ai_logs_json(db):
        logs = db.get_ai_logs(limit=10000)
        data = []
        for log in logs:
            data.append({
                'id': log['id'],
                'user_id': log['user_id'],
                'username': log['username'],
                'first_name': log['first_name'],
                'last_name': log['last_name'],
                'message': log['message'],
                'response': log['response'],
                'timestamp': log['timestamp'],
                'date': datetime.fromtimestamp(log['timestamp']).strftime("%Y-%m-%d %H:%M:%S")
            })
        return json.dumps(data, indent=2, ensure_ascii=False)

    def show_ai_logs(chat_id, page=0, search_query=None, per_page=10):
        offset = page * per_page
        logs = db.get_ai_logs(limit=per_page, offset=offset, search_query=search_query)
        total = db.count_ai_logs(search_query)
        if not logs:
            safe_send(bot, chat_id, "Логи ИИ пусты.")
            return

        text = f"🤖 **Логи ИИ** \\(стр\\. {page+1} из { (total+per_page-1)//per_page if total else 1 }\\)\n\n"
        for log in logs:
            date_str = datetime.fromtimestamp(log['timestamp']).strftime("%d.%m.%y %H:%M")
            user_info = f"{log['first_name']} {log['last_name']}".strip()
            if log['username']: user_info += f" (@{log['username']})"
            user_info += f" [ID:{log['user_id']}]"
            msg_preview = log['message'][:50] + "…" if len(log['message']) > 50 else log['message']
            resp_preview = log['response'][:50] + "…" if len(log['response']) > 50 else log['response']
            text += f"`{date_str}`\n👤 {escape_markdown_v2(user_info)}\n💬 *Q:* {escape_markdown_v2(msg_preview)}\n🤖 *A:* {escape_markdown_v2(resp_preview)}\n\n"

        markup = InlineKeyboardMarkup(row_width=4)
        if page > 0:
            markup.add(InlineKeyboardButton("◀ Назад", callback_data=f"ai_logs_page_{page-1}"))
        if offset + per_page < total:
            markup.add(InlineKeyboardButton("Вперед ▶", callback_data=f"ai_logs_page_{page+1}"))
        markup.add(InlineKeyboardButton("🔍 Поиск", callback_data="admin_ai_logs_search"))
        markup.add(InlineKeyboardButton("🗑️ Очистить", callback_data="admin_ai_logs_clear"))
        markup.add(InlineKeyboardButton("🔙 В меню", callback_data="admin_back_to_main"))

        # Кнопки ответа
        row = []
        for log in logs:
            row.append(InlineKeyboardButton(f"💬 Ответить {log['user_id']}", callback_data=f"reply_to_user_{log['user_id']}"))
        if row:
            markup.add(*row)

        safe_send(bot, chat_id, text, reply_markup=markup, parse_mode='MarkdownV2')

    def confirm_delete_all(chat_id):
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ ДА, УДАЛИТЬ ВСЁ", callback_data="admin_delete_all_yes"))
        markup.add(InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="admin_delete_all_no"))
        safe_send(bot, chat_id, "⚠️ **Вы уверены, что хотите удалить ВСЕ рекорды?** Это действие необратимо.", reply_markup=markup, parse_mode='MarkdownV2')

    def delete_one_record(chat_id, record_id):
        rec = db.get_record_by_id(record_id)
        if rec:
            db.delete_record(record_id)
            safe_send(bot, chat_id, f"✅ Рекорд ID {record_id} удалён.")
        else:
            safe_send(bot, chat_id, "Запись не найдена.")
        show_records_list(chat_id, page=0)