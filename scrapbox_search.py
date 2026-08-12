import re

import requests

_URL_RE = re.compile(r'https?://\S+')
_BRACKET_RE = re.compile(r'\[([^\[\]]+)\]')
_DECORATION_PREFIX_RE = re.compile(r'^[\*/\-_]+\s+')
_HASHTAG_RE = re.compile(r'#(\S+)')
# 英数字・ひらがな・カタカナ・漢字のいずれかを含む行だけを「意味のある行」とみなす
_MEANINGFUL_RE = re.compile(r'[0-9A-Za-z぀-ヿ一-鿿]')


def _unwrap_brackets(text):
    """Scrapboxの角括弧記法を外して中身のテキストだけ残す。
    [山田太郎] -> 山田太郎 / [* 見出し] -> 見出し / [https://x ラベル] -> ラベル /
    [https://x] -> （URLのみは空に）/ [[太字]] -> 太字（入れ子は繰り返し処理）
    """
    def repl(m):
        inner = _DECORATION_PREFIX_RE.sub('', m.group(1))
        if _URL_RE.search(inner):
            return _URL_RE.sub('', inner).strip()
        return inner

    prev = None
    while text != prev:
        prev = text
        text = _BRACKET_RE.sub(repl, text)
    return text


def clean_page_lines(lines):
    """Scrapbox本文の行リストから、回答生成に不要なノイズを機械的に除去して結合する。

    - 空行 / コードブロック見出し(code:) / URL・画像のみの行を除去
    - リンク・装飾記法を外して中身のテキストだけ残す（[山田太郎] -> 山田太郎）
    - 裸のURLを除去、ハッシュタグは記号だけ外して語は残す（#映像 -> 映像）
    - 上記の結果、記号だけになった行を除去
    """
    out = []
    for raw in lines:
        s = (raw or '').strip()
        if not s or s.startswith('code:'):
            continue
        s = _unwrap_brackets(s)
        s = _URL_RE.sub('', s)
        s = _HASHTAG_RE.sub(r'\1', s)
        s = re.sub(r'\s+', ' ', s).strip()
        if s and _MEANINGFUL_RE.search(s):
            out.append(s)
    return '\n'.join(out)


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

    raw_lines = [
        line.get('text', '') if isinstance(line, dict) else str(line)
        for line in data.get('lines', [])
    ]
    # 1行目はページタイトル（build_rag_context側で見出しに付与済み）なので落とす
    body = clean_page_lines(raw_lines[1:])
    if len(body) > max_chars:
        body = body[:max_chars] + '…(省略)'
    return body


def fetch_page_lines(project, sid, title):
    """1ページの生の行をタイトルを含めて返す。取得失敗時はNone。"""
    try:
        r = requests.get(
            f'https://scrapbox.io/api/pages/{project}/{requests.utils.quote(title)}',
            headers={'Cookie': f'connect.sid={sid}'},
            timeout=10,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None

    return [
        line.get('text', '') if isinstance(line, dict) else str(line)
        for line in data.get('lines', [])
    ]


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
