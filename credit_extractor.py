import json
import os
import re

import requests

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-oss-120b:free')

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
    if not OPENROUTER_API_KEY or not description:
        return []
    try:
        r = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {OPENROUTER_API_KEY}'},
            json={
                'model': OPENROUTER_MODEL,
                'messages': [{'role': 'user', 'content': _PROMPT.format(description=description[:4000])}],
            },
            timeout=5,
        )
        text = r.json()['choices'][0]['message']['content']
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(0))
        return [c for c in data.get('credits', []) if c.get('name') and c.get('role')]
    except Exception:
        return []
