import json
import os
import re

try:
    import anthropic
except ImportError:
    anthropic = None

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=5.0) if (anthropic and ANTHROPIC_API_KEY) else None

_PROMPT = """以下は動画の説明欄です。クレジット情報（役職と人物名のペア）を抽出してください。
該当する情報が無ければ空配列にしてください。
出力は次のJSON形式のみとし、他のテキストやコードブロック記法は含めないでください。
{{"credits": [{{"role": "役職", "name": "人物名"}}]}}

説明欄:
---
{description}
---
"""


def extract_credits(description):
    if not _client or not description:
        return []
    try:
        message = _client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': _PROMPT.format(description=description[:4000])}],
        )
        text = message.content[0].text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(0))
        return [c for c in data.get('credits', []) if c.get('name') and c.get('role')]
    except Exception:
        return []
