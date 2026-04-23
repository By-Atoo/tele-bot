import asyncio
import html
import logging
from telebot.async_telebot import AsyncTeleBot
from telebot import types
from telebot.util import smart_split

from config import cfg_manager
from database import Database
from ai_service import AIService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Инициализация компонентов
db = Database(cfg_manager.config.db_filename)
ai = AIService(db)
bot = AsyncTeleBot(cfg_manager.config.bot_token)

def is_admin(user_id: int) -> bool:
    return user_id == cfg_manager.config.admin_chat_id

async def split_and_send(chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Отправляет длинное сообщение по частям (если больше 4000 символов).
    Использует smart_split для корректного разбиения по словам.
    """
    for chunk in smart_split(text, chars_per_string=4000):
        await bot.send_message(chat_id, chunk, reply_to_message_id=reply_to_message_id)
        await asyncio.sleep(0.2)  # антифлуд

# ---------- Команды ----------
@bot.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await db.save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    await bot.reply_to(message, "Привет! Я бот с ИИ. /help — команды")

@bot.message_handler(commands=['help'])
async def cmd_help(message: types.Message):
    await bot.reply_to(message, "/top — рекорды\n/reset — сбросить диалог\n/admin — админка")

@bot.message_handler(commands=['reset'])
async def cmd_reset(message: types.Message):
    if is_admin(message.from_user.id):
        await db.clear_ai_history()
        await bot.reply_to(message, "✅ Контекст всех сброшен")
    else:
        await db.clear_ai_history(message.from_user.id)
        await bot.reply_to(message, "✅ Твой диалог сброшен")

@bot.message_handler(commands=['top'])
async def cmd_top(message: types.Message):
    records = await db.get_top_records()
    if not records:
        await bot.reply_to(message, "Рекордов пока нет")
        return

    text = "<b>🏆 Таблица лидеров:</b>\n\n"
    for i, r in enumerate(records, 1):
        text += f"{i}. {html.escape(str(r['name']))} — {r['score']} 🍎 ({r['duration']}с)\n"
    await bot.reply_to(message, text, parse_mode='HTML')

@bot.message_handler(commands=['admin'])
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    markup = types.InlineKeyboardMarkup()
    btn_stats = types.InlineKeyboardButton("📊 Статистика", callback_data="adm_stats")
    btn_clear_ai = types.InlineKeyboardButton("🤖 Очистить ИИ", callback_data="adm_clear_ai")
    markup.add(btn_stats, btn_clear_ai)
    await bot.reply_to(message, "⚙️ Панель управления:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "adm_clear_ai")
async def clear_ai_callback(call: types.CallbackQuery):
    await db.clear_ai_history()
    await bot.answer_callback_query(call.id, "Контекст сброшен")
    # Убираем клавиатуру после нажатия
    await bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

# ---------- Обработка текста ----------
@bot.message_handler(func=lambda message: True)  # все остальные текстовые сообщения
async def handle_text(message: types.Message):
    await db.save_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    await bot.send_chat_action(message.chat.id, 'typing')
    answer = await ai.ask(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
        message.text
    )
    await split_and_send(message.chat.id, answer, reply_to_message_id=message.message_id)

# ---------- Запуск ----------
async def main():
    await db.init()
    logger.info("Бот запущен")
    await bot.polling(none_stop=True)

if __name__ == "__main__":
    asyncio.run(main())