import os

import requests

GYAZO_ACCESS_TOKEN = os.environ.get('GYAZO_ACCESS_TOKEN', '')


def check_connection():
    """Gyazoアクセストークンの有効性を確認する。戻り値: (ok, message)。未設定時は (None, '未設定')"""
    if not GYAZO_ACCESS_TOKEN:
        return None, '未設定'
    try:
        r = requests.get(
            'https://api.gyazo.com/api/oauth/token/info',
            params={'access_token': GYAZO_ACCESS_TOKEN},
            timeout=5,
        )
    except Exception as e:
        return False, str(e)
    if r.status_code == 200:
        return True, '接続OK'
    return False, f'ステータス({r.status_code})'


def upload_thumbnail(image_url):
    """画像URLをダウンロードしてGyazoにアップロードし、Gyazo上のURLを返す。失敗時は空文字。"""
    if not GYAZO_ACCESS_TOKEN or not image_url:
        return ''
    try:
        img = requests.get(image_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if img.status_code != 200:
            return ''
        r = requests.post(
            'https://upload.gyazo.com/api/upload',
            data={'access_token': GYAZO_ACCESS_TOKEN},
            files={'imagedata': ('thumbnail.jpg', img.content)},
            timeout=10,
        )
        if r.status_code != 200:
            return ''
        return r.json().get('url', '')
    except Exception:
        return ''
