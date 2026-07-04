import json
from datetime import datetime, timezone, timedelta

import requests

# Botの全書き込み操作を記録する監査ログページ。肥大化を防ぐため月ごとに分割する。
PAGE_PREFIX = 'bot設定/監査ログ'

JST = timezone(timedelta(hours=9))


def page_title_for(dt):
    """記録先ページ名を返す（月ごとにローテーション）"""
    return f'{PAGE_PREFIX}/{dt.strftime("%Y-%m")}'


def build_entry(dt, action, page, actor, detail=''):
    """監査ログ1行を組み立てる。改行・パイプは区切りを壊すため除去する。"""
    def clean(s):
        return str(s).replace('\n', ' ').replace('|', '/').strip()

    line = f' {dt.strftime("%Y-%m-%d %H:%M")} | {clean(action)} | {clean(page)} | 実行者:{clean(actor) or "不明"}'
    if detail:
        line += f' | {clean(detail)[:200]}'
    return line


def append_entry(project, sid, action, page, actor, detail='', now=None):
    """当月の監査ログページにエントリを1行追記する。戻り値: status_code（失敗時 None）"""
    now = now or datetime.now(JST)
    title = page_title_for(now)

    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return None

    body_lines = []
    if r.status_code == 200:
        try:
            data = r.json()
        except Exception:
            return None
        if data.get('persistent'):
            page_lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
            body_lines = page_lines[1:]  # 1行目はタイトル行

    body_lines.append(build_entry(now, action, page, actor, detail))

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
        return None
    return r2.status_code
