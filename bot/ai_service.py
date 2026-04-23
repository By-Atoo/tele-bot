import asyncio
from openai import AsyncOpenAI
from config import cfg_manager

class AIService:
    def __init__(self, db):
        self.db = db
        self.client = AsyncOpenAI(
            api_key=cfg_manager.config.ai_api_key,
            base_url=cfg_manager.config.ai_api_url
        )

    async def ask(self, user_id: int, username: str, first_name: str, text: str) -> str:
        if not cfg_manager.config.ai_api_key:
            return "ИИ не настроен."

        history = await self.db.get_ai_history(user_id)
        messages = [{"role": "system", "content": cfg_manager.config.system_prompt}]
        for h in history:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": text})

        try:
            resp = await self.client.chat.completions.create(
                model=cfg_manager.config.ai_model,
                messages=messages,
                temperature=0.7,
                max_tokens=500
            )
            answer = resp.choices[0].message.content
            await self.db.add_ai_message(user_id, "user", text)
            await self.db.add_ai_message(user_id, "assistant", answer)
            return answer
        except Exception as e:
            return f"Ошибка: {e}"