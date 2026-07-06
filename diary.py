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
