import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from config import get_config

logger = logging.getLogger(__name__)

def create_app(db, bot):
    cfg = get_config()
    app = Flask(__name__)
    CORS(app)

    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["10 per minute"]
    )

    @app.route('/leaderboard', methods=['GET'])
    @limiter.limit("30 per minute")
    def leaderboard():
        records = db.get_top_records(20)
        return jsonify([{'name': r['name'], 'score': r['score'], 'duration': r['duration']} for r in records])

    @app.route('/record', methods=['POST'])
    @limiter.limit("5 per minute")
    def add_record():
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'No JSON data'}), 400

        if data.get('secret') != cfg['api_secret']:
            logger.warning(f"Unauthorized access attempt from {request.remote_addr}")
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403

        if 'name' not in data or 'score' not in data or 'duration' not in data:
            return jsonify({'success': False, 'error': 'Missing fields'}), 400

        try:
            name = str(data['name'])[:20].strip() or "Anonymous"
            score = int(data['score'])
            duration = int(data['duration'])
            if score < 0 or duration < 0:
                raise ValueError("Negative values not allowed")
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid data types'}), 400

        db.save_record(name, score, duration)

        try:
            text = (f"**НОВЫЙ РЕКОРД!**\n\n"
                    f"👤 Имя: {name}\n"
                    f"🍎 Очки: {score}\n"
                    f"⏱️ Время: {duration} сек.\n\n")
            bot.send_message(cfg['admin_chat_id'], text, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Не удалось отправить уведомление о рекорде через API: {e}")

        logger.info(f"New record from {request.remote_addr}: {name} - {score} pts")
        return jsonify({'success': True})

    @app.route('/')
    def index():
        return "Snake Leaderboard Bot with Zveno.ai AI & Online Tracker"

    return app
