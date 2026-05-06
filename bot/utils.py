# utils.py
import re
import time
import logging

logger = logging.getLogger(__name__)


def escape_markdown_v2(text: str) -> str:
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


def format_as_quote(text: str) -> str:
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


def _split_plain_text(text: str, max_len: int) -> list:
    parts = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break
        cut = max_len
        while cut > 0 and remaining[cut-1] == '\\':
            cut -= 1
        split_at = remaining.rfind('\n\n', 0, cut)
        if split_at == -1:
            split_at = remaining.rfind('\n', 0, cut)
        if split_at == -1:
            split_at = remaining.rfind(' ', 0, cut)
        if split_at == -1:
            split_at = cut
        parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    return parts


def _split_code_block(block: str, max_len: int) -> list:
    inner = block[3:-3].strip()
    prefix = "```\n"
    suffix = "\n```"
    overhead = len(prefix) + len(suffix)
    inner_max = max_len - overhead
    if inner_max <= 0:
        return [block]
    parts = []
    remaining = inner
    first = True
    while remaining:
        if len(remaining) <= inner_max:
            parts.append(prefix + remaining + suffix)
            break
        cut = inner_max
        split_at = remaining.rfind('\n', 0, cut)
        if split_at == -1:
            split_at = remaining.rfind(' ', 0, cut)
        if split_at == -1:
            split_at = cut
        chunk = remaining[:split_at].rstrip()
        parts.append(prefix + chunk + suffix)
        remaining = remaining[split_at:].lstrip()
    return parts


def split_message(text: str, max_len: int = 4096) -> list:
    if len(text) <= max_len:
        return [text]

    code_pattern = re.compile(r'```.*?```', re.DOTALL)
    parts = []
    pos = 0

    def add_segment(seg):
        if len(seg) <= max_len:
            parts.append(seg)
        else:
            if seg.startswith('```') and seg.endswith('```'):
                parts.extend(_split_code_block(seg, max_len))
            else:
                parts.extend(_split_plain_text(seg, max_len))

    for match in code_pattern.finditer(text):
        start, end = match.span()
        add_segment(text[pos:start])
        add_segment(text[start:end])
        pos = end
    add_segment(text[pos:])

    merged = []
    for part in parts:
        if merged and len(merged[-1]) + len(part) <= max_len:
            merged[-1] += part
        else:
            merged.append(part)
    return merged


def safe_send(bot, chat_id, text, **kwargs):
    for attempt in range(3):
        try:
            return bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            logger.warning(f"Send attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(1)
    raise Exception("Failed to send message after 3 retries")
