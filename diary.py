import json
from datetime import datetime, timezone, timedelta

import requests

import name_linker

JST = timezone(timedelta(hours=9))

VOCAB_HEADING = '【新しく知った単語】'
DIARY_HEADING = '【日記】'

# DM本文がこの接頭辞（半角/全角コロンどちらでも可）で始まる場合、
# 【日記】ではなく VOCAB_HEADING（見出しの直前）に挿入する。
VOCAB_TRIGGER_PREFIXES = ('単語:', '単語：')


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
        VOCAB_HEADING,
        '',
        DIARY_HEADING,
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


def build_entry_lines(text):
    """本文を追記用の行リストに変換する。1行目は見出し直下の通常インデント、
    複数行メッセージの2行目以降はさらにインデントする。"""
    body_lines = text.splitlines() or ['']
    first, *rest = body_lines
    lines = [f' {first}'.rstrip()]
    for line in rest:
        lines.append(f'  {line.rstrip()}')
    return lines


def classify_entry(text):
    """DM本文の宛先セクションを判定する。戻り値: ('vocab' | 'diary', 本文)
    先頭が「単語:」（全角コロンも可）で始まる場合は VOCAB_HEADING 欄、
    それ以外は DIARY_HEADING 欄とする。"""
    for prefix in VOCAB_TRIGGER_PREFIXES:
        if text.startswith(prefix):
            return 'vocab', text[len(prefix):].strip()
    return 'diary', text


def _insert_before_heading(body_lines, heading, new_lines):
    """body_lines内でheading行が最初に現れる位置の直前にnew_linesを挿入する。
    heading が見つからない場合（手動編集で見出しが消えた等）は末尾に追加する。"""
    for i, line in enumerate(body_lines):
        if line.strip() == heading:
            body_lines[i:i] = new_lines
            return
    body_lines.extend(new_lines)


def append_diary_entry(project, sid, text, section='diary', dt=None):
    """指定日（省略時は現在時刻・JST）の日記ページに本文を追記する。
    section='diary'（既定）は DIARY_HEADING 欄の末尾（ページ末尾）に追記し、
    section='vocab' は VOCAB_HEADING 欄の末尾（DIARY_HEADING 見出しの直前）に挿入する。
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

    if section == 'vocab':
        # 単語はScrapboxのリンク記法で囲み、クリックで単語ごとのページを開けるようにする
        entry_lines = build_entry_lines(f'[{text}]')
        _insert_before_heading(body_lines, DIARY_HEADING, entry_lines)
    else:
        entry_lines = build_entry_lines(text)
        # Scrapbox取得結果の末尾に空行が残っていることがあり、そのまま追記すると
        # 追記のたびに空行が増えていくため、末尾の空行を詰めてから追記する
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        body_lines.extend(entry_lines)

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
