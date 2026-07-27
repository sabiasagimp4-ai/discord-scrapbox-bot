import json
import re

import requests

# 既にScrapboxの記法になっている部分。この中は自動リンク化の対象から外す
# （[]の二重付与や、URL・タグの途中で切ってリンクを壊すのを防ぐ）。
_PROTECTED_RE = re.compile(r'\[[^\[\]]*\]|https?://\S+|#\S+')


def load_existing_pages(project, sid):
    pages = []
    skip = 0
    while True:
        try:
            r = requests.get(
                f'https://scrapbox.io/api/pages/{project}',
                params={'limit': 1000, 'skip': skip},
                headers={'Cookie': f'connect.sid={sid}'},
                timeout=10,
            )
        except Exception:
            break
        if r.status_code != 200:
            break
        batch = r.json().get('pages', [])
        pages.extend(p['title'] for p in batch)
        if len(batch) < 1000:
            break
        skip += 1000
    return pages


def load_alias_map(project, sid, mapping_page_title):
    """`本名 == 別名1, 別名2` 形式の行をパースして {別名: 本名} の辞書を返す"""
    if not mapping_page_title:
        return {}
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(mapping_page_title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return {}
    if r.status_code != 200:
        return {}

    alias_map = {}
    for line in r.json().get('lines', []):
        text = line.get('text', '') if isinstance(line, dict) else line
        if '==' not in text:
            continue
        canonical, _, aliases = text.partition('==')
        canonical = canonical.strip()
        for alias in aliases.split(','):
            alias = alias.strip()
            if alias:
                alias_map[_normalize(alias)] = canonical
    return alias_map


def check_page_exists(project, sid, title):
    # Scrapboxはリンクのみ存在する未作成ページでもステータス200を返すため、
    # 実際に保存済みかどうかは persistent フィールドで判定する必要がある。
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return False
    if r.status_code != 200:
        return False
    try:
        return bool(r.json().get('persistent', False))
    except Exception:
        return False


def add_alias(project, sid, mapping_page_title, canonical, alias):
    """表記ゆれページに `canonical == alias` を追記する。
    既存のcanonical行があれば別名を追加し、無ければ新規行を作る（既存行は削除しない）。
    戻り値: (status_code, message)
    """
    canonical = canonical.strip()
    alias = alias.strip()

    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(mapping_page_title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception as e:
        return None, str(e)

    body_lines = []
    if r.status_code == 200:
        data = r.json()
        if data.get('persistent'):
            page_lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
            body_lines = page_lines[1:]  # 1行目はタイトル行

    updated = False
    for i, text in enumerate(body_lines):
        line_canonical, sep, aliases_part = text.partition('==')
        if not sep or line_canonical.strip().lower() != canonical.lower():
            continue
        existing_aliases = [a.strip() for a in aliases_part.split(',') if a.strip()]
        if alias.lower() in (a.lower() for a in existing_aliases):
            return 200, '既に登録済みです'
        existing_aliases.append(alias)
        body_lines[i] = f'{line_canonical.strip()} == {", ".join(existing_aliases)}'
        updated = True
        break

    if not updated:
        body_lines.append(f'{canonical} == {alias}')

    payload = json.dumps({'pages': [{'title': mapping_page_title, 'lines': [mapping_page_title] + body_lines}]})
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
    except Exception as e:
        return None, str(e)
    return r2.status_code, r2.text[:300]


def list_aliases(project, sid, mapping_page_title):
    """表記ゆれページの本文行（タイトル行を除く）をそのままリストで返す"""
    if not mapping_page_title:
        return []
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(mapping_page_title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return []
    if r.status_code != 200:
        return []
    data = r.json()
    if not data.get('persistent'):
        return []
    lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
    return [line for line in lines[1:] if line.strip()]


def remove_alias(project, sid, mapping_page_title, canonical, alias):
    """表記ゆれページから指定の別名を削除する。別名が無くなった行は削除する。
    戻り値: (status_code, message)
    """
    canonical = canonical.strip()
    alias = alias.strip()

    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(mapping_page_title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception as e:
        return None, str(e)

    body_lines = []
    if r.status_code == 200:
        data = r.json()
        if data.get('persistent'):
            page_lines = [line.get('text', '') if isinstance(line, dict) else line for line in data.get('lines', [])]
            body_lines = page_lines[1:]  # 1行目はタイトル行

    for i, text in enumerate(body_lines):
        line_canonical, sep, aliases_part = text.partition('==')
        if not sep or line_canonical.strip().lower() != canonical.lower():
            continue
        existing_aliases = [a.strip() for a in aliases_part.split(',') if a.strip()]
        remaining = [a for a in existing_aliases if a.lower() != alias.lower()]
        if len(remaining) == len(existing_aliases):
            return 404, '登録されていない別名です'
        if remaining:
            body_lines[i] = f'{line_canonical.strip()} == {", ".join(remaining)}'
        else:
            body_lines.pop(i)
        break
    else:
        return 404, '登録されていない本名です'

    payload = json.dumps({'pages': [{'title': mapping_page_title, 'lines': [mapping_page_title] + body_lines}]})
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
    except Exception as e:
        return None, str(e)
    return r2.status_code, r2.text[:300]


def check_connection(project, sid):
    """Scrapboxへの接続性とCookieの有効性を確認する。戻り値: (ok, message)"""
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}',
            params={'limit': 1},
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception as e:
        return False, str(e)
    if r.status_code == 200:
        return True, '接続OK'
    if r.status_code == 403:
        return False, 'Cookie(SCRAPBOX_SID)が期限切れの可能性があります'
    return False, f'ステータス({r.status_code})'


def resolve_name(name, pages, alias_map):
    """名前を既存ページと照合し、一致すれば Scrapbox リンク記法 [名前] に変換する"""
    normalized = _normalize(name)

    for page in pages:
        if _normalize(page) == normalized:
            return f'[{page}]'

    canonical = alias_map.get(normalized)
    if canonical:
        for page in pages:
            if _normalize(page) == _normalize(canonical):
                return f'[{page}]'
        return f'[{canonical}]'

    best_page, best_score = None, 0.0
    for page in pages:
        score = _dice_coefficient(page, name)
        if score > best_score:
            best_page, best_score = page, score
    if best_score >= 0.9:
        return f'[{best_page}]'

    return name


def _lower(s):
    """照合用の小文字化。一部の文字（İ など）は小文字化すると文字数が変わり、
    元の文字列と位置がずれてリンクを挿入する場所を誤る。そういう文字列は
    小文字化せず、大文字小文字を区別して照合する（誤リンクより取りこぼしを選ぶ）。"""
    lowered = s.lower()
    return lowered if len(lowered) == len(s) else s


def _link_segment(segment, candidates):
    """記法で保護されていない範囲について、既存ページ名に一致する部分を [ ] で囲む。
    candidates は (ページ名, 照合用に小文字化したページ名) を長い順に並べたもので、
    先に見つかった＝最も長い一致を採用する（「Blender」と「Blender Guru」なら後者）。"""
    haystack = segment.lower()
    # 小文字化で長さが変わる場合は本文を元の表記のまま照合する（ページ名側も元の表記を使う）
    case_insensitive = len(haystack) == len(segment)
    if not case_insensitive:
        haystack = segment
    out = []
    i = 0
    while i < len(segment):
        for title, title_lower in candidates:
            if haystack.startswith(title_lower if case_insensitive else title, i):
                out.append(f'[{title}]')
                i += len(title)
                break
        else:
            out.append(segment[i])
            i += 1
    return ''.join(out)


def link_known_pages(text, pages, min_length=2):
    """本文中に出てくる既存ページ名をScrapboxのリンク記法 [ページ名] に変換する。
    大文字小文字は無視して照合し、置き換えにはページ側の表記を使う（「scrapbox」と
    書いても [Scrapbox] ページに繋がるようにするため）。
    min_length未満の短いページ名は、無関係な文字列に当たって誤リンクを量産するので対象外。"""
    candidates = sorted(
        {p.strip() for p in pages if len(p.strip()) >= min_length},
        key=len,
        reverse=True,
    )
    if not candidates:
        return text
    candidates = [(title, _lower(title)) for title in candidates]

    out = []
    pos = 0
    for m in _PROTECTED_RE.finditer(text):
        out.append(_link_segment(text[pos:m.start()], candidates))
        out.append(m.group())
        pos = m.end()
    out.append(_link_segment(text[pos:], candidates))
    return ''.join(out)


def _normalize(s):
    return s.strip().lower()


def _bigrams(s):
    s = _normalize(s)
    if len(s) <= 1:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _dice_coefficient(a, b):
    bigrams_a = _bigrams(a)
    bigrams_b = _bigrams(b)
    if not bigrams_a or not bigrams_b:
        return 0.0
    overlap = len(bigrams_a & bigrams_b)
    return 2 * overlap / (len(bigrams_a) + len(bigrams_b))
