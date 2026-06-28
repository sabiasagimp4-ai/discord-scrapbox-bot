import discord
import re
import os
import requests
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
    try:
        r = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
        match = re.search(r'<title[^>]*>([^<]+)</title>', r.text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    except Exception:
        pass
    return urlparse(url).netloc


def save_to_scrapbox(url, context=''):
    title = fetch_title(url)
    lines = [title, url]
    if context:
        lines.append(f'> {context[:200]}')
    lines.append('from : Discord')

    payload = {'pages': [{'title': title, 'lines': lines}]}
    r = requests.post(
        f'https://scrapbox.io/api/page-data/import/{SCRAPBOX_PROJECT}',
        json=payload,
        headers={
            'Cookie': f'connect.sid={SCRAPBOX_SID}',
            'Content-Type': 'application/json',
        },
    )
    print(f'[scrapbox] {r.status_code} {title}')
    return r.status_code


@client.event
async def on_ready():
    print(f'Bot ready: {client.user}')


@client.event
async def on_message(message):
    if message.author.bot:
        return
    if message.channel.id != CHANNEL_ID:
        return
    if KEYWORD and KEYWORD not in message.content:
        return

    urls = re.findall(r'https?://[^\s<>"]+', message.content)
    for url in urls:
        save_to_scrapbox(url, message.content)


client.run(TOKEN)
