import asyncio
from bot import main as bot_main
from api import app
import uvicorn
from tracker import run_tracker
from config import cfg_manager

async def main():
    # Запуск трекера (если включен)
    if cfg_manager.config.online_tracker.get("enabled"):
        asyncio.create_task(run_tracker())

    # Запуск FastAPI
    config = uvicorn.Config(app, host=cfg_manager.config.api_host, port=cfg_manager.config.api_port, log_level="info")
    server = uvicorn.Server(config)
    api_task = asyncio.create_task(server.serve())

    # Запуск бота
    bot_task = asyncio.create_task(bot_main())

    await asyncio.gather(api_task, bot_task)

if __name__ == "__main__":
    asyncio.run(main())