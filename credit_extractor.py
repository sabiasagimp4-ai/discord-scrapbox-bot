import os
import re

import requests

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'nvidia/nemotron-nano-9b-v2:free')

# 役職の具体例リストは、小型モデルが「映像系/音楽系」の境界を判断する錨として残している
_SYSTEM_PROMPT = (
    'MV概要欄から映像制作クレジットのみ抽出。\n'
    '\n'
    '- 記載されたものだけ出力。\n'
    '- 映像系役職のみ対象（Direction, Animation, Illustration, 3DCG, VFX, Design, Visualizer, Camera, Edit など）。\n'
    '- 音楽系役職（Music, Lyric, Compose, Vocal, Recording, Mix など）は除外。\n'
    '- 役職名は原文のまま。\n'
    '- @username・URLは除外。\n'
    '- なければ「なし」。\n'
    '\n'
    '形式:\n'
    '役職: 名前'
)

_URL_RE = re.compile(r'https?://\S+')
_HANDLE_RE = re.compile(r'@[A-Za-z0-9_.]+')
# 英数字・ひらがな・カタカナ・漢字のいずれかを含む行だけを「意味のある行」とみなす
_MEANINGFUL_RE = re.compile(r'[0-9A-Za-z぀-ヿ一-鿿]')


def clean_description(description):
    """概要欄からクレジット抽出に寄与しない部分を機械的に削る（LLMへの投入トークン削減）。
    URL・@ハンドル・空になった括弧・装飾記号だけの行・空行を除去する。"""
    out = []
    for raw in description.splitlines():
        s = _URL_RE.sub('', raw)
        s = _HANDLE_RE.sub('', s)
        s = re.sub(r'[(（]\s*[)）]', '', s)  # ハンドル除去で空になった括弧を掃除
        s = re.sub(r'\s+', ' ', s).strip()
        if s and _MEANINGFUL_RE.search(s):
            out.append(s)
    return '\n'.join(out)


def check_connection():
    """OpenRouter APIキーの有効性を確認する。戻り値: (ok, message)。未設定時は (None, '未設定')"""
    if not OPENROUTER_API_KEY:
        return None, '未設定'
    try:
        r = requests.get(
            'https://openrouter.ai/api/v1/auth/key',
            headers={'Authorization': f'Bearer {OPENROUTER_API_KEY}'},
            timeout=5,
        )
    except Exception as e:
        return False, str(e)
    if r.status_code == 200:
        return True, '接続OK'
    return False, f'ステータス({r.status_code})'


def _parse_credits(text):
    credits = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line == 'なし':
            continue
        match = re.match(r'^(.+?)[:：]\s*(.+)$', line)
        if not match:
            continue
        role, name = match.group(1).strip(), match.group(2).strip()
        if role and name:
            credits.append({'role': role, 'name': name})
    return credits


def extract_credits_debug(description):
    """extract_creditsの内部処理を診断情報付きで実行する。
    戻り値: (credits: list, raw_response: str|None, error: str|None)
    """
    if not OPENROUTER_API_KEY:
        return [], None, 'OPENROUTER_API_KEYが未設定です'
    description = clean_description(description or '')
    if not description:
        return [], None, '概要欄が空です'

    try:
        r = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {OPENROUTER_API_KEY}'},
            json={
                'model': OPENROUTER_MODEL,
                'messages': [
                    {'role': 'system', 'content': _SYSTEM_PROMPT},
                    {'role': 'user', 'content': description[:4000]},
                ],
            },
            timeout=5,
        )
    except Exception as e:
        return [], None, f'リクエストエラー: {e}'

    if r.status_code != 200:
        return [], None, f'ステータス({r.status_code}): {r.text[:200]}'

    try:
        text = r.json()['choices'][0]['message']['content']
    except Exception:
        return [], r.text[:1000], 'レスポンスの形式が想定と異なります'

    credits = _parse_credits(text)
    return credits, text, None


def extract_credits(description):
    return extract_credits_debug(description)[0]
