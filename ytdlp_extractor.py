from urllib.parse import urlparse

# yt-dlp経由でメタデータを取るドメイン。YouTube/VimeoはAPI直叩きの方が速く安定するため対象外。
# InstagramなどはデータセンターIPからだとログイン壁で失敗しうるため、あくまでベストエフォート
# （失敗時は呼び出し側が汎用HTMLフォールバックに落ちる）。
SUPPORTED_DOMAINS = ('instagram.com', 'tiktok.com', 'twitter.com', 'x.com')


def matches(url):
    """yt-dlpでの取得を試みるべきURLかを判定する"""
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith('.' + d) for d in SUPPORTED_DOMAINS)


def _extract_info(url):
    # yt-dlpは重いパッケージなので、必要になるまでインポートしない
    import yt_dlp

    options = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'socket_timeout': 10,
        'noplaylist': True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def fetch(url):
    """動画をダウンロードせずメタデータのみ取得する。
    戻り値: {'title', 'description', 'thumbnail', 'uploader'} または失敗時 None
    """
    try:
        info = _extract_info(url)
    except Exception:
        return None
    if not info:
        return None

    title = (info.get('title') or '').strip()
    uploader = (info.get('uploader') or '').strip()
    if not title:
        title = uploader
    if not title:
        return None

    return {
        'title': title,
        'description': info.get('description') or '',
        'thumbnail': info.get('thumbnail') or '',
        'uploader': uploader,
    }
