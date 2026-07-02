import requests


def search_pages(project, sid, query, limit=10):
    """Scrapbox全文検索。
    戻り値: (pages, error)
      error: None=成功(0件含む) / 'auth'=403(SID失効) / それ以外=エラー文言
      pages要素: {'title': str, 'snippet': str}
    """
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/search/query',
            params={'q': query},
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception as e:
        return [], str(e)
    if r.status_code == 403:
        return [], 'auth'
    if r.status_code != 200:
        return [], f'ステータス({r.status_code})'
    try:
        data = r.json()
    except Exception:
        return [], 'JSON不正'

    results = []
    for p in data.get('pages', [])[:limit]:
        lines = p.get('lines', [])
        if isinstance(lines, list):
            snippet = ' '.join(str(x) for x in lines)
        else:
            snippet = str(lines)
        results.append({'title': p.get('title', ''), 'snippet': snippet})
    return results, None


def fetch_page_text(project, sid, title, max_chars=1000):
    """1ページの本文テキスト（lines[].text 結合）を先頭 max_chars で返す。失敗時は ''"""
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return ''
    if r.status_code != 200:
        return ''
    try:
        data = r.json()
    except Exception:
        return ''

    lines = []
    for line in data.get('lines', []):
        lines.append(line.get('text', '') if isinstance(line, dict) else str(line))
    body = '\n'.join(lines)
    if len(body) > max_chars:
        body = body[:max_chars] + '…(省略)'
    return body


def merge_search_results(results_per_query):
    """複数クエリの検索結果をマージして関連度順に並べる。
    入力: [[{title, snippet}, ...], ...]（クエリごとのヒットリスト）
    出力: [{'title', 'snippet', 'score'}, ...]
      score = そのページがヒットしたクエリ数（多いほど関連が強い）
      スコア降順。同点は最初に出現した順を保つ（安定ソート）。
    """
    order = []
    merged = {}
    for results in results_per_query:
        for item in results:
            title = item.get('title', '')
            snippet = item.get('snippet', '')
            if title not in merged:
                merged[title] = {'title': title, 'snippet': snippet, 'score': 0}
                order.append(title)
            merged[title]['score'] += 1
            # 同じページでもクエリによってスニペットが違うので、より情報量の多いものを残す
            if len(snippet) > len(merged[title]['snippet']):
                merged[title]['snippet'] = snippet
    ranked = sorted(order, key=lambda t: -merged[t]['score'])
    return [merged[t] for t in ranked]
