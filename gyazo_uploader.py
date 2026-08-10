import os

import requests

GYAZO_ACCESS_TOKEN = os.environ.get('GYAZO_ACCESS_TOKEN', '')


def check_connection():
    """Gyazoアクセストークンの有効性を確認する。戻り値: (ok, message)。未設定時は (None, '未設定')
    トークン確認専用のエンドポイント（/api/oauth/token/info）は現在のGyazo APIには存在せず、
    常に404を返すため使わない。代わりに実在する /api/images を per_page=1 で叩き、
    トークンが有効なら200・無効なら401が返る挙動を利用する（画像アップロードと同じ認証方式）。"""
    if not GYAZO_ACCESS_TOKEN:
        return None, '未設定'
    try:
        r = requests.get(
            'https://api.gyazo.com/api/images',
            params={'access_token': GYAZO_ACCESS_TOKEN, 'per_page': 1},
            timeout=5,
        )
    except Exception as e:
        return False, str(e)
    if r.status_code == 200:
        return True, '接続OK'
    if r.status_code == 401:
        return False, 'アクセストークンが無効です'
    return False, f'ステータス({r.status_code})'


def upload_image(data, filename='image.jpg'):
    """画像のバイト列をGyazoにアップロードし、Gyazo上のURLを返す。失敗時は空文字。
    Discordの添付ファイルURLは期限切れで見られなくなるため、Scrapboxに貼る画像は
    必ずこの関数を通して恒久URLに変換してから書き込む。"""
    if not GYAZO_ACCESS_TOKEN or not data:
        return ''
    try:
        r = requests.post(
            'https://upload.gyazo.com/api/upload',
            data={'access_token': GYAZO_ACCESS_TOKEN},
            files={'imagedata': (filename, data)},
            timeout=10,
        )
        if r.status_code != 200:
            return ''
        return r.json().get('url', '')
    except Exception:
        return ''


def upload_thumbnail(image_url):
    """画像URLをダウンロードしてGyazoにアップロードし、Gyazo上のURLを返す。失敗時は空文字。"""
    if not GYAZO_ACCESS_TOKEN or not image_url:
        return ''
    try:
        img = requests.get(image_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        if img.status_code != 200:
            return ''
    except Exception:
        return ''
    return upload_image(img.content, 'thumbnail.jpg')
