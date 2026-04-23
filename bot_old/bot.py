#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import io
import json
import logging
import os
import random
import signal
import sqlite3
import threading
import time
import asyncio
from collections import defaultdict
from datetime import datetime

import telebot
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from telebot import apihelper
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline
from openai import OpenAI

# ------------------- НАСТРОЙКА ЛОГИРОВАНИЯ -------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== РАБОТА С КОНФИГУРАЦИЕЙ ====================
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

CONFIG = {}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)
        logger.info(f"Создан файл конфигурации {CONFIG_FILE}")
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def apply_config(config_dict):
    global CONFIG, BOT_TOKEN, ADMIN_CHAT_ID, API_HOST, API_PORT, API_SECRET
    global DB_FILENAME, AI_API_KEY, AI_API_URL, AI_MODEL, SYSTEM_PROMPT, PROXY_URL

    CONFIG = config_dict
    BOT_TOKEN = config_dict['bot_token']
    ADMIN_CHAT_ID = config_dict['admin_chat_id']
    API_HOST = config_dict['api_host']
    API_PORT = config_dict['api_port']
    API_SECRET = config_dict['api_secret']
    DB_FILENAME = config_dict['db_filename']
    AI_API_KEY = config_dict['ai_api_key']
    AI_API_URL = config_dict['ai_api_url']
    AI_MODEL = config_dict['ai_model']
    SYSTEM_PROMPT = {"role": "system", "content": config_dict['system_prompt']}
    PROXY_URL = config_dict.get('proxy_url')

    if PROXY_URL:
        apihelper.proxy = {'https': PROXY_URL}
    else:
        apihelper.proxy = None

    logger.info("Конфигурация применена.")

apply_config(load_config())

# ==================== НАБЛЮДАТЕЛЬ ЗА ФАЙЛОМ ====================
class ConfigFileEventHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(CONFIG_FILE):
            logger.info(f"Файл {CONFIG_FILE} изменён, перезагружаем конфигурацию...")
            try:
                new_config = load_config()
                apply_config(new_config)
                logger.info("Конфигурация успешно перезагружена.")
            except Exception as e:
                logger.error(f"Ошибка при перезагрузке конфигурации: {e}")

observer = Observer()
observer.schedule(ConfigFileEventHandler(), path='.', recursive=False)
observer.start()

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА И FLASK ====================
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
CORS(app)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["10 per minute"]
)

user_states = {}
user_histories = defaultdict(list)

# ------------------- РАБОТА С БАЗОЙ ДАННЫХ -------------------
def db_connection():
    return sqlite3.connect(DB_FILENAME)

def init_db():
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS records
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      name TEXT, score INTEGER, duration INTEGER, timestamp INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS ai_logs
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      user_id INTEGER,
                      username TEXT,
                      first_name TEXT,
                      last_name TEXT,
                      message TEXT,
                      response TEXT,
                      timestamp INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (user_id INTEGER PRIMARY KEY,
                      username TEXT,
                      first_name TEXT,
                      last_name TEXT,
                      first_seen INTEGER,
                      last_seen INTEGER)''')
    logger.info(f"База данных инициализирована: {DB_FILENAME}")

def save_record(name, score, duration):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO records (name, score, duration, timestamp) VALUES (?,?,?,?)',
                  (name, score, duration, int(time.time())))

def get_top_records(limit=20):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, score, duration, timestamp FROM records ORDER BY score DESC LIMIT ?', (limit,))
        rows = c.fetchall()
    return [{'id': r[0], 'name': r[1], 'score': r[2], 'duration': r[3], 'timestamp': r[4]} for r in rows]

def get_all_records(order_by='score DESC'):
    allowed_columns = {
        'score DESC': 'score DESC',
        'score ASC': 'score ASC',
        'duration DESC': 'duration DESC',
        'duration ASC': 'duration ASC',
        'timestamp DESC': 'timestamp DESC',
        'timestamp ASC': 'timestamp ASC'
    }
    order_clause = allowed_columns.get(order_by, 'score DESC')
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(f'SELECT id, name, score, duration, timestamp FROM records ORDER BY {order_clause}')
        rows = c.fetchall()
    return [{'id': r[0], 'name': r[1], 'score': r[2], 'duration': r[3], 'timestamp': r[4]} for r in rows]

def delete_record(record_id):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM records WHERE id = ?', (record_id,))

def delete_all_records():
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM records')

def update_record(record_id, field, value):
    if field not in ('name', 'score', 'duration'):
        raise ValueError("Недопустимое поле для обновления")
    with db_connection() as conn:
        c = conn.cursor()
        c.execute(f'UPDATE records SET {field} = ? WHERE id = ?', (value, record_id))

def get_record_by_id(record_id):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, score, duration, timestamp FROM records WHERE id = ?', (record_id,))
        row = c.fetchone()
    if row:
        return {'id': row[0], 'name': row[1], 'score': row[2], 'duration': row[3], 'timestamp': row[4]}
    return None

def search_records_by_name(query):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT id, name, score, duration, timestamp FROM records WHERE name LIKE ? ORDER BY score DESC', (f'%{query}%',))
        rows = c.fetchall()
    return [{'id': r[0], 'name': r[1], 'score': r[2], 'duration': r[3], 'timestamp': r[4]} for r in rows]

def get_statistics():
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT COUNT(*), AVG(score), MAX(score), MIN(score) FROM records')
        count, avg, max_score, min_score = c.fetchone()
    return {
        'count': count or 0,
        'avg': round(avg, 2) if avg else 0,
        'max': max_score or 0,
        'min': min_score or 0
    }

# ------------------- ЛОГИРОВАНИЕ AI -------------------
def save_ai_log(user_id, username, first_name, last_name, message, response):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO ai_logs 
                     (user_id, username, first_name, last_name, message, response, timestamp)
                     VALUES (?,?,?,?,?,?,?)''',
                  (user_id, username, first_name, last_name, message, response, int(time.time())))

def get_ai_log_by_id(log_id):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('''SELECT id, user_id, username, first_name, last_name, message, response, timestamp 
                     FROM ai_logs WHERE id = ?''', (log_id,))
        row = c.fetchone()
    if row:
        return {'id': row[0], 'user_id': row[1], 'username': row[2], 'first_name': row[3],
                'last_name': row[4], 'message': row[5], 'response': row[6], 'timestamp': row[7]}
    return None

def get_ai_logs(limit=50, offset=0, search_query=None):
    with db_connection() as conn:
        c = conn.cursor()
        if search_query:
            try:
                search_id = int(search_query)
                c.execute('''SELECT id, user_id, username, first_name, last_name, message, response, timestamp 
                             FROM ai_logs 
                             WHERE user_id = ? OR username LIKE ?
                             ORDER BY timestamp DESC LIMIT ? OFFSET ?''',
                          (search_id, f'%{search_query}%', limit, offset))
            except ValueError:
                c.execute('''SELECT id, user_id, username, first_name, last_name, message, response, timestamp 
                             FROM ai_logs 
                             WHERE username LIKE ?
                             ORDER BY timestamp DESC LIMIT ? OFFSET ?''',
                          (f'%{search_query}%', limit, offset))
        else:
            c.execute('''SELECT id, user_id, username, first_name, last_name, message, response, timestamp 
                         FROM ai_logs 
                         ORDER BY timestamp DESC LIMIT ? OFFSET ?''', (limit, offset))
        rows = c.fetchall()
    return [{'id': r[0], 'user_id': r[1], 'username': r[2], 'first_name': r[3],
             'last_name': r[4], 'message': r[5], 'response': r[6], 'timestamp': r[7]} for r in rows]

def count_ai_logs(search_query=None):
    with db_connection() as conn:
        c = conn.cursor()
        if search_query:
            try:
                search_id = int(search_query)
                c.execute('SELECT COUNT(*) FROM ai_logs WHERE user_id = ? OR username LIKE ?',
                          (search_id, f'%{search_query}%'))
            except ValueError:
                c.execute('SELECT COUNT(*) FROM ai_logs WHERE username LIKE ?', (f'%{search_query}%',))
        else:
            c.execute('SELECT COUNT(*) FROM ai_logs')
        return c.fetchone()[0]

def delete_ai_logs():
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM ai_logs')
    logger.info("Все логи ИИ удалены.")

def export_ai_logs_json():
    logs = get_ai_logs(limit=10000)
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

# ------------------- ПОЛЬЗОВАТЕЛИ -------------------
def save_user(user):
    with db_connection() as conn:
        c = conn.cursor()
        now = int(time.time())
        c.execute('''UPDATE users SET username=?, first_name=?, last_name=?, last_seen=?
                     WHERE user_id=?''',
                  (user.username, user.first_name, user.last_name, now, user.id))
        if c.rowcount == 0:
            c.execute('''INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (user.id, user.username, user.first_name, user.last_name, now, now))

def get_all_users():
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('''SELECT user_id, username, first_name, last_name, first_seen, last_seen 
                     FROM users ORDER BY last_seen DESC''')
        rows = c.fetchall()
    return [{'user_id': r[0], 'username': r[1], 'first_name': r[2], 'last_name': r[3],
             'first_seen': r[4], 'last_seen': r[5]} for r in rows]

# ------------------- УВЕДОМЛЕНИЯ -------------------
def notify_admin_new_record(name, score, duration):
    if not ADMIN_CHAT_ID:
        return
    text = (f"**НОВЫЙ РЕКОРД!**\n\n"
            f"👤 Имя: {name}\n"
            f"🍎 Очки: {score}\n"
            f"⏱️ Время: {duration} сек.\n\n")
    try:
        bot.send_message(ADMIN_CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление о рекорде: {e}")

def notify_admin_new_message(message):
    if not ADMIN_CHAT_ID:
        return
    user = message.from_user
    user_str = f"@{user.username}" if user.username else user.first_name
    safe_user = escape_markdown(user_str)

    if message.content_type == 'text':
        preview = escape_markdown(message.text[:100] + ('…' if len(message.text) > 100 else ''))
        content_desc = f"💬 {preview}"
    elif message.content_type == 'photo':
        content_desc = "📷 Фото"
    elif message.content_type == 'audio':
        content_desc = "🎵 Аудио"
    elif message.content_type == 'document':
        content_desc = "📄 Документ"
    elif message.content_type == 'voice':
        content_desc = "🎤 Голосовое сообщение"
    elif message.content_type == 'video':
        content_desc = "🎬 Видео"
    elif message.content_type == 'sticker':
        content_desc = f"🖼️ Стикер {message.sticker.emoji}"
    else:
        content_desc = "📎 Медиа"

    if message.caption:
        caption_preview = escape_markdown(message.caption[:50] + ('…' if len(message.caption) > 50 else ''))
        content_desc += f"\n📝 {caption_preview}"

    text = f"➕ {safe_user}\n {content_desc}"
    try:
        bot.send_message(ADMIN_CHAT_ID, text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление админу: {e}")

# ------------------- API ДЛЯ ИГРЫ -------------------
@app.route('/leaderboard', methods=['GET'])
@limiter.limit("30 per minute")
def leaderboard():
    records = get_top_records(20)
    return jsonify([{'name': r['name'], 'score': r['score'], 'duration': r['duration']} for r in records])

@app.route('/record', methods=['POST'])
@limiter.limit("5 per minute")
def add_record():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'No JSON data'}), 400

    if data.get('secret') != API_SECRET:
        logger.warning(f"Unauthorized access attempt from {request.remote_addr}")
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    if 'name' not in data or 'score' not in data or 'duration' not in data:
        return jsonify({'success': False, 'error': 'Missing fields'}), 400

    try:
        name = str(data['name'])[:20].strip()
        if not name:
            name = "Anonymous"
        score = int(data['score'])
        duration = int(data['duration'])
        if score < 0 or duration < 0:
            raise ValueError("Negative values not allowed")
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid data types'}), 400

    save_record(name, score, duration)
    notify_admin_new_record(name, score, duration)
    logger.info(f"New record from {request.remote_addr}: {name} - {score} pts")
    return jsonify({'success': True})

@app.route('/')
def index():
    return "Snake Leaderboard Bot with Zveno.ai AI & Online Tracker"

# ------------------- ФОРМАТИРОВАНИЕ -------------------
def escape_markdown(text):
    if not text:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(f'\\{ch}' if ch in escape_chars else ch for ch in text)

def format_as_quote(text):
    """Оборачивает код в ```, стихи/цитаты в >, остальное оставляет как есть."""
    lines = text.splitlines()
    # Признаки кода: отступы, ключевые слова, фигурные скобки
    is_code = any(
        ('{' in text and '}' in text) or
        'def ' in text or 'function ' in text or
        'import ' in text or 'class ' in text or
        (line.startswith(' ') or line.startswith('\t'))
        for line in lines
    )
    if is_code:
        return f"```\n{text}\n```"
    elif len(lines) > 3:
        return '\n'.join(f'> {line}' for line in lines)
    else:
        return text

def split_message(text, max_len=4096):
    """Разбивает текст на части, не разрывая строки кода и абзацы."""
    if len(text) <= max_len:
        return [text]

    parts = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break

        # Пытаемся разорвать по двойному переносу строки (конец абзаца)
        split_at = remaining.rfind('\n\n', 0, max_len)
        if split_at == -1:
            split_at = remaining.rfind('\n', 0, max_len)
        if split_at == -1:
            split_at = remaining.rfind(' ', 0, max_len)
        if split_at == -1:
            split_at = max_len

        parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return parts

# ------------------- TELEGRAM БОТ -------------------
def is_admin(chat_id):
    return chat_id == ADMIN_CHAT_ID

@bot.message_handler(commands=['start'])
def start_msg(m):
    save_user(m.from_user)
    bot.reply_to(m, "Привет! Я бездарь.\n"
                 "Можешь задать свой вопрос или скинуть фоточку ножек.\n")

@bot.message_handler(commands=['help'])
def help_cmd(m):
    save_user(m.from_user)
    text = (
        "📖 *Доступные команды:*\n\n"
        "/top — таблица игры\n"
        "/reset — сбросить историю диалога\n"
        "/users — список пользователей (только админ)\n"
    )
    bot.reply_to(m, text, parse_mode='Markdown')

@bot.message_handler(commands=['reset'])
def reset_cmd(m):
    save_user(m.from_user)
    user_id = m.from_user.id
    if is_admin(m.chat.id):
        user_histories.clear()
        bot.reply_to(m, "✅ Контекст ИИ сброшен для всех пользователей.")
    else:
        if user_id in user_histories:
            del user_histories[user_id]
        bot.reply_to(m, "✅ Контекст обнулён. Начнём с чистого листа.")

@bot.message_handler(commands=['reload'])
def reload_cmd(m):
    save_user(m.from_user)
    if not is_admin(m.chat.id):
        bot.reply_to(m, "⛔ Отказано.")
        return
    try:
        new_config = load_config()
        apply_config(new_config)
        bot.reply_to(m, "✅ Конфигурация перезагружена.")
    except Exception as e:
        bot.reply_to(m, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['top'])
def top_msg(m):
    save_user(m.from_user)
    records = get_top_records(20)
    if not records:
        bot.reply_to(m, "Пусто.")
        return
    text = "🏆 ТАБЛИЦА ЛИДЕРОВ 🏆\n\n"
    for i, r in enumerate(records, 1):
        text += f"{i}. {r['name']} — {r['score']} очков ({r['duration']} сек.)\n"
    bot.reply_to(m, text)

@bot.message_handler(commands=['users'])
def list_users_cmd(m):
    save_user(m.from_user)
    if not is_admin(m.chat.id):
        bot.reply_to(m, "⛔ У вас нет доступа к этой команде.")
        return

    users = get_all_users()
    if not users:
        bot.reply_to(m, "😕 Пока нет ни одного пользователя.")
        return

    text = "👥 *Список пользователей:*\n\n"
    for i, u in enumerate(users, 1):
        name = u['first_name'] or ""
        if u['last_name']:
            name += f" {u['last_name']}"
        username = f"@{u['username']}" if u['username'] else "нет username"
        last_seen = datetime.fromtimestamp(u['last_seen']).strftime("%d.%m.%y %H:%M")
        text += f"{i}\\. `{u['user_id']}` \\- {escape_markdown(name)} \\({escape_markdown(username)}\\)\n"
        text += f"   🕒 *Последняя активность:* {escape_markdown(last_seen)}\n\n"

        if len(text) > 3500:
            bot.send_message(m.chat.id, text, parse_mode='MarkdownV2')
            text = ""

    if text:
        bot.send_message(m.chat.id, text, parse_mode='MarkdownV2')

@bot.message_handler(commands=['admin'])
def admin_panel(m):
    save_user(m.from_user)
    if not is_admin(m.chat.id):
        bot.reply_to(m, "⛔ Отказано.")
        return
    show_admin_main(m.chat.id)

def show_admin_main(chat_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🐍 Змейка", callback_data="admin_category_snake"),
        InlineKeyboardButton("🤖 ИИ и логи", callback_data="admin_category_ai"),
        InlineKeyboardButton("📤 Экспорт", callback_data="admin_category_export"),
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_category_users"),
        InlineKeyboardButton("📡 Трекер", callback_data="admin_category_tracker")
    )
    bot.send_message(chat_id, "🔐 *Админ‑панель*\nВыберите категорию:", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_category_'))
def handle_category(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ Отказанно")
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
    bot.send_message(chat_id, "🐍 *Управление игрой «Змейка»*", reply_markup=markup, parse_mode='Markdown')

def show_ai_menu(chat_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🤖 Логи ИИ", callback_data="admin_ai_logs"),
        InlineKeyboardButton("🔍 Поиск в логах", callback_data="admin_ai_logs_search"),
        InlineKeyboardButton("🗑️ Очистить логи", callback_data="admin_ai_logs_clear"),
        InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main")
    )
    bot.send_message(chat_id, "🤖 *ИИ и логирование*", reply_markup=markup, parse_mode='Markdown')

def show_export_menu(chat_id):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("💾 Экспорт рекордов (JSON)", callback_data="admin_export_json"),
        InlineKeyboardButton("📤 Экспорт логов ИИ", callback_data="admin_export_ai_logs"),
        InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main")
    )
    bot.send_message(chat_id, "📤 *Экспорт данных*", reply_markup=markup, parse_mode='Markdown')

def show_users_menu(chat_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("👥 Список пользователей", callback_data="admin_list_users"))
    markup.add(InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main"))
    bot.send_message(chat_id, "👥 *Пользователи*", reply_markup=markup, parse_mode='Markdown')

def show_tracker_menu(chat_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Главное меню", callback_data="admin_back_to_main"))
    bot.send_message(chat_id, "📡 *Трекер онлайн‑статуса* (управление в разработке)", reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data in ['admin_back_to_main'])
def back_to_main(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ Нет прав")
        return
    show_admin_main(call.message.chat.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == 'admin_list_users')
def list_users_callback(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ Нет прав")
        return
    users = get_all_users()
    if not users:
        bot.send_message(call.message.chat.id, "😕 Пока нет ни одного пользователя.")
    else:
        text = "👥 *Список пользователей:*\n\n"
        for i, u in enumerate(users, 1):
            name = u['first_name'] or ""
            if u['last_name']:
                name += f" {u['last_name']}"
            username = f"@{u['username']}" if u['username'] else "нет username"
            last_seen = datetime.fromtimestamp(u['last_seen']).strftime("%d.%m.%y %H:%M")
            text += f"{i}\\. `{u['user_id']}` \\- {escape_markdown(name)} \\({escape_markdown(username)}\\)\n"
            text += f"   🕒 *Последняя активность:* {escape_markdown(last_seen)}\n\n"
            if len(text) > 3500:
                bot.send_message(call.message.chat.id, text, parse_mode='MarkdownV2')
                text = ""
        if text:
            bot.send_message(call.message.chat.id, text, parse_mode='MarkdownV2')
    bot.answer_callback_query(call.id)

# Обработчик для старых callback'ов (admin_*, ai_logs_*)
@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_') or call.data.startswith('ai_logs_'))
def handle_legacy_callbacks(call):
    if not is_admin(call.message.chat.id):
        bot.answer_callback_query(call.id, "⛔ Нет прав")
        return
    data = call.data

    if data == 'admin_list':
        show_records_list(call.message.chat.id, page=0)
    elif data == 'admin_search':
        bot.send_message(call.message.chat.id, "Введите имя для поиска:")
        user_states[call.message.chat.id] = {'action': 'search'}
    elif data == 'admin_add_test':
        add_test_record(call.message.chat.id)
    elif data == 'admin_stats':
        show_stats(call.message.chat.id)
    elif data == 'admin_delete_all_confirm':
        confirm_delete_all(call.message.chat.id)
    elif data == 'admin_export_json':
        export_records_json(call.message.chat.id)
    elif data == 'admin_export_ai_logs':
        json_str = export_ai_logs_json()
        file = io.BytesIO(json_str.encode('utf-8'))
        file.name = f"ai_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        bot.send_document(call.message.chat.id, file, caption="📁 Экспорт логов ИИ")
    elif data == 'admin_ai_logs':
        user_states.pop(call.message.chat.id, None)
        show_ai_logs(call.message.chat.id, page=0)
    elif data.startswith('ai_logs_page_'):
        state = user_states.get(call.message.chat.id, {})
        search_query = state.get('search_query') if state.get('action') == 'ai_logs_view' else None
        parts = data.split('_')
        page = int(parts[-1])
        show_ai_logs(call.message.chat.id, page=page, search_query=search_query)
    elif data.startswith('ai_logs_full_'):
        log_id = int(data.split('_')[-1])
        log = get_ai_log_by_id(log_id)
        if log:
            user_info = f"{log['first_name']} {log['last_name']}".strip()
            if log['username']:
                user_info += f" (@{log['username']})"
            user_info += f" [ID:{log['user_id']}]"
            date_str = datetime.fromtimestamp(log['timestamp']).strftime("%d.%m.%y %H:%M")
            full_text = f"📄 *Полный лог ID {log_id}*\n`{date_str}`\n👤 {escape_markdown(user_info)}\n\n💬 *Вопрос:*\n{escape_markdown(log['message'])}\n\n🤖 *Ответ:*\n{escape_markdown(log['response'])}"
            for part in split_message(full_text):
                bot.send_message(call.message.chat.id, part, parse_mode='Markdown')
        else:
            bot.answer_callback_query(call.id, "Запись не найдена.")
    elif data == 'admin_ai_logs_search':
        bot.send_message(call.message.chat.id, "Введите @username для поиска:")
        user_states[call.message.chat.id] = {'action': 'ai_logs_search'}
    elif data == 'admin_ai_logs_clear':
        delete_ai_logs()
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
        delete_all_records()
        bot.send_message(call.message.chat.id, "✅ Все рекорды удалены.")
        show_admin_main(call.message.chat.id)
    elif data == 'admin_delete_all_no':
        bot.send_message(call.message.chat.id, "Операция отменена.")
        show_admin_main(call.message.chat.id)
    elif data == 'admin_back_to_menu':   # устаревшее
        show_admin_main(call.message.chat.id)
    bot.answer_callback_query(call.id)

def show_records_list(chat_id, page=0, records=None, per_page=5):
    if records is None:
        records = get_all_records()
    total = len(records)
    start = page * per_page
    end = start + per_page
    page_records = records[start:end]
    if not page_records:
        bot.send_message(chat_id, "Нет записей.")
        return
    text = f"📋 **Рекорды (стр. {page+1} из { (total+per_page-1)//per_page if total else 1 })**\n\n"
    for r in page_records:
        date_str = datetime.fromtimestamp(r['timestamp']).strftime("%d.%m.%y %H:%M")
        text += f"`ID: {r['id']}`\n👤 {r['name']} | 🍎 {r['score']} | ⏱️ {r['duration']} сек.\n_{date_str}_\n\n"
    markup = InlineKeyboardMarkup(row_width=2)
    if page > 0:
        markup.add(InlineKeyboardButton("◀ Назад", callback_data=f"admin_page_{page-1}"))
    if end < total:
        markup.add(InlineKeyboardButton("Вперед ▶", callback_data=f"admin_page_{page+1}"))
    for r in page_records:
        markup.add(InlineKeyboardButton(f"✏️ Редактировать ID {r['id']} ({r['name']})", callback_data=f"admin_edit_{r['id']}"))
    markup.add(InlineKeyboardButton("🔙 В меню", callback_data="admin_back_to_main"))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def show_edit_menu(chat_id, record_id):
    rec = get_record_by_id(record_id)
    if not rec:
        bot.send_message(chat_id, "Запись не найдена.")
        return
    text = f"✏️ **Редактирование ID {rec['id']}**\n\n👤 Имя: `{rec['name']}`\n🍎 Очки: `{rec['score']}`\n⏱️ Время: `{rec['duration']}` сек.\n\nЧто хотите изменить?"
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("📝 Имя", callback_data=f"admin_edit_field_{record_id}_name"))
    markup.add(InlineKeyboardButton("🔢 Очки", callback_data=f"admin_edit_field_{record_id}_score"))
    markup.add(InlineKeyboardButton("⏱️ Время", callback_data=f"admin_edit_field_{record_id}_duration"))
    markup.add(InlineKeyboardButton("🗑️ Удалить запись", callback_data=f"admin_delete_one_{record_id}"))
    markup.add(InlineKeyboardButton("🔙 Назад к списку", callback_data="admin_list"))
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def ask_for_new_value(chat_id, record_id, field):
    field_names = {'name': 'имя', 'score': 'очки', 'duration': 'время (сек)'}
    bot.send_message(chat_id, f"Введите новое значение для поля **{field_names[field]}** (ID {record_id}):", parse_mode='Markdown')
    user_states[chat_id] = {'action': 'edit', 'record_id': record_id, 'field': field}

def process_edit_value(message, record_id, field):
    if not is_admin(message.chat.id):
        return
    new_value = message.text.strip()
    if field in ('score', 'duration'):
        try:
            new_value = int(new_value)
        except ValueError:
            bot.send_message(message.chat.id, "❌ Ошибка: введите целое число.")
            return
    try:
        update_record(record_id, field, new_value)
        bot.send_message(message.chat.id, f"✅ Поле `{field}` обновлено на `{new_value}`.", parse_mode='Markdown')
        show_edit_menu(message.chat.id, record_id)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при обновлении: {e}")
    finally:
        user_states.pop(message.chat.id, None)

def delete_one_record(chat_id, record_id):
    rec = get_record_by_id(record_id)
    if rec:
        delete_record(record_id)
        bot.send_message(chat_id, f"✅ Рекорд ID {record_id} удалён.")
    else:
        bot.send_message(chat_id, "Запись не найдена.")
    show_records_list(chat_id, page=0)

def confirm_delete_all(chat_id):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ ДА, УДАЛИТЬ ВСЁ", callback_data="admin_delete_all_yes"))
    markup.add(InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="admin_delete_all_no"))
    bot.send_message(chat_id, "⚠️ **Вы уверены, что хотите удалить ВСЕ рекорды?** Это действие необратимо.", reply_markup=markup, parse_mode='Markdown')

def add_test_record(chat_id):
    test_names = ["Test", "Test2", "Атощко", "Atoo_o", "Anonymous"]
    name = random.choice(test_names) + str(random.randint(1, 99))
    score = random.randint(50, 500)
    duration = random.randint(20, 120)
    save_record(name, score, duration)
    bot.send_message(chat_id, f"✅ Добавлен тестовый рекорд: {name} — {score} очков ({duration} сек.)")
    show_admin_main(chat_id)

def show_stats(chat_id):
    stats = get_statistics()
    text = f"📊 **Статистика**\n\n"
    text += f"Всего рекордов: {stats['count']}\n"
    text += f"Средний счёт: {stats['avg']}\n"
    text += f"Максимальный счёт: {stats['max']}\n"
    text += f"Минимальный счёт: {stats['min']}\n"
    bot.send_message(chat_id, text, parse_mode='Markdown')

def export_records_json(chat_id):
    records = get_all_records()
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

# ------------------- ИНТЕГРАЦИЯ С Zveno.ai -------------------
def ask_ai(message, question):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    last_name = message.from_user.last_name or ""

    if not AI_API_KEY:
        return "AI не настроен."

    client = OpenAI(
        api_key=AI_API_KEY,
        base_url=AI_API_URL
    )

    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": SYSTEM_PROMPT['content']}
        ]

    history = user_histories[user_id]
    history.append({"role": "user", "content": question})

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=history,
            temperature=0.7,
            max_tokens=500
        )
        answer = response.choices[0].message.content
        history.append({"role": "assistant", "content": answer})

        if len(history) > 21:
            user_histories[user_id] = history[:1] + history[-20:]

        save_ai_log(user_id, username, first_name, last_name, question, answer)
        return answer
    except Exception as e:
        logger.error(f"Zveno API error: {e}")
        return "Инет не робит, Рашка всё таки.."

def show_ai_logs(chat_id, page=0, search_query=None, per_page=10):
    offset = page * per_page
    logs = get_ai_logs(limit=per_page, offset=offset, search_query=search_query)
    total = count_ai_logs(search_query)
    if not logs:
        bot.send_message(chat_id, "Логи ИИ пусты.")
        return

    text = f"🤖 **Логи ИИ** (стр. {page+1} из { (total+per_page-1)//per_page if total else 1 })\n\n"
    for log in logs:
        date_str = datetime.fromtimestamp(log['timestamp']).strftime("%d.%m.%y %H:%M")
        user_info = f"{log['first_name']} {log['last_name']}".strip()
        if log['username']:
            user_info += f" (@{log['username']})"
        user_info += f" [ID:{log['user_id']}]"
        msg_preview = log['message'][:50] + "…" if len(log['message']) > 50 else log['message']
        resp_preview = log['response'][:50] + "…" if len(log['response']) > 50 else log['response']
        user_info = escape_markdown(user_info)
        msg_preview = escape_markdown(msg_preview)
        resp_preview = escape_markdown(resp_preview)
        text += f"`{date_str}`\n👤 {user_info}\n💬 *Q:* {msg_preview}\n🤖 *A:* {resp_preview}\n\n"

    markup = InlineKeyboardMarkup(row_width=4)
    if page > 0:
        markup.add(InlineKeyboardButton("◀ Назад", callback_data=f"ai_logs_page_{page-1}"))
    if offset + per_page < total:
        markup.add(InlineKeyboardButton("Вперед ▶", callback_data=f"ai_logs_page_{page+1}"))
    markup.add(InlineKeyboardButton("🔍 Поиск", callback_data="admin_ai_logs_search"))
    markup.add(InlineKeyboardButton("🗑️ Очистить", callback_data="admin_ai_logs_clear"))
    markup.add(InlineKeyboardButton("🔙 В меню", callback_data="admin_back_to_main"))

    row = []
    for log in logs:
        row.append(InlineKeyboardButton(f"📄 {log['id']}", callback_data=f"ai_logs_full_{log['id']}"))
        if len(row) == 3:
            markup.add(*row)
            row = []
    if row:
        markup.add(*row)

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

# ------------------- ОБРАБОТЧИКИ СООБЩЕНИЙ -------------------
@bot.message_handler(content_types=['text'])
def handle_text(message):
    if message.text.startswith('/'):
        return
    save_user(message.from_user)
    notify_admin_new_message(message)
    bot.send_chat_action(message.chat.id, 'typing')
    ai_answer = ask_ai(message, message.text)
    formatted = format_as_quote(ai_answer)
    for part in split_message(formatted):
        bot.reply_to(message, part, parse_mode='Markdown')

@bot.message_handler(content_types=['photo', 'audio', 'document', 'voice', 'video', 'sticker'])
def handle_media(message):
    save_user(message.from_user)
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

    notify_admin_new_message(message)
    bot.reply_to(message, "Слабо текстом? 😏")

@bot.message_handler(func=lambda m: m.chat.id in user_states and is_admin(m.chat.id))
def handle_state_message(message):
    state = user_states.get(message.chat.id)
    if not state:
        return
    action = state.get('action')
    if action == 'search':
        query = message.text.strip()
        if len(query) < 2:
            bot.send_message(message.chat.id, "Введите минимум 2 символа для поиска.")
            return
        records = search_records_by_name(query)
        if not records:
            bot.send_message(message.chat.id, f"Ничего не найдено по запросу `{query}`.", parse_mode='Markdown')
        else:
            show_records_list(message.chat.id, page=0, records=records)
        user_states.pop(message.chat.id, None)
    elif action == 'ai_logs_search':
        query = message.text.strip()
        if not query:
            bot.send_message(message.chat.id, "Пустой запрос.")
            return
        user_states[message.chat.id] = {'action': 'ai_logs_view', 'search_query': query, 'page': 0}
        show_ai_logs(message.chat.id, page=0, search_query=query)
    elif action == 'edit':
        record_id = state['record_id']
        field = state['field']
        process_edit_value(message, record_id, field)

# ------------------- ТРЕКЕР ОНЛАЙН-СТАТУСА (Telethon) -------------------
def start_online_tracker():
    tracker_config = CONFIG.get('online_tracker', {})
    if not tracker_config.get('enabled', False):
        logger.info("Мониторинг онлайн-статуса отключён.")
        return

    api_id = tracker_config.get('api_id')
    api_hash = tracker_config.get('api_hash')
    tracked_usernames = tracker_config.get('tracked_usernames', [])
    notification_chat_id = tracker_config.get('notification_chat_id')
    check_interval = tracker_config.get('check_interval', 30)

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
                                    duration_str = f"{int(hours)}ч {int(minutes)}м {int(seconds)}с"
                                    status_text = f"🔴 вышел из сети (был онлайн: {duration_str})"
                                    del login_time[username]
                                else:
                                    status_text = "🔴 вышел из сети"
                            await client.send_message(
                                notification_chat_id,
                                f"👤 @{username} {status_text}"
                            )
                            logger.info(f"@{username} {status_text}")

                        prev_status[username] = is_online
                    except Exception as e:
                        logger.error(f"Ошибка при проверке @{username}: {e}")

                await asyncio.sleep(check_interval)
        finally:
            await client.disconnect()

    def run_async_loop():
        asyncio.run(tracker_task())

    tracker_thread = threading.Thread(target=run_async_loop, daemon=True)
    tracker_thread.start()

# ------------------- ЗАПУСК -------------------
def run_bot():
    logger.info("Запуск Telegram бота...")
    bot.infinity_polling()

def shutdown_handler(signum, frame):
    logger.info("Останавливаем бота...")
    bot.stop_polling()
    observer.stop()
    observer.join()
    time.sleep(1)
    logger.info("Бот остановлен.")

if __name__ == '__main__':
    init_db()
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    start_online_tracker()

    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    logger.info(f"Запуск Flask API на http://{API_HOST}:{API_PORT}")
    try:
        app.run(host=API_HOST, port=API_PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_handler(None, None)