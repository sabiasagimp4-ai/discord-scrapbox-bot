import os
import re

import requests

OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL = os.environ.get('OPENROUTER_MODEL', 'openai/gpt-oss-20b:free')

_SYSTEM_PROMPT = (
    'あなたはYouTubeのMV概要欄から映像制作クレジットを抽出する専門家です。\n'
    '\n'
    'ルール:\n'
    '1. 概要欄に実際に書かれているクレジットだけを出力する。書かれていない項目は一切出力しない。\n'
    '2. 映像・ビジュアル制作の役職のみ対象（Direction, Animation, Illustration, 3DCG, VFX, Movie, Design, Jacket, Visualizer, Lyric Motion, Post Effect, Camera, Edit など）。\n'
    '3. 音楽制作の役職は除外（Music, Lyric, Compose, Arrange, Vocal, Guitar, Recording, Sound, Mastering, Mix Engineer など）。\n'
    '4. 役職名は概要欄に書かれた表記をそのまま使う。\n'
    '5. @usernameやURLは出力しない。\n'
    '6. 映像クレジットが一つも見つからない場合のみ「なし」と答える。\n'
    '\n'
    '出力例（概要欄にDirectionとIllustrationしかなければこの2行だけ出す）:\n'
    'Direction: 山田太郎\n'
    'Illustration: 鈴木花子'
)


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
