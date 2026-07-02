import asyncio
import discord
import json
import random
import re
import os
import threading
import time
import requests
from datetime import time as dt_time, timezone, timedelta
from discord.ext import tasks
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import credit_extractor
import gyazo_uploader
import name_linker
import playlist_loader
import rag_qa
import scrapbox_search

REQUIRED_ENV_VARS = ('DISCORD_TOKEN', 'CHANNEL_ID', 'SCRAPBOX_PROJECT', 'SCRAPBOX_SID')

TOKEN = os.environ.get('DISCORD_TOKEN', '')
CHANNEL_ID = int(os.environ.get('CHANNEL_ID') or '0')
KEYWORD = os.environ.get('KEYWORD', '')
SCRAPBOX_PROJECT = os.environ.get('SCRAPBOX_PROJECT', '')
SCRAPBOX_SID = os.environ.get('SCRAPBOX_SID', '')
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', '')
CREDIT_MAPPING_PAGE = os.environ.get('CREDIT_MAPPING_PAGE', '')
GUILD_ID = os.environ.get('GUILD_ID', '')

PAGES_CACHE_TTL = 300
_pages_cache = {'pages': [], 'ts': 0.0}
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

JST = timezone(timedelta(hours=9))

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
    return r.status_code, r.text[:300], title, embedded_thumbnail


def write_page_to_scrapbox(title, body_text):
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
    return r.status_code, r.text[:300]


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
    except Exception as e:
        print(f'[send_daily_random_article] error: {e}')


def find_new_titles(known_titles, current_titles):
    """既知のタイトル集合と現在の全タイトルを比較し、新規に増えたタイトルのリストを返す"""
    return [title for title in current_titles if title not in known_titles]


@tasks.loop(minutes=5)
async def notify_new_pages():
    global _known_page_titles
    try:
        current_titles = await asyncio.to_thread(get_existing_pages)
        if _known_page_titles is None:
            _known_page_titles = set(current_titles)
            return

        new_titles = find_new_titles(_known_page_titles, current_titles)
        _known_page_titles = set(current_titles)
        if not new_titles:
            return

        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        for title in new_titles:
            if title in _recently_saved_titles:
                _recently_saved_titles.discard(title)
                continue
            if title == CREDIT_MAPPING_PAGE:
                continue
            scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
            embed = _build_result_embed(title, scrapbox_url, '', 'Scrapboxに新しいページが投稿されました', discord.Color.gold())
            await channel.send(embed=embed)
    except Exception as e:
        print(f'[notify_new_pages] error: {e}')


def run_daily_health_checks():
    """必須・任意の外部サービス接続を確認する。戻り値: 異常があった項目のメッセージのリスト（正常時は空リスト）"""
    checks = [
        ('Scrapbox', name_linker.check_connection, (SCRAPBOX_PROJECT, SCRAPBOX_SID)),
        ('YouTube Data API', check_youtube_connection, ()),
        ('OpenRouter(AI)', credit_extractor.check_connection, ()),
        ('Gyazo', gyazo_uploader.check_connection, ()),
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
            return
        channel = client.get_channel(CHANNEL_ID) or await client.fetch_channel(CHANNEL_ID)
        await channel.send('⚠️ 日次ヘルスチェックで異常を検出しました\n' + '\n'.join(problems))
    except Exception as e:
        print(f'[daily_health_check] error: {e}')


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


@tree.command(name='save', description='URLをScrapboxに保存します')
@discord.app_commands.describe(url='保存したいURL', overwrite='既存ページがあっても上書き保存する')
async def save_command(interaction: discord.Interaction, url: str, overwrite: bool = False):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return

    await interaction.response.defer()
    urls = await asyncio.to_thread(expand_urls, [url])
    results, embeds = await asyncio.to_thread(process_urls, urls, overwrite=overwrite)
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
        _format_status_line('Scrapbox', await asyncio.to_thread(name_linker.check_connection, SCRAPBOX_PROJECT, SCRAPBOX_SID)),
        _format_status_line('YouTube Data API', await asyncio.to_thread(check_youtube_connection)),
        _format_status_line('OpenRouter(AI)', await asyncio.to_thread(credit_extractor.check_connection)),
        _format_status_line('Gyazo', await asyncio.to_thread(gyazo_uploader.check_connection)),
    ]
    await interaction.followup.send('\n'.join(lines))


@tree.command(name='debug', description='URLのメタデータ取得結果（概要欄・クレジット抽出結果など）を確認します')
@discord.app_commands.describe(url='確認したいURL')
async def debug_command(interaction: discord.Interaction, url: str):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return

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
        title = self.page_title.value.strip()
        body_text = self.body.value or ''
        try:
            status, body = await asyncio.to_thread(write_page_to_scrapbox, title, body_text)
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
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return
    await interaction.response.send_modal(WriteModal())


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
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return
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
        print(f'[ask thread] create failed: {e}')
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


@tree.command(name='search', description='Scrapboxをキーワード検索します')
@discord.app_commands.describe(query='検索キーワード')
async def search_command(interaction: discord.Interaction, query: str):
    if interaction.channel_id != CHANNEL_ID:
        await interaction.response.send_message('このチャンネルでは使えません', ephemeral=True)
        return
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
    status, body = await asyncio.to_thread(name_linker.add_alias, SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE, canonical, alias)

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
    status, body = await asyncio.to_thread(name_linker.remove_alias, SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE, canonical, alias)

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
    lines = await asyncio.to_thread(name_linker.list_aliases, SCRAPBOX_PROJECT, SCRAPBOX_SID, CREDIT_MAPPING_PAGE)
    if not lines:
        await interaction.followup.send('登録されている表記ゆれはありません')
        return
    text = '\n'.join(lines)
    if len(text) > 1900:
        text = text[:1900] + '\n...(省略)'
    await interaction.followup.send(text)


@client.event
async def on_message(message):
    if message.author.bot:
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
    results, embeds = await asyncio.to_thread(process_urls, urls)
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


def main():
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise SystemExit(f'必須環境変数が未設定です: {", ".join(missing)}')
    threading.Thread(target=run_health_server, daemon=True).start()
    client.run(TOKEN)


if __name__ == '__main__':
    main()
