import json
from datetime import datetime, timezone, timedelta

import requests

import name_linker

JST = timezone(timedelta(hours=9))

# 日記ページの雛形（タイトル行は除く）。内容を変えたい場合はここを編集する。
DIARY_TEMPLATE = [
    '[* 今日のできごと]',
    '',
    '[* 学び・気づき]',
    '',
    '[* 感想]',
    '',
    '#日記',
]


def diary_title_for(dt):
    """日記ページのタイトル（YYYY-MM-DD形式）を返す"""
    return dt.strftime('%Y-%m-%d')


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

    lines = [title] + DIARY_TEMPLATE
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
