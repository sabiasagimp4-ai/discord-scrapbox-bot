import discord
import json
import re
import os
import threading
import time
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import credit_extractor
import gyazo_uploader
import name_linker

TOKEN = os.environ['DISCORD_TOKEN']
CHANNEL_ID = int(os.environ['CHANNEL_ID'])
KEYWORD = os.environ.get('KEYWORD', '')
SCRAPBOX_PROJECT = os.environ['SCRAPBOX_PROJECT']
SCRAPBOX_SID = os.environ['SCRAPBOX_SID']
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
CREDIT_MAPPING_PAGE = os.environ.get('CREDIT_MAPPING_PAGE', '')
GUILD_ID = os.environ.get('GUILD_ID', '')

PAGES_CACHE_TTL = 300
_pages_cache = {'pages': [], 'ts': 0.0}
_alias_map_cache = None

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)


def _normalize_title(title):
    """Scrapboxのタイトルは改行を含められないため、空白類を1スペースに畳む"""
    return re.sub(r'\s+', ' ', title).strip()


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
    """戻り値: {'title': str, 'description': str, 'thumbnail': str}"""
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
                return {'title': _normalize_title(r.json().get('title', '')), 'description': '', 'thumbnail': ''}
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
                }
        except Exception:
            pass

    # 汎用HTMLタイトル取得（og:imageもサムネイルとして取得）
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        match = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'https?://\S+', '', title).strip()
            title = _normalize_title(title)
            if title:
                return {'title': title, 'description': '', 'thumbnail': _extract_og_image(r.text)}
    except Exception:
        pass

    return {'title': urlparse(url).netloc, 'description': '', 'thumbnail': ''}


def get_existing_pages():
    now = time.time()
    if now - _pages_cache['ts'] > PAGES_CACHE_TTL:
        _pages_cache['pages'] = name_linker.load_existing_pages(SCRAPBOX_PROJECT, SCRAPBOX_SID)
        _pages_cache['ts'] = now
    return _pages_cache['pages']


def get_alias_map():
    global _alias_map_cache
    if _alias_map_cache is None:
        _alias_map_cache = name_linker.load_alias_map(SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE)
    return _alias_map_cache


def save_to_scrapbox(url):
    metadata = fetch_metadata(url)
    title = metadata['title']

    if name_linker.check_page_exists(SCRAPBOX_PROJECT, SCRAPBOX_SID, title):
        return 'duplicate', None, title

    lines = [title, f'[{url}]']

    thumbnail = metadata.get('thumbnail')
    if thumbnail:
        # 直リンクはホットリンク制限で表示できないサイトがあるため、Gyazoにアップロードして恒久URL化する
        lines.append(f'[{gyazo_uploader.upload_thumbnail(thumbnail) or thumbnail}]')

    credits = credit_extractor.extract_credits(metadata['description'])
    if credits:
        pages = get_existing_pages()
        alias_map = get_alias_map()
        lines.append('クレジット')
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
    )
    return r.status_code, r.text[:300], title


def _format_error_reply(status, body):
    if status == 403:
        return '❌ Scrapboxの認証エラー(403): Cookie(SCRAPBOX_SID)が期限切れの可能性があります。管理者に再取得を依頼してください。'
    return f'❌ エラー({status}): {body}'


@client.event
async def on_ready():
    print(f'Bot ready: {client.user}')
    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()


@tree.command(name='save', description='URLをScrapboxに保存します')
@discord.app_commands.describe(url='保存したいURL')
async def save_command(interaction: discord.Interaction, url: str):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return

    await interaction.response.defer()
    status, body, title = save_to_scrapbox(url)
    scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'

    if status == 'duplicate':
        await interaction.followup.send(f'{interaction.user.display_name}\n{url}\n既に保存済みです {scrapbox_url}')
    elif status == 200:
        await interaction.followup.send(f'{interaction.user.display_name}\n{url}\n{scrapbox_url}')
    else:
        await interaction.followup.send(_format_error_reply(status, body))


@client.event
async def on_message(message):
    print(f'[msg] ch={message.channel.id} author={message.author} content={message.content[:50]!r}')
    if message.author.bot:
        return
    if message.channel.id != CHANNEL_ID:
        print(f'[skip] channel mismatch: {message.channel.id} != {CHANNEL_ID}')
        return
    if KEYWORD and KEYWORD not in message.content:
        print(f'[skip] keyword not found: {KEYWORD!r}')
        return

    urls = re.findall(r'https?://[^\s<>"]+', message.content)
    print(f'[urls] {urls}')
    if not urls:
        await message.reply('URLが見つかりませんでした')
        return
    for url in urls:
        status, body, title = save_to_scrapbox(url)
        scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
        if status == 'duplicate':
            await message.reply(f'既に保存済みです {scrapbox_url}')
        elif status == 200:
            await message.reply(f'保存しました {scrapbox_url}')
        else:
            await message.reply(_format_error_reply(status, body))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()


threading.Thread(target=run_health_server, daemon=True).start()
client.run(TOKEN)
