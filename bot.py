import asyncio
import discord
import hmac
import json
import random
import re
import os
import threading
import time
import requests
from collections import deque
from datetime import datetime, time as dt_time, timezone, timedelta
from discord.ext import tasks
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import audit_log
import channel_links
import credit_extractor
import diary
import gyazo_uploader
import name_linker
import playlist_loader
import rag_qa
import scrapbox_search
import ytdlp_extractor

REQUIRED_ENV_VARS = ('DISCORD_TOKEN', 'CHANNEL_ID', 'SCRAPBOX_PROJECT', 'SCRAPBOX_SID')

TOKEN = os.environ.get('DISCORD_TOKEN', '')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID') or '0')
KEYWORD = os.environ.get('KEYWORD', '')
SCRAPBOX_PROJECT = os.environ.get('SCRAPBOX_PROJECT', '')
SCRAPBOX_SID = os.environ.get('SCRAPBOX_SID', '')
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
CREDIT_MAPPING_PAGE = os.environ.get('CREDIT_MAPPING_PAGE', '')
GUILD_ID = os.environ.get('GUILD_ID', '')
# 個人の日記ページ自動作成用。Karureの共有プロジェクトとは別のScrapboxプロジェクトを想定。
# 未設定ならこの機能は完全に無効化される（タスクを起動しない）。
DIARY_SCRAPBOX_PROJECT = os.environ.get('DIARY_SCRAPBOX_PROJECT', '')
DIARY_SCRAPBOX_SID = os.environ.get('DIARY_SCRAPBOX_SID', '')
# DMでの日記即追記を許可する本人のDiscordユーザーID。Botはサーバー全体から見えるため、
# これでガードしないと他のメンバーのDMまで日記に書き込まれてしまう。
DIARY_OWNER_USER_ID = int(os.environ.get('DIARY_OWNER_USER_ID') or '0')
# iOSショートカット等からのWebhook経由の日記追記用トークン。ヘルスチェックサーバーは
# インターネットに公開されているため、これが無ければ機能自体を無効化する。
DIARY_WEBHOOK_TOKEN = os.environ.get('DIARY_WEBHOOK_TOKEN', '')


def _env_hour(name, default):
    """時刻指定の環境変数を0〜23の整数として読む。不正値なら既定値にフォールバックする
    （設定ミスでBotの起動自体が落ちるのを避ける）。"""
    raw = os.environ.get(name, '').strip()
    if raw.isdigit() and 0 <= int(raw) <= 23:
        return int(raw)
    return default


# 日記の書き込み催促DMを送る時刻（JSTの時。分は0固定）。
DIARY_REMINDER_HOUR = _env_hour('DIARY_REMINDER_HOUR', 22)
# 日記本文に出てくる既存ページ名を自動でリンク記法にするか。'0'で無効化できる
# （書いたままの文章を残したい場合に、後から戻せる逃げ道を用意しておく）。
DIARY_AUTOLINK = os.environ.get('DIARY_AUTOLINK', '1').strip() != '0'

PAGES_CACHE_TTL = 300
# complete は「直近の取得で一覧が欠けなく取れたか」。新規ページ通知はこれが False の間、
# 比較を見送る（欠けた一覧を基準にすると既存ページを新規と誤判定するため）。
_pages_cache = {'pages': [], 'ts': 0.0, 'complete': False}
# 日記プロジェクト側のページ一覧（自動リンク用）。共有プロジェクトとは別なので別枠で持つ。
_diary_pages_cache = {'pages': [], 'ts': 0.0}
# 1回の巡回で個別に通知する新規ページ数の上限。これを超えたら1通にまとめる
# （一括インポート等でチャンネルが埋まるのを防ぐ）。
NOTIFY_NEW_PAGES_MAX = 5
NOTIFY_NEW_PAGES_LIST_MAX = 10
_alias_map_cache = None
_known_page_titles = None
_recently_saved_titles = set()

RAG_TOP_N = 8
ASK_COOLDOWN_SECONDS = 30
_ask_cooldowns = {}
# /ask の会話継続用。回答メッセージに作ったスレッドのIDをキーに、そのスレッドでの
# 過去のやり取り（{'q','a'} のリスト）を保持する。再起動で消えるのは許容。
ASK_HISTORY_MAX = 5
ASK_THREADS_MAX = 200
_ask_threads = {}
# リアクションによる操作。📚系でメッセージ内URLを保存、❓系でメッセージ内容をaskに問い合わせ。
SAVE_REACTION_EMOJIS = {'📚', '💾', '🔖'}
ASK_REACTION_EMOJIS = {'❓', '❔'}

# チャンネル⇔案件ページの紐づけ（channel_links モジュールでScrapboxに永続化）。
# None は「未読込」を意味し、初回参照時にScrapboxから読み込む。
_channel_project_links = None

JST = timezone(timedelta(hours=9))

# 日記DMの追記を1件ずつ直列化する。連続でDMを送った際に、fetch→appendの間に
# 別のDMの書き込みが割り込んで内容が消える（同時編集による喪失）のを防ぐ。
_diary_dm_lock = asyncio.Lock()

# --- 反証可能性（observability）用の状態 ---
# 「通知は動いている」「最新コードが動いている」をいつでも検証できるようにする。
_started_at = time.time()
GIT_COMMIT = os.environ.get('RENDER_GIT_COMMIT', '')[:7]  # Renderが自動設定。ローカルでは空
_recent_errors = deque(maxlen=20)   # (unix_ts, source, message)
_task_last_runs = {}                # タスク名 -> {'ts': unix_ts, 'ok': bool, 'detail': str}


def record_error(source, message):
    """バックグラウンド処理のエラーを記録する。printだけだと沈黙の故障になるため、/statusから参照できるようにする。"""
    message = str(message)
    _recent_errors.append((time.time(), source, message[:200]))
    print(f'[{source}] error: {message}')


def _mark_task_run(name, ok, detail=''):
    _task_last_runs[name] = {'ts': time.time(), 'ok': ok, 'detail': str(detail)[:200]}


def _format_uptime(seconds):
    """稼働秒数を「N日N時間」形式の文字列にする"""
    minutes = int(seconds) // 60
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f'{days}日{hours}時間'
    if hours:
        return f'{hours}時間{minutes}分'
    return f'{minutes}分'


def _audit(action, page, actor, detail=''):
    """Botの書き込み操作を監査ログ（Scrapbox: bot設定/監査ログ/YYYY-MM）へ記録する。
    「Botがこのページをいつ・誰の操作で書き換えたか」を事後検証可能にするための機能であり、
    ログ書込の失敗が本処理を壊してはならない（エラーは記録のみ）。同期関数。"""
    try:
        status = audit_log.append_entry(SCRAPBOX_PROJECT, SCRAPBOX_SID, action, page, actor, detail)
        if status != 200:
            record_error('audit', f'監査ログ書込失敗({status})')
    except Exception as e:
        record_error('audit', e)

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


# Scrapboxはタイトルに [ ] を含められない（リンク記法と衝突するため400で弾かれる）。
# YouTubeの動画名には「[Official Music Video]」のような角括弧が頻出するので、
# 情報を落とさないよう丸括弧に置き換えてから保存する。
_TITLE_BRACKETS = str.maketrans({'[': '(', ']': ')'})


def _normalize_title(title):
    """Scrapboxのタイトルとして使える形に整える。改行を含められないため空白類を
    1スペースに畳み、使えない [ ] は丸括弧に置き換える。"""
    return re.sub(r'\s+', ' ', title.translate(_TITLE_BRACKETS)).strip()


def _extract_og_image(html):
    match = re.search(
        r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    match = re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']og:image["\']',
        html,
        re.IGNORECASE,
    )
    return match.group(1) if match else ''


def fetch_metadata(url):
    """戻り値: {'title': str, 'description': str, 'thumbnail': str, 'source': str}
    'source' はどの取得経路を通ったかを示す（デバッグ用）"""
    yt_match = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    if yt_match:
        video_id = yt_match.group(1)

        # YouTube Data API v3（description取得に必要、APIキーが未設定ならスキップ）
        if YOUTUBE_API_KEY:
            try:
                r = requests.get(
                    'https://www.googleapis.com/youtube/v3/videos',
                    params={'part': 'snippet', 'id': video_id, 'key': YOUTUBE_API_KEY},
                    timeout=5,
                )
                items = r.json().get('items', [])
                if items:
                    snippet = items[0]['snippet']
                    return {
                        'title': _normalize_title(snippet.get('title', '')),
                        'description': snippet.get('description', ''),
                        'thumbnail': '',
                        'source': 'YouTube Data API',
                    }
            except Exception:
                pass

        # YouTube oEmbed API（フォールバック、認証不要だがdescriptionは取得不可）
        try:
            r = requests.get(
                f'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json',
                timeout=5,
            )
            if r.status_code == 200:
                return {
                    'title': _normalize_title(r.json().get('title', '')),
                    'description': '',
                    'thumbnail': '',
                    'source': 'YouTube oEmbed（概要欄は取得不可）',
                }
        except Exception:
            pass

    # Vimeo oEmbed API（descriptionも取得できる）
    if 'vimeo.com' in url:
        try:
            r = requests.get(
                f'https://vimeo.com/api/oembed.json?url={url}',
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    'title': _normalize_title(data.get('title', '')),
                    'description': data.get('description', ''),
                    'thumbnail': '',
                    'source': 'Vimeo oEmbed',
                }
        except Exception:
            pass

    # Instagram・TikTok・Twitter(X)などは yt-dlp でメタデータ取得を試みる
    # （データセンターIPからは失敗しうるベストエフォート。失敗時は下の汎用HTMLへフォールバック）
    if ytdlp_extractor.matches(url):
        info = ytdlp_extractor.fetch(url)
        if info:
            return {
                'title': _normalize_title(info['title']),
                'description': info['description'],
                'thumbnail': info['thumbnail'],
                'source': 'yt-dlp',
            }

    # 汎用HTMLタイトル取得（og:imageもサムネイルとして取得）
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        match = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'https?://\S+', '', title).strip()
            title = _normalize_title(title)
            if title:
                return {
                    'title': title,
                    'description': '',
                    'thumbnail': _extract_og_image(r.text),
                    'source': 'HTML <title>（概要欄は取得不可）',
                }
    except Exception:
        pass

    return {'title': urlparse(url).netloc, 'description': '', 'thumbnail': '', 'source': '取得失敗'}


def check_youtube_connection():
    """YouTube Data APIキーの有効性を確認する。戻り値: (ok, message)。未設定時は (None, '未設定')"""
    if not YOUTUBE_API_KEY:
        return None, '未設定'
    try:
        r = requests.get(
            'https://www.googleapis.com/youtube/v3/videos',
            params={'part': 'id', 'id': 'dQw4w9WgXcQ', 'key': YOUTUBE_API_KEY},
            timeout=5,
        )
    except Exception as e:
        return False, str(e)
    if r.status_code == 200:
        return True, '接続OK'
    return False, f'ステータス({r.status_code})'


def _format_status_line(label, result):
    ok, detail = result
    if ok is None:
        return f'⏭️ {label}: {detail}'
    return f'{"✅" if ok else "❌"} {label}: {detail}'


def get_existing_pages_status():
    """(一覧が完全に取れたか, ページ一覧) を返す。取得に失敗したときは欠けた一覧で
    上書きせず、前回取れた一覧をそのまま返す。欠けた一覧を正解として扱うと、
    次に取得できたときに既存ページが「新しく増えた」ように見えてしまう。"""
    now = time.time()
    if now - _pages_cache['ts'] > PAGES_CACHE_TTL:
        ok, pages = name_linker.fetch_all_page_titles(SCRAPBOX_PROJECT, SCRAPBOX_SID)
        # 失敗時もtsは進める（5分間は再取得せず、全ページ取得を叩き続けないため）
        _pages_cache['ts'] = now
        _pages_cache['complete'] = ok
        if ok:
            _pages_cache['pages'] = pages
        else:
            record_error('pages_cache', 'ページ一覧を完全に取得できませんでした（前回の一覧を使います）')
    return _pages_cache['complete'], _pages_cache['pages']


def get_existing_pages():
    return get_existing_pages_status()[1]


def get_diary_pages():
    """日記プロジェクトの既存ページ一覧を返す（自動リンク用、TTL付きキャッシュ）。
    DMを送るたびに全ページを取りに行くと追記が目に見えて遅くなるため、共有プロジェクト側と
    同じくキャッシュする。新しく作ったページが最大 PAGES_CACHE_TTL 秒リンクされないのは許容。"""
    now = time.time()
    if now - _diary_pages_cache['ts'] > PAGES_CACHE_TTL:
        ok, pages = name_linker.fetch_all_page_titles(DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID)
        _diary_pages_cache['ts'] = now
        # 欠けた一覧で上書きするとリンクが取りこぼされるので、失敗時は前回の一覧を使う
        if ok:
            _diary_pages_cache['pages'] = pages
    return _diary_pages_cache['pages']


def autolink_diary_text(text):
    """日記本文中の既存ページ名をリンク記法に変換する（同期関数）。
    リンク化はあくまで付加価値なので、ページ一覧の取得に失敗しても本文は素通しし、
    追記そのものは必ず成功させる。"""
    if not DIARY_AUTOLINK or not text:
        return text
    try:
        pages = diary.linkable_page_titles(get_diary_pages())
        return name_linker.link_known_pages(text, pages)
    except Exception as e:
        record_error('diary_autolink', e)
        return text


def get_alias_map():
    global _alias_map_cache
    if _alias_map_cache is None:
        _alias_map_cache = name_linker.load_alias_map(SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE)
    return _alias_map_cache


def save_to_scrapbox(url, overwrite=False, actor=''):
    metadata = fetch_metadata(url)
    title = metadata['title']

    if not overwrite and name_linker.check_page_exists(SCRAPBOX_PROJECT, SCRAPBOX_SID, title):
        return 'duplicate', None, title, ''

    lines = [title, f'[{url}]']

    thumbnail = metadata.get('thumbnail')
    embedded_thumbnail = ''
    if thumbnail:
        # 直リンクはホットリンク制限で表示できないサイトがあるため、Gyazoにアップロードして恒久URL化する
        embedded_thumbnail = gyazo_uploader.upload_thumbnail(thumbnail) or thumbnail
        lines.append(f'[{embedded_thumbnail}]')

    credits = credit_extractor.extract_credits(metadata['description'])
    if credits:
        pages = get_existing_pages()
        alias_map = get_alias_map()
        for c in credits:
            resolved = name_linker.resolve_name(c['name'], pages, alias_map)
            lines.append(f" {c['role']}: {resolved}")

    payload = json.dumps({'pages': [{'title': title, 'lines': lines}]})
    r = requests.post(
        f'https://scrapbox.io/api/page-data/import/{SCRAPBOX_PROJECT}.json',
        files={'import-file': ('pages.json', payload, 'application/json')},
        headers={
            'Cookie': f'connect.sid={SCRAPBOX_SID}',
            'Origin': 'https://scrapbox.io',
            'Referer': 'https://scrapbox.io',
        },
        timeout=10,
    )
    if r.status_code == 200:
        _recently_saved_titles.add(title)
        _audit('save', title, actor or '自動保存', url)
    return r.status_code, r.text[:300], title, embedded_thumbnail


def write_page_to_scrapbox(title, body_text, actor='', action='write'):
    """Scrapboxに任意テキストのページを書き込む。戻り値: (status_code, body)"""
    lines = [title] + (body_text.splitlines() if body_text else [])
    payload = json.dumps({'pages': [{'title': title, 'lines': lines}]})
    r = requests.post(
        f'https://scrapbox.io/api/page-data/import/{SCRAPBOX_PROJECT}.json',
        files={'import-file': ('pages.json', payload, 'application/json')},
        headers={
            'Cookie': f'connect.sid={SCRAPBOX_SID}',
            'Origin': 'https://scrapbox.io',
            'Referer': 'https://scrapbox.io',
        },
        timeout=10,
    )
    if r.status_code == 200:
        _recently_saved_titles.add(title)
        _audit(action, title, actor)
    return r.status_code, r.text[:300]


# /project create で作る案件ページの雛形（タイトル行は除く）。
# 全案件ページに共通リンク #Karure制作 を付けることで、「Karure制作」ページの
# 逆リンク一覧がそのまま案件一覧として機能する。
PROJECT_PAGE_TEMPLATE = [
    '[* 概要]',
    '',
    '[* データ]',
    '',
    '[* メモ・感想]',
    '',
    '#Karure制作',
]

# 「#タグ」だけで構成される行（ページ末尾のタグブロック判定に使う）
_TAG_ONLY_RE = re.compile(r'^\s*#\S+(?:\s+#\S+)*\s*$')


def _note_insert_index(body_lines):
    """メモの挿入位置を返す。ページ末尾の空行・タグ行ブロックの直前
    （＝雛形なら「メモ・感想」セクションの末尾）に挿入し、タグをページ最下部に保つ。"""
    i = len(body_lines)
    while i > 0:
        line = body_lines[i - 1]
        if not line.strip() or _TAG_ONLY_RE.match(line):
            i -= 1
        else:
            break
    return i


def _build_note_lines(note_text, date_str, author):
    """追記ブロックの行リストを組み立てる。日付・記入者の行の下に本文をインデントで付ける。"""
    lines = [f' {date_str} {author}']
    for line in note_text.splitlines():
        lines.append(f'  {line.rstrip()}')
    return lines


def append_note_to_scrapbox(title, note_text, author, allow_create=False):
    """既存ページに日付・記入者付きでメモを追記する。挿入位置は末尾タグブロックの直前。
    ページが存在しない場合、allow_create=False なら作成せず 'not_found' を返す
    （ページ名のタイポで迷子ページが量産されるのを防ぐ）。
    戻り値: (status_code, body)
    """
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={SCRAPBOX_SID}'},
            timeout=10,
        )
    except Exception as e:
        return None, str(e)

    body_lines = []
    existed = False
    prev_updated = None
    if r.status_code == 200:
        data = r.json()
        if data.get('persistent'):
            existed = True
            prev_updated = data.get('updated')
            page_lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
            body_lines = page_lines[1:]  # 1行目はタイトル行

    if not existed and not allow_create:
        return 'not_found', ''

    date_str = datetime.now(JST).strftime('%Y-%m-%d')
    note_lines = _build_note_lines(note_text, date_str, author)
    idx = _note_insert_index(body_lines)
    body_lines[idx:idx] = note_lines

    payload = json.dumps({'pages': [{'title': title, 'lines': [title] + body_lines}]})
    try:
        r2 = requests.post(
            f'https://scrapbox.io/api/page-data/import/{SCRAPBOX_PROJECT}.json',
            files={'import-file': ('pages.json', payload, 'application/json')},
            headers={
                'Cookie': f'connect.sid={SCRAPBOX_SID}',
                'Origin': 'https://scrapbox.io',
                'Referer': 'https://scrapbox.io',
            },
            timeout=10,
        )
    except Exception as e:
        return None, str(e)
    if r2.status_code == 200:
        if not existed:
            _recently_saved_titles.add(title)
        # 直前のupdated時刻を残す: 同時編集による内容喪失が疑われた際の事後検証に使える
        detail = f'追記{len(note_lines)}行' + (f' / 直前updated:{prev_updated}' if prev_updated else ' / 新規作成')
        _audit('note', title, author, detail)
    return r2.status_code, r2.text[:300]


def fetch_random_article():
    """Scrapboxプロジェクト内の全ページからランダムに1件選ぶ。
    戻り値: {'title': str, 'scrapbox_url': str, 'thumbnail': str, 'description': str} または該当ページが無い場合はNone"""
    pages = get_existing_pages()
    if not pages:
        return None
    title = random.choice(pages)
    scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'

    thumbnail = ''
    description = ''
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={SCRAPBOX_SID}'},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            thumbnail = data.get('image') or ''
            description = '\n'.join(data.get('descriptions', []))
    except Exception:
        pass

    return {'title': title, 'scrapbox_url': scrapbox_url, 'thumbnail': thumbnail, 'description': description}


def _build_result_embed(title, scrapbox_url, thumbnail, description, color):
    embed = discord.Embed(title=title, url=scrapbox_url, description=description, color=color)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def _format_error_reply(status, body):
    if status == 403:
        return '❌ Scrapboxの認証エラー(403): Cookie(SCRAPBOX_SID)が期限切れの可能性があります。管理者に再取得を依頼してください。'
    return f'❌ エラー({status}): {body}'


def expand_urls(urls):
    """プレイリストURLを動画URLの並びに展開する。展開後・展開不要なURLを通して重複を除いたリストを返す"""
    expanded = []
    seen = set()
    for url in urls:
        playlist_id = playlist_loader.extract_playlist_id(url)
        video_urls = playlist_loader.fetch_playlist_video_urls(playlist_id, YOUTUBE_API_KEY) if playlist_id else []
        for u in (video_urls or [url]):
            if u not in seen:
                seen.add(u)
                expanded.append(u)
    return expanded


def process_urls(urls, overwrite=False, actor=''):
    """各URLを保存し、(エラーメッセージのリスト, Embedのリスト) を返す"""
    results = []
    embeds = []
    for url in urls:
        status, body, title, thumbnail = save_to_scrapbox(url, overwrite=overwrite, actor=actor)
        scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
        if status == 'duplicate':
            embeds.append(_build_result_embed(title, scrapbox_url, thumbnail, '既に保存済みです', discord.Color.blue()))
        elif status == 200:
            description = '上書き保存しました' if overwrite else '保存しました'
            embeds.append(_build_result_embed(title, scrapbox_url, thumbnail, description, discord.Color.green()))
        else:
            results.append(_format_error_reply(status, body))
    return results, embeds


@tasks.loop(time=dt_time(hour=21, minute=0, tzinfo=JST))
async def send_daily_random_article():
    try:
        article = await asyncio.to_thread(fetch_random_article)
        if not article:
            return
        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        embed = _build_result_embed(
            article['title'], article['scrapbox_url'], article['thumbnail'], article['description'], discord.Color.purple()
        )
        await channel.send(content='📚 今日のランダム記事', embed=embed)
        _mark_task_run('日次ランダム記事', True)
    except Exception as e:
        record_error('send_daily_random_article', e)
        _mark_task_run('日次ランダム記事', False, e)


def find_new_titles(known_titles, current_titles):
    """既知のタイトル集合と現在の全タイトルを比較し、新規に増えたタイトルのリストを返す"""
    return [title for title in current_titles if title not in known_titles]


def select_notifiable_titles(new_titles):
    """新規タイトルのうち、実際にチャンネルへ通知するものだけを残す。
    Bot自身が保存したページと設定用ページは通知しない（自分の投稿の二重通知になるため）。"""
    notifiable = []
    for title in new_titles:
        if title in _recently_saved_titles:
            _recently_saved_titles.discard(title)
            continue
        if title == CREDIT_MAPPING_PAGE or title.startswith('bot設定'):
            continue
        notifiable.append(title)
    return notifiable


def build_bulk_new_pages_message(titles):
    """新規ページが一度に大量に増えたときの、1通にまとめた通知文を組み立てる"""
    lines = [f'📄 Scrapboxに新しいページが{len(titles)}件増えました']
    lines += [f'・{title}' for title in titles[:NOTIFY_NEW_PAGES_LIST_MAX]]
    if len(titles) > NOTIFY_NEW_PAGES_LIST_MAX:
        lines.append(f'…ほか{len(titles) - NOTIFY_NEW_PAGES_LIST_MAX}件')
    return '\n'.join(lines)


@tasks.loop(minutes=5)
async def notify_new_pages():
    global _known_page_titles
    try:
        complete, current_titles = await asyncio.to_thread(get_existing_pages_status)
        if not complete:
            # 欠けた一覧を基準にすると、次に取得できたときに既存ページが全部「新規」になる
            _mark_task_run('新規ページ通知', False, 'ページ一覧を取得できず今回は比較を見送り')
            return
        if _known_page_titles is None:
            _known_page_titles = set(current_titles)
            _mark_task_run('新規ページ通知', True, '初回ベースライン記録')
            return

        new_titles = find_new_titles(_known_page_titles, current_titles)
        _known_page_titles = set(current_titles)
        notifiable = select_notifiable_titles(new_titles)
        if not notifiable:
            _mark_task_run('新規ページ通知', True)
            return

        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        if len(notifiable) > NOTIFY_NEW_PAGES_MAX:
            # 一度にこの数を超えるのは一括インポート等の異常時。1件ずつ流すとチャンネルが埋まる
            await channel.send(build_bulk_new_pages_message(notifiable))
            _mark_task_run('新規ページ通知', True, f'{len(notifiable)}件をまとめて通知')
            return
        for title in notifiable:
            scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
            embed = _build_result_embed(title, scrapbox_url, '', 'Scrapboxに新しいページが投稿されました', discord.Color.gold())
            await channel.send(embed=embed)
        _mark_task_run('新規ページ通知', True)
    except Exception as e:
        record_error('notify_new_pages', e)
        _mark_task_run('新規ページ通知', False, e)


def run_daily_health_checks():
    """必須・任意の外部サービス接続を確認する。戻り値: 異常があった項目のメッセージのリスト（正常時は空リスト）"""
    checks = [
        ('Scrapbox', name_linker.check_connection, (SCRAPBOX_PROJECT, SCRAPBOX_SID)),
        ('YouTube Data API', check_youtube_connection, ()),
        ('OpenRouter(AI)', credit_extractor.check_connection, ()),
    ]
    problems = []
    for label, func, args in checks:
        try:
            ok, detail = func(*args)
        except Exception as e:
            ok, detail = False, str(e)
        if ok is False:
            problems.append(f'❌ {label}: {detail}')
    return problems


@tasks.loop(time=dt_time(hour=8, minute=0, tzinfo=JST))
async def daily_health_check():
    try:
        problems = await asyncio.to_thread(run_daily_health_checks)
        if not problems:
            _mark_task_run('日次ヘルスチェック', True)
            return
        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        await channel.send('⚠️ 日次ヘルスチェックで異常を検出しました\n' + '\n'.join(problems))
        _mark_task_run('日次ヘルスチェック', True, f'異常{len(problems)}件を通知')
    except Exception as e:
        record_error('daily_health_check', e)
        _mark_task_run('日次ヘルスチェック', False, e)


@tasks.loop(time=dt_time(hour=0, minute=5, tzinfo=JST))
async def create_daily_diary_page_task():
    """個人の日記ページ（Karureの共有プロジェクトとは別のScrapboxプロジェクト）を
    日付タイトル・雛形付きで自動作成する。Discordへの通知は行わない（裏で完結する）。"""
    try:
        status, title = await asyncio.to_thread(diary.create_diary_page, DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID)
        if status in ('created', 'exists'):
            _mark_task_run('日記ページ作成', True, f'{status}: {title}')
        else:
            record_error('diary', f'{title} の作成に失敗（ステータス:{status}）')
            _mark_task_run('日記ページ作成', False, f'ステータス:{status}')
    except Exception as e:
        record_error('diary', e)
        _mark_task_run('日記ページ作成', False, e)


def build_diary_reminder_message(title, project, dt=None):
    """日記の催促DM本文を組み立てる。そのまま返信すれば追記されることを毎回添え、
    「開いて書く」より軽い手段を提示する。あわせてその日のお題を1つ出し、
    「何を書けばいいか分からない」で止まらないようにする。"""
    url = f'https://scrapbox.io/{project}/{requests.utils.quote(title)}'
    prompt = diary.prompt_for(dt or datetime.now(JST))
    return (
        f'📔 {title} の日記がまだ空です。\n'
        f'今日のお題: {prompt}\n'
        'このDMに返信すれば【日記】欄に、「単語:〇〇」なら【新しく知った単語】欄に追記されます。'
        '「ページ:〇〇」で新しいページも作れます。写真もそのまま送れます。\n'
        f'{url}'
    )


async def send_diary_reminder_dm(title):
    user = client.get_user(DIARY_OWNER_USER_ID) or await client.fetch_user(DIARY_OWNER_USER_ID)
    await user.send(build_diary_reminder_message(title, DIARY_SCRAPBOX_PROJECT))


@tasks.loop(time=dt_time(hour=DIARY_REMINDER_HOUR, minute=0, tzinfo=JST))
async def diary_reminder_task():
    """その日の日記ページが雛形のまま（または未作成）なら、本人にDMで書き込みを催促する。
    既に何か書いてあれば何も送らない（毎日必ず届く通知は読み飛ばされるようになるため）。
    Scrapboxに繋がらず確認できなかった場合も送らない（空だと誤断定しないため）。"""
    try:
        state, title = await asyncio.to_thread(
            diary.check_diary_written, DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID
        )
        if state is None:
            record_error('diary_reminder', f'{title} の記入確認に失敗（Scrapboxに接続できません）')
            _mark_task_run('日記リマインド', False, '記入確認に失敗')
            return
        if state == 'written':
            _mark_task_run('日記リマインド', True, f'記入済みのため催促なし: {title}')
            return
        await send_diary_reminder_dm(title)
        _mark_task_run('日記リマインド', True, f'催促DMを送信: {title}')
    except Exception as e:
        record_error('diary_reminder', e)
        _mark_task_run('日記リマインド', False, e)


@client.event
async def on_ready():
    print(f'Bot ready: {client.user}')
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    if not send_daily_random_article.is_running():
        send_daily_random_article.start()
    if not daily_health_check.is_running():
        daily_health_check.start()
    if not notify_new_pages.is_running():
        notify_new_pages.start()
    if DIARY_SCRAPBOX_PROJECT and DIARY_SCRAPBOX_SID and not create_daily_diary_page_task.is_running():
        create_daily_diary_page_task.start()
    # 催促DMの宛先が要るため、DM追記と同じく DIARY_OWNER_USER_ID も揃って初めて起動する
    if DIARY_SCRAPBOX_PROJECT and DIARY_SCRAPBOX_SID and DIARY_OWNER_USER_ID and not diary_reminder_task.is_running():
        diary_reminder_task.start()


@tree.command(name='save', description='URLをScrapboxに保存します')
@discord.app_commands.describe(url='保存したいURL', overwrite='既存ページがあっても上書き保存する')
async def save_command(interaction: discord.Interaction, url: str, overwrite: bool = False):

    await interaction.response.defer()
    urls = await asyncio.to_thread(expand_urls, [url])
    results, embeds = await asyncio.to_thread(process_urls, urls, overwrite=overwrite, actor=interaction.user.display_name)
    content = f'{interaction.user.display_name}\n{url}'

    if embeds:
        await interaction.followup.send(content=content, embeds=embeds[:10])
    if results:
        await interaction.followup.send('\n'.join(results))


@tree.command(name='status', description='Bot・Scrapbox・外部APIの疎通状況を確認します')
async def status_command(interaction: discord.Interaction):

    await interaction.response.defer()
    lines = [
        '✅ Discord: 接続中',
        _format_status_line('Scrapbox', await asyncio.to_thread(name_linker.check_connection, SCRAPBOX_PROJECT, SCRAPBOX_SID)),
        _format_status_line('YouTube Data API', await asyncio.to_thread(check_youtube_connection)),
        _format_status_line('OpenRouter(AI)', await asyncio.to_thread(credit_extractor.check_connection)),
        _format_status_line('Gyazo', await asyncio.to_thread(gyazo_uploader.check_connection)),
    ]
    lines.extend(_build_observability_lines())
    await interaction.followup.send('\n'.join(lines))


def _build_observability_lines(now=None):
    """稼働情報・定期タスクの最終実行・直近エラーを /status 用の行リストにする。
    「動いているはず」を検証可能な事実に変えるための表示。"""
    now = now if now is not None else time.time()
    lines = ['', f'🕒 稼働時間: {_format_uptime(now - _started_at)}' + (f' / version: {GIT_COMMIT}' if GIT_COMMIT else '')]
    for name, info in _task_last_runs.items():
        mark = '✅' if info['ok'] else '❌'
        at = datetime.fromtimestamp(info['ts'], JST).strftime('%m/%d %H:%M')
        suffix = f'（{info["detail"][:80]}）' if info['detail'] else ''
        lines.append(f'{mark} {name}: 最終実行 {at}{suffix}')
    if _recent_errors:
        lines.append(f'⚠️ 直近エラー（{len(_recent_errors)}件記録）:')
        for ts, source, message in list(_recent_errors)[-3:]:
            at = datetime.fromtimestamp(ts, JST).strftime('%m/%d %H:%M')
            lines.append(f'　{at} [{source}] {message[:100]}')
    return lines


@tree.command(name='debug', description='URLのメタデータ取得結果（概要欄・クレジット抽出結果など）を確認します')
@discord.app_commands.describe(url='確認したいURL')
async def debug_command(interaction: discord.Interaction, url: str):

    await interaction.response.defer()
    metadata = await asyncio.to_thread(fetch_metadata, url)
    raw_description = metadata.get('description') or ''
    description = raw_description or '(概要欄なし、または取得不可)'
    if len(description) > 1500:
        description = description[:1500] + '\n...(省略)'

    embed = discord.Embed(
        title=metadata.get('title') or '(タイトル取得失敗)',
        description=description,
        color=discord.Color.orange(),
    )
    embed.add_field(name='取得元', value=metadata.get('source', '不明'), inline=False)

    if not credit_extractor.OPENROUTER_API_KEY:
        credits_text = '(OPENROUTER_API_KEY未設定のためスキップ)'
    else:
        credits, raw_response, error = await asyncio.to_thread(credit_extractor.extract_credits_debug, raw_description)
        if error:
            credits_text = f'❌ {error}'
            if raw_response:
                credits_text += f'\n--- 生レスポンス ---\n{raw_response}'
        elif credits:
            credits_text = '\n'.join(f"{c['role']}: {c['name']}" for c in credits)
        else:
            credits_text = '(抽出結果なし。LLMの生レスポンス↓)\n' + (raw_response or '(レスポンスなし)')
    if len(credits_text) > 1000:
        credits_text = credits_text[:1000] + '\n...(省略)'
    embed.add_field(name='クレジット抽出結果(OpenRouter)', value=credits_text, inline=False)

    thumbnail = metadata.get('thumbnail')
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    await interaction.followup.send(embed=embed)


class WriteModal(discord.ui.Modal, title='Scrapboxに書き込む'):
    page_title = discord.ui.TextInput(
        label='タイトル',
        placeholder='ページのタイトルを入力',
        required=True,
        max_length=200,
    )
    body = discord.ui.TextInput(
        label='本文',
        style=discord.TextStyle.paragraph,
        placeholder='本文を入力（改行可）',
        required=False,
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        title = _normalize_title(self.page_title.value)
        body_text = self.body.value or ''
        try:
            status, body = await asyncio.to_thread(write_page_to_scrapbox, title, body_text, interaction.user.display_name)
        except Exception as e:
            await interaction.followup.send(f'❌ エラー: {e}')
            return
        if status == 200:
            scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
            embed = _build_result_embed(title, scrapbox_url, '', body_text[:200] if body_text else '', discord.Color.green())
            await interaction.followup.send('✅ 保存しました', embed=embed)
        else:
            await interaction.followup.send(_format_error_reply(status, body))


@tree.command(name='write', description='Scrapboxに新しいページを作成します')
async def write_command(interaction: discord.Interaction):
    await interaction.response.send_modal(WriteModal())


def _get_channel_links_sync():
    """チャンネル⇔案件ページの紐づけを返す（未読込ならScrapboxから読む）。同期関数。"""
    global _channel_project_links
    if _channel_project_links is None:
        loaded = channel_links.load_links(SCRAPBOX_PROJECT, SCRAPBOX_SID)
        if loaded is None:
            return {}  # 通信失敗。キャッシュは作らず次回再試行する
        _channel_project_links = loaded
    return _channel_project_links


def _save_channel_links_sync(links):
    """紐づけをScrapboxへ保存し、成功したらメモリも更新する。戻り値: 成否"""
    global _channel_project_links
    status, _ = channel_links.save_links(SCRAPBOX_PROJECT, SCRAPBOX_SID, links)
    if status == 200:
        _channel_project_links = links
        _recently_saved_titles.add(channel_links.LINKS_PAGE_TITLE)
        return True
    return False


async def _page_autocomplete(interaction: discord.Interaction, current: str):
    """既存ページタイトルの入力候補（タイポによる迷子ページ防止）。"""
    try:
        pages = await asyncio.to_thread(get_existing_pages)
    except Exception:
        return []
    current_lower = current.lower()
    choices = []
    for title in pages:
        # Discordの制約でChoiceは100文字まで。bot設定ページは候補に出さない
        if len(title) > 100 or title.startswith('bot設定'):
            continue
        if current_lower in title.lower():
            choices.append(discord.app_commands.Choice(name=title, value=title))
            if len(choices) >= 25:
                break
    return choices


class NoteModal(discord.ui.Modal, title='ページに追記'):
    def __init__(self, page_title, allow_create=False):
        super().__init__()
        self.page_title = page_title
        self.allow_create = allow_create
        self.note = discord.ui.TextInput(
            label=f'{page_title[:40]} への追記',
            style=discord.TextStyle.paragraph,
            placeholder='データ・メモ・感想などを入力（改行可）',
            required=True,
            max_length=4000,
        )
        self.add_item(self.note)

    def _recovery_text(self):
        """失敗時に入力内容を失わせないためのコピー用ブロック"""
        return f'\n入力内容（コピー用）:\n```\n{self.note.value[:1700]}\n```'

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        author = interaction.user.display_name
        try:
            status, body = await asyncio.to_thread(
                append_note_to_scrapbox, self.page_title, self.note.value, author, self.allow_create
            )
        except Exception as e:
            await interaction.followup.send(f'❌ エラー: {e}' + self._recovery_text())
            return
        if status == 200:
            scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(self.page_title)}'
            embed = _build_result_embed(self.page_title, scrapbox_url, '', self.note.value[:200], discord.Color.green())
            await interaction.followup.send('✅ 追記しました', embed=embed)
        elif status == 'not_found':
            await interaction.followup.send(
                f'❌ ページ「{self.page_title}」が見つかりませんでした（タイポ防止のため、存在しないページには追記しません）。\n'
                f'新しいページとして作成する場合は `/note page:{self.page_title} create:True` を実行してください。'
                + self._recovery_text()
            )
        else:
            await interaction.followup.send(_format_error_reply(status, body) + self._recovery_text())


@tree.command(name='note', description='ページにメモ・感想を追記します（このチャンネルに紐づく案件ページには page 省略可）')
@discord.app_commands.describe(
    page='追記先のページ名（省略時はこのチャンネルに紐づく案件ページ）',
    create='ページが存在しない場合に新規作成する',
)
async def note_command(interaction: discord.Interaction, page: str = None, create: bool = False):
    if page is None:
        links = await asyncio.to_thread(_get_channel_links_sync)
        page = links.get(interaction.channel_id)
        if not page:
            await interaction.response.send_message(
                'このチャンネルに紐づく案件ページがありません。`page:` でページ名を指定するか、'
                '`/project link` でこのチャンネルに案件ページを紐づけてください。',
                ephemeral=True,
            )
            return
    page = _normalize_title(page)
    if not page:
        await interaction.response.send_message('ページ名を入力してください', ephemeral=True)
        return
    await interaction.response.send_modal(NoteModal(page, allow_create=create))


@note_command.autocomplete('page')
async def note_page_autocomplete(interaction: discord.Interaction, current: str):
    return await _page_autocomplete(interaction, current)


project_group = discord.app_commands.Group(name='project', description='案件ページを管理します')
tree.add_command(project_group)


@project_group.command(name='create', description='案件ページを雛形付きで作成し、このチャンネルに紐づけます')
@discord.app_commands.describe(name='案件名（ページタイトルになります）')
async def project_create_command(interaction: discord.Interaction, name: str):
    name = _normalize_title(name)
    if not name:
        await interaction.response.send_message('案件名を入力してください', ephemeral=True)
        return

    await interaction.response.defer()
    exists = await asyncio.to_thread(name_linker.check_page_exists, SCRAPBOX_PROJECT, SCRAPBOX_SID, name)
    scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(name)}'
    if exists:
        embed = _build_result_embed(name, scrapbox_url, '', '同名のページが既に存在します。このチャンネルに紐づけるには /project link を使ってください', discord.Color.blue())
        await interaction.followup.send(embed=embed)
        return

    status, body = await asyncio.to_thread(
        write_page_to_scrapbox, name, '\n'.join(PROJECT_PAGE_TEMPLATE), interaction.user.display_name, 'project-create'
    )
    if status != 200:
        await interaction.followup.send(_format_error_reply(status, body))
        return

    links = dict(await asyncio.to_thread(_get_channel_links_sync))
    links[interaction.channel_id] = name
    linked = await asyncio.to_thread(_save_channel_links_sync, links)
    note_hint = 'このチャンネルに紐づけました。/note だけで追記できます' if linked else '作成しました（チャンネルへの紐づけ保存には失敗しました。/project link で再試行できます）'
    embed = _build_result_embed(name, scrapbox_url, '', f'案件ページを作成しました。{note_hint}', discord.Color.green())
    await interaction.followup.send(embed=embed)


@project_group.command(name='link', description='既存の案件ページをこのチャンネルに紐づけます')
@discord.app_commands.describe(page='紐づける既存ページ名')
async def project_link_command(interaction: discord.Interaction, page: str):
    page = _normalize_title(page)
    if not page:
        await interaction.response.send_message('ページ名を入力してください', ephemeral=True)
        return

    await interaction.response.defer()
    exists = await asyncio.to_thread(name_linker.check_page_exists, SCRAPBOX_PROJECT, SCRAPBOX_SID, page)
    if not exists:
        await interaction.followup.send(f'❌ ページ「{page}」が見つかりません（存在するページのみ紐づけできます）')
        return

    links = dict(await asyncio.to_thread(_get_channel_links_sync))
    links[interaction.channel_id] = page
    if await asyncio.to_thread(_save_channel_links_sync, links):
        await asyncio.to_thread(_audit, 'project-link', page, interaction.user.display_name, f'channel:{interaction.channel_id}')
        scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(page)}'
        embed = _build_result_embed(page, scrapbox_url, '', 'このチャンネルに紐づけました。/note だけで追記できます', discord.Color.green())
        await interaction.followup.send(embed=embed)
    else:
        await interaction.followup.send('❌ 紐づけの保存に失敗しました')


@project_link_command.autocomplete('page')
async def project_link_autocomplete(interaction: discord.Interaction, current: str):
    return await _page_autocomplete(interaction, current)


@project_group.command(name='unlink', description='このチャンネルと案件ページの紐づけを解除します')
async def project_unlink_command(interaction: discord.Interaction):
    await interaction.response.defer()
    links = dict(await asyncio.to_thread(_get_channel_links_sync))
    page = links.pop(interaction.channel_id, None)
    if page is None:
        await interaction.followup.send('このチャンネルに紐づいている案件ページはありません')
        return
    if await asyncio.to_thread(_save_channel_links_sync, links):
        await asyncio.to_thread(_audit, 'project-unlink', page, interaction.user.display_name, f'channel:{interaction.channel_id}')
        await interaction.followup.send(f'「{page}」との紐づけを解除しました')
    else:
        await interaction.followup.send('❌ 紐づけ解除の保存に失敗しました')


def _clean_question(question):
    """制御文字を除去し500文字で切り詰める。戻り値: (cleaned, truncated)"""
    cleaned = ''.join(ch for ch in question if ch >= ' ' or ch == '\n').strip()
    truncated = len(cleaned) > 500
    if truncated:
        cleaned = cleaned[:500]
    return cleaned, truncated


def _format_ask_error(error, query):
    if error == 'auth':
        return _format_error_reply(403, '')
    if error.startswith('no_hits'):
        _, _, searched = error.partition(':')
        return f'Scrapbox内に該当する情報が見つかりませんでした（検索語: {searched or query}）'
    if error == 'search':
        return '❌ Scrapbox検索でエラーが発生しました'
    if error.startswith('llm:'):
        return f'❌ AI応答の生成に失敗しました（{error[4:]}）'
    return f'❌ エラー: {error}'


@tree.command(name='ask', description='Scrapboxの内容に基づいて質問に答えます')
@discord.app_commands.describe(question='質問文')
async def ask_command(interaction: discord.Interaction, question: str):
    if not rag_qa.OPENROUTER_API_KEY:
        await interaction.response.send_message('OPENROUTER_API_KEYが未設定のため利用できません', ephemeral=True)
        return

    now = time.time()
    remaining = ASK_COOLDOWN_SECONDS - (now - _ask_cooldowns.get(interaction.user.id, 0))
    if remaining > 0:
        await interaction.response.send_message(
            f'連続実行はできません。あと{int(remaining) + 1}秒お待ちください', ephemeral=True
        )
        return

    cleaned, truncated = _clean_question(question)
    if not cleaned:
        await interaction.response.send_message('質問を入力してください', ephemeral=True)
        return

    _ask_cooldowns[interaction.user.id] = now
    await interaction.response.defer()

    answer, sources, error = await asyncio.to_thread(
        rag_qa.answer_question, cleaned, SCRAPBOX_PROJECT, SCRAPBOX_SID, RAG_TOP_N
    )
    if error:
        await interaction.followup.send(_format_ask_error(error, cleaned))
        return

    prefix = '⚠️ 質問が長いため500文字で切り詰めました\n' if truncated else None
    sent = await interaction.followup.send(content=prefix, embed=_build_answer_embed(answer, sources))
    await _start_ask_thread(sent, cleaned, answer)


def _build_answer_embed(answer, sources):
    description = answer if len(answer) <= 4096 else answer[:4000] + '\n…(省略)'
    embed = discord.Embed(title='回答', description=description, color=discord.Color.teal())
    if sources:
        links = [
            f'[{title}](https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)})'
            for title in sources
        ]
        source_text = '\n'.join(links)
        if len(source_text) > 1024:
            source_text = source_text[:1000] + '\n…(省略)'
        embed.add_field(name='出典', value=source_text, inline=False)
    embed.set_footer(text=f'model: {rag_qa.OPENROUTER_QA_MODEL}')
    return embed


async def _start_ask_thread(message, question, answer):
    """回答メッセージにスレッドを作り、会話継続の起点として初回のやり取りを記録する。"""
    try:
        thread = await message.create_thread(name=question[:90] or '/ask', auto_archive_duration=60)
    except Exception as e:
        record_error('ask_thread', f'create failed: {e}')
        return
    if len(_ask_threads) >= ASK_THREADS_MAX:
        _ask_threads.pop(next(iter(_ask_threads)), None)
    _ask_threads[thread.id] = [{'q': question, 'a': answer}]
    try:
        await thread.send('このスレッドで続けて質問できます（「その人の他の作品は？」のような追い質問もOK）。')
    except Exception:
        pass


async def handle_ask_followup(message):
    """/ask で作られたスレッド内の追い質問を、これまでの会話履歴付きで処理する。"""
    history = _ask_threads.get(message.channel.id)
    if history is None or not rag_qa.OPENROUTER_API_KEY:
        return

    now = time.time()
    remaining = ASK_COOLDOWN_SECONDS - (now - _ask_cooldowns.get(message.author.id, 0))
    if remaining > 0:
        await message.reply(f'連続実行はできません。あと{int(remaining) + 1}秒お待ちください')
        return

    cleaned, _ = _clean_question(message.content)
    if not cleaned:
        return
    _ask_cooldowns[message.author.id] = now

    async with message.channel.typing():
        answer, sources, error = await asyncio.to_thread(
            rag_qa.answer_question, cleaned, SCRAPBOX_PROJECT, SCRAPBOX_SID, RAG_TOP_N,
            history=history[-ASK_HISTORY_MAX:],
        )
    if error:
        await message.reply(_format_ask_error(error, cleaned))
        return

    await message.reply(embed=_build_answer_embed(answer, sources))
    history.append({'q': cleaned, 'a': answer})
    del history[:-ASK_HISTORY_MAX]


@tree.command(name='ask-debug', description='直近の /ask の内部状態（検索キーワード・ヒット・投入コンテキスト）を表示します')
async def ask_debug_command(interaction: discord.Interaction):
    trace = rag_qa.last_trace
    if not trace:
        await interaction.response.send_message('直近の /ask 実行の記録がありません（Bot再起動後は消えます）', ephemeral=True)
        return

    at = datetime.fromtimestamp(trace['ts'], JST).strftime('%m/%d %H:%M:%S')
    embed = discord.Embed(
        title='直近の /ask 内部トレース',
        description=f'実行: {at} JST\n質問: {trace["question"][:500]}',
        color=discord.Color.orange(),
    )
    hits_text = '\n'.join(f'・{kw} → {hits}' for kw, hits in trace['hits']) or '(検索前に終了)'
    embed.add_field(name='検索キーワードとヒット件数', value=hits_text[:1024], inline=False)
    selected_text = '\n'.join(f'・{title}（スコア{score}）' for title, score in trace['selected']) or '(採用ページなし)'
    embed.add_field(name='コンテキストに採用したページ', value=selected_text[:1024], inline=False)
    embed.add_field(name='投入コンテキスト', value=f'{trace["context_chars"]}文字', inline=True)
    embed.add_field(name='結果', value=(f'エラー: {trace["error"]}'[:1024] if trace['error'] else '回答生成に成功'), inline=True)
    embed.set_footer(text=f'model: {trace["model"]}')
    await interaction.response.send_message(embed=embed)


@tree.command(name='search', description='Scrapboxをキーワード検索します')
@discord.app_commands.describe(query='検索キーワード')
async def search_command(interaction: discord.Interaction, query: str):
    cleaned = query.strip()
    if not cleaned:
        await interaction.response.send_message('検索キーワードを入力してください', ephemeral=True)
        return

    await interaction.response.defer()
    pages, error = await asyncio.to_thread(
        scrapbox_search.search_pages, SCRAPBOX_PROJECT, SCRAPBOX_SID, cleaned, 20
    )
    if error == 'auth':
        await interaction.followup.send(_format_error_reply(403, ''))
        return
    if error:
        await interaction.followup.send('❌ Scrapbox検索でエラーが発生しました')
        return
    if not pages:
        await interaction.followup.send(f'「{cleaned}」に一致するページは見つかりませんでした')
        return

    links = []
    seen = set()
    for p in pages:
        title = p['title']
        if title in seen:
            continue
        seen.add(title)
        links.append(f'[{title}](https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)})')
    description = '\n'.join(links)
    if len(description) > 4096:
        description = description[:4000] + '\n…(省略)'
    embed = discord.Embed(title=f'検索結果: {cleaned}', description=description, color=discord.Color.blue())
    embed.set_footer(text=f'{len(links)}件')
    await interaction.followup.send(embed=embed)


alias_group = discord.app_commands.Group(name='alias', description='人物名の表記ゆれを管理します')
tree.add_command(alias_group)


def _check_alias_command_allowed(interaction, require_permission):
    if not CREDIT_MAPPING_PAGE:
        return 'CREDIT_MAPPING_PAGEが設定されていません'
    if require_permission:
        permissions = getattr(interaction.user, 'guild_permissions', None)
        if not permissions or not permissions.manage_guild:
            return 'このコマンドの実行には「サーバーの管理」権限が必要です'
    return None


@alias_group.command(name='add', description='表記ゆれを追加します')
@discord.app_commands.describe(canonical='本名（正式表記）', alias='追加する別名')
async def alias_add_command(interaction: discord.Interaction, canonical: str, alias: str):
    error = _check_alias_command_allowed(interaction, require_permission=True)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer()
    status, body = await asyncio.to_thread(name_linker.add_alias, SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE, canonical, alias)

    if status == 200:
        global _alias_map_cache
        _alias_map_cache = None
        await asyncio.to_thread(_audit, 'alias-add', CREDIT_MAPPING_PAGE, interaction.user.display_name, f'{canonical} == {alias}')
        await interaction.followup.send(f'{canonical} == {alias} を登録しました')
    else:
        await interaction.followup.send(_format_error_reply(status, body))


@alias_group.command(name='remove', description='表記ゆれを削除します')
@discord.app_commands.describe(canonical='本名（正式表記）', alias='削除する別名')
async def alias_remove_command(interaction: discord.Interaction, canonical: str, alias: str):
    error = _check_alias_command_allowed(interaction, require_permission=True)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer()
    status, body = await asyncio.to_thread(name_linker.remove_alias, SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE, canonical, alias)

    if status == 200:
        global _alias_map_cache
        _alias_map_cache = None
        await asyncio.to_thread(_audit, 'alias-remove', CREDIT_MAPPING_PAGE, interaction.user.display_name, f'{canonical} == {alias}')
        await interaction.followup.send(f'{canonical} == {alias} を削除しました')
    elif status == 404:
        await interaction.followup.send(f'❌ {body}')
    else:
        await interaction.followup.send(_format_error_reply(status, body))


@alias_group.command(name='list', description='登録済みの表記ゆれ一覧を表示します')
async def alias_list_command(interaction: discord.Interaction):
    error = _check_alias_command_allowed(interaction, require_permission=False)
    if error:
        await interaction.response.send_message(error, ephemeral=True)
        return

    await interaction.response.defer()
    lines = await asyncio.to_thread(name_linker.list_aliases, SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE)
    if not lines:
        await interaction.followup.send('登録されている表記ゆれはありません')
        return
    text = '\n'.join(lines)
    if len(text) > 1900:
        text = text[:1900] + '\n...(省略)'
    await interaction.followup.send(text)


async def upload_diary_images(attachments):
    """DMに添付された画像をGyazoにアップロードし、Scrapboxに貼れる行のリストを返す。
    Discordの添付URLは時間が経つと失効するため、そのまま貼らずGyazoの恒久URLに変換する。
    画像以外の添付は無視する（Scrapboxに貼っても表示できないため）。"""
    lines = []
    for attachment in attachments:
        if not (attachment.content_type or '').startswith('image/'):
            continue
        try:
            data = await attachment.read()
            url = await asyncio.to_thread(gyazo_uploader.upload_image, data, attachment.filename)
        except Exception as e:
            record_error('diary_dm', e)
            continue
        if url:
            lines.append(f'[{url}]')
        else:
            record_error('diary_dm', f'{attachment.filename} のGyazoアップロードに失敗しました')
    return lines


def build_page_body_lines(body_text, image_lines):
    """「ページ:」DMの本文を、新規ページに書く行リストに変換する。
    日記への追記と違いインデントは付けない（そのページの本文そのものになるため）。"""
    lines = [line.rstrip() for line in body_text.splitlines()] if body_text else []
    while lines and not lines[-1].strip():
        lines.pop()
    return lines + image_lines


async def create_diary_side_page(title, body_text, image_lines):
    """日記プロジェクトに任意タイトルのページを作り、その日の日記からリンクする。
    リンクを張らないとどこからも辿れないページになるため、作成と同時に【日記】欄へ
    [タイトル] を1行追記する（日記が索引として機能する形を保つ）。
    戻り値: (status, title) — status は diary.create_page と同じ。
    日記へのリンク追記に失敗した場合はページ自体は残るが、成否は失敗として返す。"""
    title = _normalize_title(title)
    if not title:
        record_error('diary_dm', '「ページ:」に続くページ名が空でした')
        return 'no-title', ''

    body_text = await asyncio.to_thread(autolink_diary_text, body_text)
    body_lines = build_page_body_lines(body_text, image_lines)
    status, title = await asyncio.to_thread(
        diary.create_page, DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID, title, body_lines
    )
    if status not in ('created', 'appended'):
        return status, title

    # 作ったばかりのページを本文中の自動リンク対象にするため、一覧を取り直させる
    _diary_pages_cache['ts'] = 0.0
    link_status, _ = await asyncio.to_thread(
        diary.append_diary_entry, DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID, f'[{title}]', 'diary'
    )
    if link_status != 'appended':
        record_error('diary_dm', f'{title} は作成しましたが、日記からのリンク追記に失敗しました（ステータス:{link_status}）')
        return link_status, title
    return status, title


def build_diary_entries(text, image_lines):
    """DM1通を「どの欄に何を書くか」の (section, 本文) リストに変換する。
    写真は【日記】欄に入れる（その日の記録であって単語ではないため）ので、
    「単語:」で始まるDMに写真が付いていた場合だけ2件に分かれる。"""
    section, body = diary.classify_entry(text) if text else ('diary', '')
    if section == 'vocab':
        entries = [('vocab', body)] if body else []
        if image_lines:
            entries.append(('diary', '\n'.join(image_lines)))
        return entries
    combined = '\n'.join(([body] if body else []) + image_lines)
    return [('diary', combined)] if combined else []


async def handle_diary_dm(message):
    """個人のDMで送った内容を、その日の日記ページに自動追記する。先頭が「単語:」の
    場合は【新しく知った単語】欄、それ以外は【日記】欄に入る。先頭が「ページ:」の
    場合だけは日記への追記ではなく、1行目をタイトルとした独立ページを作る。
    画像を添付するとGyazo経由で貼られる（本文なしで写真だけ送ってもよい）。
    DIARY_SCRAPBOX_PROJECT/SID/DIARY_OWNER_USER_ID がすべて設定済みで、かつ送信者が
    本人（DIARY_OWNER_USER_ID）の場合のみ動作する。Karureサーバーの誰でもこのBotに
    DMを送れてしまうため、本人確認は必須（でなければ他人のDMが日記に混入する）。"""
    if not (DIARY_SCRAPBOX_PROJECT and DIARY_SCRAPBOX_SID and DIARY_OWNER_USER_ID):
        return
    if message.author.id != DIARY_OWNER_USER_ID:
        return
    text = message.content.strip()
    if not text and not message.attachments:
        return

    image_lines = await upload_diary_images(message.attachments)
    page_title, page_body = diary.parse_page_entry(text)
    entries = [] if page_title is not None else build_diary_entries(text, image_lines)
    if page_title is None and not entries:
        return

    ok, status, title = False, None, None
    async with _diary_dm_lock:
        if page_title is not None:
            try:
                status, title = await create_diary_side_page(page_title, page_body, image_lines)
            except Exception as e:
                record_error('diary_dm', e)
            ok = status in ('created', 'appended')
        else:
            for section, body in entries:
                if section == 'diary':
                    body = await asyncio.to_thread(autolink_diary_text, body)
                try:
                    status, title = await asyncio.to_thread(
                        diary.append_diary_entry, DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID, body, section
                    )
                except Exception as e:
                    record_error('diary_dm', e)
                    status, title = None, None
                ok = status == 'appended'
                if not ok:
                    break

    try:
        await message.add_reaction('✅' if ok else '❌')
    except Exception:
        pass
    if not ok and title:
        record_error('diary_dm', f'{title} への書き込み失敗（ステータス:{status}）')


@client.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild is None:
        await handle_diary_dm(message)
        return
    # /ask で作られたスレッド内の追い質問は会話継続として処理する
    if isinstance(message.channel, discord.Thread) and message.channel.id in _ask_threads:
        await handle_ask_followup(message)
        return
    print(f'[msg] ch={message.channel.id} author={message.author} content={message.content[:50]!r}')
    if message.channel.id != CHANNEL_ID:
        print(f'[skip] channel mismatch: {message.channel.id} != {CHANNEL_ID}')
        return
    if KEYWORD and KEYWORD not in message.content:
        print(f'[skip] keyword not found: {KEYWORD!r}')
        return

    urls = await asyncio.to_thread(expand_urls, re.findall(r'https?://[^\s<>"]+', message.content))
    print(f'[urls] {urls}')
    if not urls:
        await message.reply('URLが見つかりませんでした')
        return
    results, embeds = await asyncio.to_thread(process_urls, urls, actor=message.author.display_name)
    await message.reply(content='\n'.join(results) or None, embeds=embeds[:10])


def _reaction_action(emoji):
    """リアクション絵文字から実行するアクションを返す。'save' / 'ask' / None"""
    if emoji in SAVE_REACTION_EMOJIS:
        return 'save'
    if emoji in ASK_REACTION_EMOJIS:
        return 'ask'
    return None


@client.event
async def on_raw_reaction_add(payload):
    # 古いメッセージにも反応できるよう raw イベントを使う。サーバー内の全チャンネルで有効
    if payload.user_id == client.user.id:
        return
    if payload.guild_id is None:
        return
    action = _reaction_action(str(payload.emoji))
    if action is None:
        return
    try:
        channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
        message = await channel.fetch_message(payload.message_id)
    except Exception as e:
        record_error('reaction', f'fetch_message failed: {e}')
        return
    reactor = payload.member.display_name if payload.member else '不明'
    if action == 'save':
        await handle_reaction_save(message, reactor)
    elif action == 'ask':
        await handle_reaction_ask(message, payload.user_id)


async def handle_reaction_save(message, reactor='不明'):
    """📚系リアクションが付いたメッセージ内のURLをScrapboxに保存する。"""
    urls = await asyncio.to_thread(expand_urls, re.findall(r'https?://[^\s<>"]+', message.content))
    if not urls:
        return
    results, embeds = await asyncio.to_thread(process_urls, urls, actor=f'リアクション保存:{reactor}')
    await message.reply(content='\n'.join(results) or None, embeds=embeds[:10])


async def handle_reaction_ask(message, reactor_id):
    """❓系リアクションが付いたメッセージの内容を質問として /ask 相当の回答を返す。"""
    if not rag_qa.OPENROUTER_API_KEY:
        return
    cleaned, _ = _clean_question(message.content)
    if not cleaned:
        return

    now = time.time()
    if ASK_COOLDOWN_SECONDS - (now - _ask_cooldowns.get(reactor_id, 0)) > 0:
        return
    _ask_cooldowns[reactor_id] = now

    async with message.channel.typing():
        answer, sources, error = await asyncio.to_thread(
            rag_qa.answer_question, cleaned, SCRAPBOX_PROJECT, SCRAPBOX_SID, RAG_TOP_N
        )
    if error:
        await message.reply(_format_ask_error(error, cleaned))
        return
    sent = await message.reply(embed=_build_answer_embed(answer, sources))
    await _start_ask_thread(sent, cleaned, answer)


def handle_diary_webhook_request(token, raw_body):
    """iOSショートカット等からのWebhook POSTを処理する（Discordを介さない日記追記経路）。
    DMでの追記（handle_diary_dm）と同じ diary.classify_entry / append_diary_entry を
    再利用するため、「単語:」プレフィックスの挙動も共通。
    戻り値: (http_status, response_dict)
    """
    if not (DIARY_SCRAPBOX_PROJECT and DIARY_SCRAPBOX_SID and DIARY_WEBHOOK_TOKEN):
        return 503, {'error': '日記Webhookは未設定です'}
    if not hmac.compare_digest(token or '', DIARY_WEBHOOK_TOKEN):
        return 401, {'error': 'トークンが不正です'}
    try:
        data = json.loads(raw_body or b'{}')
    except Exception:
        return 400, {'error': 'JSONの形式が不正です'}
    text = (data.get('text') or '').strip()
    if not text:
        return 400, {'error': 'text が空です'}

    section, body = diary.classify_entry(text)
    if section == 'diary':
        body = autolink_diary_text(body)
    status, title = diary.append_diary_entry(DIARY_SCRAPBOX_PROJECT, DIARY_SCRAPBOX_SID, body, section)
    if status == 'appended':
        return 200, {'status': 'appended', 'title': title, 'section': section}
    record_error('diary_webhook', f'{title} への追記失敗（ステータス:{status}）')
    return 502, {'error': f'Scrapboxへの書き込みに失敗しました（ステータス:{status}）'}


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        if self.path != '/diary':
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get('Content-Length') or 0)
        raw_body = self.rfile.read(length) if length else b''
        token = self.headers.get('X-Diary-Token', '')
        status, payload = handle_diary_webhook_request(token, raw_body)
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    # ThreadingHTTPServer: 日記Webhookの書き込み（Scrapboxへの数秒かかりうる通信）が、
    # UptimeRobot等からのヘルスチェックGETをブロックしないようにするため
    ThreadingHTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()


def main():
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise SystemExit(f'必須環境変数が未設定です: {", ".join(missing)}')
    threading.Thread(target=run_health_server, daemon=True).start()
    client.run(TOKEN)


if __name__ == '__main__':
    main()
