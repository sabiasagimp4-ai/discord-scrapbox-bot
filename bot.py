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
import playlist_loader

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


def save_to_scrapbox(url, overwrite=False):
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
    return r.status_code, r.text[:300], title, embedded_thumbnail


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


def process_urls(urls, overwrite=False):
    """各URLを保存し、(エラーメッセージのリスト, Embedのリスト) を返す"""
    results = []
    embeds = []
    for url in urls:
        status, body, title, thumbnail = save_to_scrapbox(url, overwrite=overwrite)
        scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
        if status == 'duplicate':
            embeds.append(_build_result_embed(title, scrapbox_url, thumbnail, '既に保存済みです', discord.Color.blue()))
        elif status == 200:
            description = '上書き保存しました' if overwrite else '保存しました'
            embeds.append(_build_result_embed(title, scrapbox_url, thumbnail, description, discord.Color.green()))
        else:
            results.append(_format_error_reply(status, body))
    return results, embeds


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
@discord.app_commands.describe(url='保存したいURL', overwrite='既存ページがあっても上書き保存する')
async def save_command(interaction: discord.Interaction, url: str, overwrite: bool = False):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return

    await interaction.response.defer()
    urls = expand_urls([url])
    results, embeds = process_urls(urls, overwrite=overwrite)
    content = f'{interaction.user.display_name}\n{url}'

    if embeds:
        await interaction.followup.send(content=content, embeds=embeds[:10])
    if results:
        await interaction.followup.send('\n'.join(results))


@tree.command(name='status', description='Bot・Scrapbox・外部APIの疎通状況を確認します')
async def status_command(interaction: discord.Interaction):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return

    await interaction.response.defer()
    lines = [
        '✅ Discord: 接続中',
        _format_status_line('Scrapbox', name_linker.check_connection(SCRAPBOX_PROJECT, SCRAPBOX_SID)),
        _format_status_line('YouTube Data API', check_youtube_connection()),
        _format_status_line('OpenRouter(AI)', credit_extractor.check_connection()),
        _format_status_line('Gyazo', gyazo_uploader.check_connection()),
    ]
    await interaction.followup.send('\n'.join(lines))


alias_group = discord.app_commands.Group(name='alias', description='人物名の表記ゆれを管理します')
tree.add_command(alias_group)


def _check_alias_command_allowed(interaction, require_permission):
    if interaction.channel_id != CHANNEL_ID:
        return 'このチャンネルでは使えません'
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
    status, body = name_linker.add_alias(SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE, canonical, alias)

    if status == 200:
        global _alias_map_cache
        _alias_map_cache = None
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
    status, body = name_linker.remove_alias(SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE, canonical, alias)

    if status == 200:
        global _alias_map_cache
        _alias_map_cache = None
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
    lines = name_linker.list_aliases(SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE)
    if not lines:
        await interaction.followup.send('登録されている表記ゆれはありません')
        return
    text = '\n'.join(lines)
    if len(text) > 1900:
        text = text[:1900] + '\n...(省略)'
    await interaction.followup.send(text)


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

    urls = expand_urls(re.findall(r'https?://[^\s<>"]+', message.content))
    print(f'[urls] {urls}')
    if not urls:
        await message.reply('URLが見つかりませんでした')
        return
    results, embeds = process_urls(urls)
    await message.reply(content='\n'.join(results) or None, embeds=embeds[:10])


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
