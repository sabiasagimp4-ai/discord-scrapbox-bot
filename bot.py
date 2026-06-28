import discord
import json
import re
import os
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

TOKEN = os.environ['DISCORD_TOKEN']
CHANNEL_ID = int(os.environ['CHANNEL_ID'])
KEYWORD = os.environ.get('KEYWORD', '')
SCRAPBOX_PROJECT = os.environ['SCRAPBOX_PROJECT']
SCRAPBOX_SID = os.environ['SCRAPBOX_SID']

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


def fetch_title(url):
    # YouTube oEmbed API (no auth needed)
    yt_match = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    if yt_match:
        try:
            r = requests.get(
                f'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={yt_match.group(1)}&format=json',
                timeout=5,
            )
            if r.status_code == 200:
                return r.json().get('title', '')
        except Exception:
            pass

    # Vimeo oEmbed API
    if 'vimeo.com' in url:
        try:
            r = requests.get(
                f'https://vimeo.com/api/oembed.json?url={url}',
                timeout=5,
            )
            if r.status_code == 200:
                return r.json().get('title', '')
        except Exception:
            pass

    # 汎用HTMLタイトル取得
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        match = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            title = re.sub(r'https?://\S+', '', title).strip()
            if title:
                return title
    except Exception:
        pass

    return urlparse(url).netloc


def save_to_scrapbox(url):
    title = fetch_title(url)
    lines = [title, f'[{url}]']

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


@client.event
async def on_ready():
    print(f'Bot ready: {client.user}')


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
        if status == 200:
            scrapbox_url = f'https://scrapbox.io/{SCRAPBOX_PROJECT}/{requests.utils.quote(title)}'
            await message.reply(f'保存しました {scrapbox_url}')
        else:
            await message.reply(f'❌ エラー({status}): {body}')


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
