import json

import requests

# チャンネル⇔案件ページの対応を保存するScrapboxページ。
# Botはメモリ以外の永続ストレージを持たないため、再起動しても消えないようScrapboxに保存する。
LINKS_PAGE_TITLE = 'bot設定/チャンネル案件'


def parse_links(body_lines):
    """設定ページの本文行を {channel_id(int): page_title(str)} に変換する。不正行はスキップ。"""
    links = {}
    for line in body_lines:
        text = line.strip()
        if not text or '|' not in text:
            continue
        channel_part, _, title_part = text.partition('|')
        title = title_part.strip()
        try:
            channel_id = int(channel_part.strip())
        except ValueError:
            continue
        if title:
            links[channel_id] = title
    return links


def serialize_links(links):
    """{channel_id: page_title} を設定ページの全行（タイトル行含む）に変換する。"""
    lines = [LINKS_PAGE_TITLE]
    for channel_id in sorted(links):
        lines.append(f' {channel_id} | {links[channel_id]}')
    return lines


def load_links(project, sid):
    """設定ページを読み込んで dict を返す。ページが無ければ空dict、通信失敗は None。"""
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(LINKS_PAGE_TITLE)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return {}
    try:
        data = r.json()
    except Exception:
        return None
    if not data.get('persistent'):
        return {}
    lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
    return parse_links(lines[1:])


def save_links(project, sid, links):
    """設定ページを丸ごと書き換えて保存する。戻り値: (status_code, body)"""
    payload = json.dumps({'pages': [{'title': LINKS_PAGE_TITLE, 'lines': serialize_links(links)}]})
    try:
        r = requests.post(
            f'https://scrapbox.io/api/page-data/import/{project}.json',
            files={'import-file': ('pages.json', payload, 'application/json')},
            headers={
                'Cookie': f'connect.sid={sid}',
                'Origin': 'https://scrapbox.io',
                'Referer': 'https://scrapbox.io',
            },
            timeout=10,
        )
    except Exception as e:
        return None, str(e)
    return r.status_code, r.text[:300]
