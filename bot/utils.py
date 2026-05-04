import time
import logging

logger = logging.getLogger(__name__)

def escape_markdown_v2(text):
    if not text:
        return ""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    result = []
    for ch in text:
        if ch == '\\':
            result.append('\\\\')
        elif ch in escape_chars:
            result.append('\\' + ch)
        else:
            result.append(ch)
    return ''.join(result)

def format_as_quote(text):
    lines = text.splitlines()
    is_code = (
        ('{' in text and '}' in text) or
        'def ' in text or 'function ' in text or
        'import ' in text or 'class ' in text or
        any(line.startswith(' ') or line.startswith('\t') for line in lines)
    )
    if is_code:
        return f"```\n{text}\n```"
    else:
        escaped = escape_markdown_v2(text)
        return '\n'.join('> ' + line for line in escaped.split('\n'))

def quote_text(text):
    """Экранирует и оборачивает строку в цитату MarkdownV2 (> )."""
    escaped = escape_markdown_v2(text)
    return '\n'.join('> ' + line for line in escaped.split('\n'))

def split_message(text, max_len=4096):
    if len(text) <= max_len:
        return [text]
    parts = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break
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

def safe_send(bot, chat_id, text, **kwargs):
    for attempt in range(3):
        try:
            return bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            logger.warning(f"Send attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(1)
    raise Exception("Failed to send message after 3 retries")