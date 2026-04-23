import asyncio
import html
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import cfg_manager
from database import Database
from ai_service import AIService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = Database(cfg_manager.config.db_filename)
ai = AIService(db)
bot = Bot(token=cfg_manager.config.bot_token)
dp = Dispatcher()

def is_admin(user_id: int) -> bool:
    return user_id == cfg_manager.config.admin_chat_id

async def split_and_send(message: types.Message, text: str):
    if len(text) <= 4000:
        await message.reply(text)
        return
    for i in range(0, len(text), 4000):
        await message.reply(text[i:i+4000])
        await asyncio.sleep(0.2)  # антифлуд

# ---------- Команды ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await db.save_user(message.from_user.id, message.from_user.username,
                       message.from_user.first_name, message.from_user.last_name)
    await message.reply("Привет! Я бот с ИИ. /help — команды")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.reply("/top — рекорды\n/reset — сбросить диалог\n/admin — админка")

@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    if is_admin(message.from_user.id):
        await db.clear_ai_history()
        await message.reply("✅ Контекст всех сброшен")
    else:
        await db.clear_ai_history(message.from_user.id)
        await message.reply("✅ Твой диалог сброшен")

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    records = await db.get_top_records()
    if not records:
        return await message.reply("Рекордов пока нет")
    text = "<b>🏆 Таблица лидеров:</b>\n\n"
    for i, r in enumerate(records, 1):
        text += f"{i}. {html.escape(r['name'])} — {r['score']} 🍎 ({r['duration']}с)\n"
    await message.reply(text)

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardBuilder()
    kb.add(InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"))
    kb.add(InlineKeyboardButton(text="🤖 Очистить ИИ", callback_data="adm_clear_ai"))
    await message.reply("⚙️ Панель управления:", reply_markup=kb.as_markup())

@dp.callback_query(lambda c: c.data == "adm_clear_ai")
async def clear_ai_callback(call: types.CallbackQuery):
    await db.clear_ai_history()
    await call.answer("Контекст сброшен")
    await call.message.edit_reply_markup()

# ---------- Обработка текста ----------
@dp.message()
async def handle_text(message: types.Message):
    await db.save_user(message.from_user.id, message.from_user.username,
                       message.from_user.first_name, message.from_user.last_name)
    await bot.send_chat_action(message.chat.id, "typing")
    answer = await ai.ask(message.from_user.id, message.from_user.username,
                          message.from_user.first_name, message.text)
    await split_and_send(message, answer)

# ---------- Запуск ----------
async def main():
    await db.init()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())