import json
from datetime import datetime, timezone, timedelta

import requests

import name_linker

JST = timezone(timedelta(hours=9))

def diary_title_for(dt):
    """日記ページのタイトル（YYYY-MM-DD形式）を返す"""
    return dt.strftime('%Y-%m-%d')


def build_template(dt):
    """日記ページの雛形（タイトル行は除く）を組み立てる。
    前日・翌日ページへのナビゲーションリンクを先頭に含む。月末・年末・うるう年の
    境界は timedelta による日付演算に任せることで自前の分岐なしに正しく処理する。
    内容を変えたい場合はここを編集する。"""
    prev_day = diary_title_for(dt - timedelta(days=1))
    today = diary_title_for(dt)
    next_day = diary_title_for(dt + timedelta(days=1))
    return [
        f'<- [{prev_day}] / [{today}] / [{next_day}] ->',
        '',
        '',
        '【新しく知った単語】',
        '',
        '【日記】',
    ]


def create_diary_page(project, sid, dt=None):
    """指定日（省略時は現在時刻・JST）の日記ページを雛形付きで作成する。
    既に存在する場合は上書きせずスキップする（同日中の再実行での重複作成を防ぐ）。
    戻り値: (status, title)
      status: 'created' / 'exists' / int（HTTPステータス、失敗時） / None（通信失敗）
    """
    dt = dt or datetime.now(JST)
    title = diary_title_for(dt)

    if name_linker.check_page_exists(project, sid, title):
        return 'exists', title

    lines = [title] + build_template(dt)
    payload = json.dumps({'pages': [{'title': title, 'lines': lines}]})
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
    except Exception:
        return None, title
    if r.status_code == 200:
        return 'created', title
    return r.status_code, title


def build_diary_entry_lines(text, time_str):
    """DM本文を日記エントリの行に変換する。1行目には時刻を先頭に付け、
    複数行メッセージの2行目以降はインデントする。"""
    body_lines = text.splitlines() or ['']
    first, *rest = body_lines
    lines = [f' {time_str} {first}'.rstrip()]
    for line in rest:
        lines.append(f'  {line.rstrip()}')
    return lines


def append_diary_entry(project, sid, text, dt=None):
    """指定日（省略時は現在時刻・JST）の日記ページ末尾にDM本文を追記する。
    ページが無ければ雛形付きで新規作成してから追記する（ページ名は日付から
    決定的に導かれるため、/note と違いタイポによる誤作成のリスクが無い）。
    戻り値: (status, title)
      status: 'appended' / int（HTTPステータス、失敗時） / None（通信失敗）
    """
    dt = dt or datetime.now(JST)
    title = diary_title_for(dt)

    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return None, title

    body_lines = []
    if r.status_code == 200:
        try:
            data = r.json()
        except Exception:
            return None, title
        if data.get('persistent'):
            page_lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
            body_lines = page_lines[1:]
    if not body_lines:
        body_lines = build_template(dt)

    body_lines.extend(build_diary_entry_lines(text, dt.strftime('%H:%M')))

    payload = json.dumps({'pages': [{'title': title, 'lines': [title] + body_lines}]})
    try:
        r2 = requests.post(
            f'https://scrapbox.io/api/page-data/import/{project}.json',
            files={'import-file': ('pages.json', payload, 'application/json')},
            headers={
                'Cookie': f'connect.sid={sid}',
                'Origin': 'https://scrapbox.io',
                'Referer': 'https://scrapbox.io',
            },
            timeout=10,
        )
    except Exception:
        return None, title
    if r2.status_code == 200:
        return 'appended', title
    return r2.status_code, title
