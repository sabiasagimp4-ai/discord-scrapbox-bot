import requests


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
