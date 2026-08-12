# Scrapbox動画のEagle一括取り込み Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scrapboxプロジェクトの全ページから動画URLを収集し、Windows上のBridge経由でダウンロードしてEagleへ登録できるDiscord機能を追加する。

**Architecture:** Render上のBotはScrapboxのページ走査、URL抽出、確認付きジョブ作成、認証済みジョブAPI、Discord DM通知を担当する。Windows上の`eagle_bridge.py`はBotをHTTPSでポーリングし、yt-dlp/ffmpegで動画をダウンロードし、Eagleのlocalhost Web APIへファイルを登録する。クラウド側ジョブは既存アプリのプロセス内状態、Bridge側の成功マニフェストはローカルJSONとして保持する。

**Tech Stack:** Python 3、discord.py、requests、yt-dlp、Eagle Web API、既存の`unittest discover -s tests`。

## Global Constraints

- YouTube Data API、Cookie、ログインセッションは使用しない。
- Eagleのローカルポートを外部公開しない。BridgeからBotへの外向きHTTPS接続だけを使用する。
- `/eagle import-all` はプレビューのみ、`confirm:true` が指定されるまでダウンロードを開始しない。
- Bot側のジョブはプロセス内状態であり、Render再起動時の未完了ジョブは再実行で復旧する。
- Bridgeの成功マニフェストを正規化URL単位の重複排除に使う。
- 動画取得やEagle登録の個別失敗は記録し、他のジョブを継続する。
- 同時ダウンロード数は初期値1、リトライ回数とタイムアウトを設定可能にする。
- すべての新規関数は、実装前に対応する失敗テストを作成して確認する。

---

## ファイル構成

### Create

- `eagle_import.py`: URL候補抽出、URL正規化、ページ出典情報、Bot側のジョブ状態管理。
- `eagle_bridge.py`: Botジョブのポーリング、yt-dlpダウンロード、Eagle API登録、ローカル成功マニフェスト。
- `tests/test_eagle_import.py`: 抽出・正規化・ジョブ状態のユニットテスト。
- `tests/test_eagle_bridge.py`: BridgeのHTTP、ダウンロード、Eagle登録、再実行のユニットテスト。

### Modify

- `scrapbox_search.py`: URLを削除しない生のページ本文取得関数を追加する。
- `bot.py`: `/eagle`コマンド、ジョブAPI、Bridge結果処理、DM進捗通知、環境変数を追加する。
- `tests/test_scrapbox_search.py`: 生本文取得のテストを追加する。
- `tests/test_bot.py`: コマンド認証、プレビュー、確認、API認証、結果処理のテストを追加する。
- `requirements.txt`: Bridgeを同じPython環境で実行するための依存関係を確認・必要最小限に調整する。
- `README.md`: Eagle、ffmpeg、Bridge、環境変数、Discordコマンド、失敗条件を追記する。

---

### Task 1: URL抽出・正規化・ページ出典モデル

**Files:**

- Create: `tests/test_eagle_import.py`
- Create: `eagle_import.py`

**Interfaces:**

- Produces `extract_video_urls(text) -> list[str]`。
- Produces `canonicalize_video_url(url) -> str | None`。
- Produces `SourceOccurrence(source_url, page_title, page_url, source_line)`。
- Produces `VideoSource(canonical_url, sources: list[SourceOccurrence])`。`sources[0]`を代表出典として使う。
- Produces `collect_video_sources(pages) -> list[VideoSource]`。`pages`は`{"title": str, "url": str, "lines": list[str]}`辞書の反復可能オブジェクトとする。

- [ ] **Step 1: URL抽出の失敗テストを書く**

```python
from eagle_import import extract_video_urls


def test_extract_video_urls_finds_plain_markdown_and_scrapbox_urls():
    text = "動画 https://youtu.be/abc123。 [https://vimeo.com/42 clip]"

    assert extract_video_urls(text) == [
        "https://youtu.be/abc123",
        "https://vimeo.com/42",
    ]
```

- [ ] **Step 2: テストを実行して、未定義関数で失敗することを確認する**

実行: `python -m unittest tests.test_eagle_import.VideoUrlExtractionTests.test_extract_video_urls_finds_plain_markdown_and_scrapbox_urls -v`

期待結果: `ImportError` または `AttributeError`。既存コードが偶然通していないことを確認する。

- [ ] **Step 3: URL抽出を最小実装する**

`http://`または`https://`から始まるURLを正規表現で抽出し、末尾の`。、．,.;:!?)]}`を除去する。抽出順は本文中の出現順を維持し、URLのドメインによる絞り込みはこの関数では行わない。

- [ ] **Step 4: 抽出テストを実行して通過させる**

実行: `python -m unittest tests.test_eagle_import.VideoUrlExtractionTests -v`

期待結果: PASS。

- [ ] **Step 5: URL正規化の失敗テストを書く**

```python
from eagle_import import canonicalize_video_url


def test_canonicalize_video_url_removes_tracking_fragment_only():
    assert canonicalize_video_url(
        "https://www.youtube.com/watch?v=abc123&si=tracking#live"
    ) == "https://www.youtube.com/watch?v=abc123"


def test_canonicalize_video_url_rejects_non_http_url():
    assert canonicalize_video_url("not-a-url") is None
```

- [ ] **Step 6: 正規化テストを実行して失敗を確認する**

実行: `python -m unittest tests.test_eagle_import.VideoUrlCanonicalizationTests -v`

期待結果: `ImportError` または期待値不一致。

- [ ] **Step 7: URL正規化を最小実装する**

`urllib.parse`でスキームとホストを検証し、ホストを小文字化する。YouTubeの`si`、`feature`、`app`など動画識別に不要な既知パラメータとfragmentだけを削除し、`v`など意味のあるパラメータは残す。未知のパラメータを一律削除しない。

- [ ] **Step 8: 正規化テストを実行して通過させる**

実行: `python -m unittest tests.test_eagle_import.VideoUrlCanonicalizationTests -v`

期待結果: PASS。

- [ ] **Step 9: ページ出典の重複排除テストを書く**

```python
from eagle_import import collect_video_sources


def test_collect_video_sources_deduplicates_url_and_keeps_all_sources():
    pages = [
        {"title": "A", "url": "https://scrapbox.io/p/A", "lines": ["https://youtu.be/x"]},
        {"title": "B", "url": "https://scrapbox.io/p/B", "lines": ["https://www.youtube.com/watch?v=x"]},
    ]

    result = collect_video_sources(pages)

    assert len(result) == 1
    assert result[0].canonical_url == "https://youtu.be/x"
    assert [source.page_title for source in result[0].sources] == ["A", "B"]
```

- [ ] **Step 10: 出典モデルと収集処理を実装する**

`VideoSource`は正規化URL、代表元URL、ページタイトル、ページURL、元行、全出典のリストを保持する。正規化URLを辞書キーにして出現順を保つ。URLが正規化できないものは除外する。

- [ ] **Step 11: Task 1のテストを実行する**

実行: `python -m unittest tests.test_eagle_import -v`

期待結果: PASS。

- [ ] **Step 12: Task 1をコミットする**

```bash
git add eagle_import.py tests/test_eagle_import.py
git commit -m "feat: add Scrapbox video URL collection helpers"
```

### Task 2: Scrapbox生本文取得

**Files:**

- Modify: `scrapbox_search.py`
- Modify: `tests/test_scrapbox_search.py`

**Interfaces:**

- Produces `fetch_page_lines(project, sid, title) -> list[str] | None`。
- `fetch_page_lines`はタイトル行を含むAPIレスポンスの`lines[].text`を順序通り返し、失敗時は`None`を返す。
- 既存の`fetch_page_text`のURL除去・要約動作は変更しない。

- [ ] **Step 1: 生本文取得の失敗テストを書く**

```python
def test_fetch_page_lines_preserves_urls_and_title(self):
    response = FakeResponse(200, {"lines": [{"text": "Page"}, {"text": "https://youtu.be/x"}]})
    with patch("scrapbox_search.requests.get", return_value=response):
        assert scrapbox_search.fetch_page_lines("p", "sid", "Page") == [
            "Page", "https://youtu.be/x"
        ]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

実行: `python -m unittest tests.test_scrapbox_search.FetchPageLinesTests -v`

期待結果: `AttributeError`。

- [ ] **Step 3: `fetch_page_lines`を最小実装する**

既存`fetch_page_text`と同じURL、Cookie、タイムアウト、JSONエラー処理を使い、タイトル行を落とさず返す。欠損した行は空文字列へ変換する。

- [ ] **Step 4: テストを通過させる**

実行: `python -m unittest tests.test_scrapbox_search.FetchPageLinesTests -v`

期待結果: PASS。

- [ ] **Step 5: 既存Scrapbox検索テストを実行する**

実行: `python -m unittest tests.test_scrapbox_search -v`

期待結果: PASS。

- [ ] **Step 6: Task 2をコミットする**

```bash
git add scrapbox_search.py tests/test_scrapbox_search.py
git commit -m "feat: preserve raw Scrapbox page lines"
```

### Task 3: Bot側ジョブストアと全ページ走査

**Files:**

- Modify: `eagle_import.py`
- Modify: `tests/test_eagle_import.py`

**Interfaces:**

- Produces `ImportJob(job_id, canonical_url, source: VideoSource, status, attempts, error, result)`。
- Produces `EagleImportStore.create_preview(page_titles, fetch_page, project_url) -> Preview`。
- Produces `EagleImportStore.confirm(preview_id) -> list[ImportJob]`。
- Produces `EagleImportStore.claim(limit=1) -> list[ImportJob]`。
- Produces `EagleImportStore.complete(job_id, result) -> bool`。
- Produces `EagleImportStore.fail(job_id, error, retry_after) -> bool`。
- Produces `EagleImportStore.status() -> dict[str, int]`。

- [ ] **Step 1: ページ走査とプレビューの失敗テストを書く**

```python
def test_create_preview_scans_all_pages_and_counts_fetch_failures():
    pages = {"A": ["A", "https://youtu.be/x"], "B": None}

    store = EagleImportStore()
    preview = store.create_preview(
        ["A", "B"], lambda title: pages[title], "https://scrapbox.io/p"
    )

    assert preview.page_count == 2
    assert preview.video_count == 1
    assert preview.failed_page_count == 1
```

- [ ] **Step 2: テストを実行して失敗を確認する**

実行: `python -m unittest tests.test_eagle_import.ImportStorePreviewTests -v`

期待結果: `ImportError` または `NameError`。

- [ ] **Step 3: プレビュー作成を最小実装する**

ページタイトルごとに生本文を取得し、失敗ページを数える。成功したページは`collect_video_sources`へ渡し、プレビューIDと出典付き候補をメモリへ保存する。

- [ ] **Step 4: プレビューテストを通過させる**

実行: `python -m unittest tests.test_eagle_import.ImportStorePreviewTests -v`

期待結果: PASS。

- [ ] **Step 5: 確認・claim・結果処理の失敗テストを書く**

```python
def test_confirm_creates_pending_jobs_and_claim_moves_them_to_running():
    store = seeded_store_with_one_preview()

    jobs = store.confirm(store.last_preview_id)
    claimed = store.claim(limit=1)

    assert jobs[0].status == "pending"
    assert claimed[0].status == "running"
    assert store.status()["running"] == 1


def test_complete_is_idempotent_for_finished_job():
    store, job_id = seeded_running_store()

    assert store.complete(job_id, {"title": "video"}) is True
    assert store.complete(job_id, {"title": "changed"}) is False
    assert store.status()["succeeded"] == 1
```

- [ ] **Step 6: ジョブ状態遷移を最小実装する**

`pending -> running -> succeeded|failed`だけを許可する。claim済みジョブは`lease_timeout`を過ぎたらpendingへ戻せる。完了済みジョブへの再完了は`False`を返し、状態を変更しない。ジョブIDはUUID文字列、作成時のattemptsは0とする。

- [ ] **Step 7: ジョブテストを通過させる**

実行: `python -m unittest tests.test_eagle_import.ImportStoreStateTests -v`

期待結果: PASS。

- [ ] **Step 8: Task 3全テストを実行する**

実行: `python -m unittest tests.test_eagle_import -v`

期待結果: PASS。

- [ ] **Step 9: Task 3をコミットする**

```bash
git add eagle_import.py tests/test_eagle_import.py
git commit -m "feat: add in-memory Eagle import jobs"
```

### Task 4: BotのEagleコマンドとBridge API

**Files:**

- Modify: `bot.py`
- Modify: `tests/test_bot.py`

**Interfaces:**

- Adds `EAGLE_BRIDGE_TOKEN = os.getenv('EAGLE_BRIDGE_TOKEN', '').strip()`。
- Adds `/eagle import-all` with `confirm: bool = False`.
- Adds `/eagle status` and `/eagle retry-failed`。`retry-failed`は直近ジョブの`failed`だけを新しいpendingジョブとして再登録する。
- Adds `GET /eagle/jobs` and `POST /eagle/jobs/{job_id}/result` to the existing `HealthHandler`.
- Adds `is_eagle_bridge_authorized(headers) -> bool` and JSON response helpers that do not leak the token.

- [ ] **Step 1: API認証とプレビューの失敗テストを書く**

```python
def test_eagle_jobs_rejects_missing_bridge_token(self):
    response = request_health("GET", "/eagle/jobs", headers={})

    assert response.status_code == 401


def test_import_all_without_confirm_only_sends_preview(self):
    interaction = fake_owner_interaction()

    asyncio.run(bot.eagle_import_all(interaction, confirm=False))

    assert "確認" in interaction.followup.messages[0]
    assert store.status()["pending"] == 0
```

- [ ] **Step 2: テストを実行して失敗を確認する**

実行: `python -m unittest tests.test_bot.EagleImportCommandTests.test_eagle_jobs_rejects_missing_bridge_token tests.test_bot.EagleImportCommandTests.test_import_all_without_confirm_only_sends_preview -v`

期待結果: `AttributeError` または期待ステータス不一致。

- [ ] **Step 3: Botコマンドと認証を最小実装する**

既存の所有者チェックパターンを再利用する。プレビューでは`asyncio.to_thread`で全ページ一覧取得と本文取得を実行し、Discordメッセージを1900文字以内に切り詰める。確認時だけstore.confirmを呼ぶ。

- [ ] **Step 4: コマンドテストを通過させる**

実行: `python -m unittest tests.test_bot.EagleImportCommandTests -v`

期待結果: PASS。

- [ ] **Step 4b: statusとretry-failedの失敗テストを書く**

```python
def test_retry_failed_requeues_only_failed_jobs(self):
    seed_failed_and_succeeded_jobs()

    asyncio.run(bot.eagle_retry_failed(fake_owner_interaction()))

    assert store.status()["pending"] == 1
    assert store.status()["succeeded"] == 1
```

- [ ] **Step 4c: statusとretry-failedを最小実装する**

`status`はstore.status()の件数を返す。`retry-failed`は失敗ジョブのURLと出典を引き継いでpendingへ戻し、成功済みジョブやrunningジョブは変更しない。

- [ ] **Step 4d: statusとretry-failedのテストを通過させる**

実行: `python -m unittest tests.test_bot.EagleImportCommandTests -v`

期待結果: PASS。

- [ ] **Step 5: ジョブ取得APIの失敗テストを書く**

```python
def test_eagle_jobs_claims_pending_job_with_valid_token(self):
    job = seeded_pending_job()
    response = request_health(
        "GET", "/eagle/jobs?limit=1",
        headers={"X-Eagle-Bridge-Token": "test-token"},
    )

    assert response.status_code == 200
    assert response.json["jobs"][0]["job_id"] == job.job_id
    assert store.status()["running"] == 1
```

- [ ] **Step 6: 結果報告APIの失敗テストを書く**

```python
def test_eagle_result_marks_job_succeeded_and_notifies_owner(self):
    job = seeded_running_job()
    response = request_health(
        "POST", f"/eagle/jobs/{job.job_id}/result",
        headers={"X-Eagle-Bridge-Token": "test-token"},
        json={"status": "succeeded", "title": "Video"},
    )

    assert response.status_code == 200
    assert store.status()["succeeded"] == 1
```

- [ ] **Step 7: APIルーティングとDM進捗通知を実装する**

既存`HealthHandler`の認証付きPOST処理を壊さず、`/eagle/jobs`だけにGETを追加する。ジョブ取得は最大10件、結果JSONの必須値を検証する。Discord通知は結果ごとに送信せず、成功・失敗累計を含めて一定間隔でまとめる。

- [ ] **Step 8: APIテストを通過させる**

実行: `python -m unittest tests.test_bot.EagleImportApiTests -v`

期待結果: PASS。

- [ ] **Step 9: 既存Botテストを実行する**

実行: `python -m unittest tests.test_bot -v`

期待結果: PASS。

- [ ] **Step 10: Task 4をコミットする**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: add Eagle import commands and bridge API"
```

### Task 5: Windows Eagle Bridge

**Files:**

- Create: `tests/test_eagle_bridge.py`
- Create: `eagle_bridge.py`

**Interfaces:**

- Produces `EagleClient(base_url='http://127.0.0.1:41595', session=None)`。
- `EagleClient.add_from_path(path, name, website, annotation, tags) -> dict` がEagleの`/api/item/addFromPath`へJSONをPOSTする。
- Produces `BridgeManifest(path)` with `contains(canonical_url)`, `record_success(canonical_url, result)`。
- Produces `EagleBridge(bot_url, token, download_dir, manifest, eagle_client, downloader)`。
- `EagleBridge.process_job(job) -> BridgeResult` はスキップ、成功、失敗を返す。

- [ ] **Step 1: Eagle APIクライアントの失敗テストを書く**

```python
def test_eagle_client_posts_local_file_and_metadata():
    session = FakeSession(json_data={"status": "success", "item": {"id": "e1"}})
    client = EagleClient(session=session)

    result = client.add_from_path(
        "C:/tmp/video.mp4", "Title", "https://youtu.be/x", "Scrapbox: Page", ["video"]
    )

    assert result["item"]["id"] == "e1"
    assert session.last_json["name"] == "Title"
    assert session.last_json["website"] == "https://youtu.be/x"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

実行: `python -m unittest tests.test_eagle_bridge.EagleClientTests -v`

期待結果: `ImportError`。

- [ ] **Step 3: Eagle APIクライアントを実装する**

`requests.Session`を使い、タイムアウト10秒で`POST http://127.0.0.1:41595/api/item/addFromPath`を実行する。Eagleが起動していない、HTTPエラー、JSON不正は例外として返す。

- [ ] **Step 4: Eagle APIテストを通過させる**

実行: `python -m unittest tests.test_eagle_bridge.EagleClientTests -v`

期待結果: PASS。

- [ ] **Step 5: マニフェストとジョブ処理の失敗テストを書く**

```python
def test_bridge_skips_url_already_in_manifest(tmp_path):
    manifest = BridgeManifest(tmp_path / "manifest.json")
    manifest.record_success("https://youtu.be/x", {"id": "e1"})
    bridge = make_bridge(manifest=manifest)

    result = bridge.process_job({"canonical_url": "https://youtu.be/x"})

    assert result.status == "skipped"
    assert bridge.downloader.calls == []
```

- [ ] **Step 6: ダウンロード成功・Eagle失敗のテストを書く**

```python
def test_bridge_downloads_then_registers_video_and_records_success(tmp_path):
    bridge = make_bridge(tmp_path=tmp_path)

    result = bridge.process_job({
        "job_id": "j1",
        "canonical_url": "https://youtu.be/x",
        "page_title": "Page",
        "page_url": "https://scrapbox.io/p/Page",
        "source_line": "https://youtu.be/x",
    })

    assert result.status == "succeeded"
    assert bridge.eagle_client.calls[0]["website"] == "https://youtu.be/x"
    assert bridge.manifest.contains("https://youtu.be/x")
```

- [ ] **Step 7: Bridgeの最小実装を作る**

yt-dlpは`YoutubeDL`の注入可能なdownloaderで呼び、`format='bestvideo*+bestaudio/best'`、`merge_output_format='mp4'`、一時ディレクトリを指定する。ダウンロード後の実ファイルを選び、タイトルをメタデータに使う。処理が失敗した場合は一時ファイルを削除し、エラー文字列を`BridgeResult`へ格納する。既成功URLはダウンロード前にスキップする。

- [ ] **Step 8: Bridgeテストを通過させる**

実行: `python -m unittest tests.test_eagle_bridge -v`

期待結果: PASS。

- [ ] **Step 9: ポーリングの失敗テストを書く**

```python
def test_bridge_reports_result_to_bot_after_processing_job():
    bridge = make_bridge_with_bot_session()

    bridge.poll_once()

    assert bridge.bot_session.last_post_path == "/eagle/jobs/j1/result"
    assert bridge.bot_session.last_json["status"] == "succeeded"
```

- [ ] **Step 10: `poll_once`とCLIエントリーポイントを実装する**

`GET {bot_url}/eagle/jobs?limit=1`をトークン付きで呼び、各ジョブを順番に処理し、結果を`POST`する。`--bot-url`、`--token`、`--download-dir`、`--poll-seconds`、`--manifest`をCLI引数で受け、未指定時は`EAGLE_BRIDGE_TOKEN`などの環境変数を使う。トークンをログに出さない。

- [ ] **Step 11: ポーリングテストを通過させる**

実行: `python -m unittest tests.test_eagle_bridge.PollingTests -v`

期待結果: PASS。

- [ ] **Step 12: Task 5をコミットする**

```bash
git add eagle_bridge.py tests/test_eagle_bridge.py
git commit -m "feat: add local Eagle download bridge"
```

### Task 6: README、依存関係、設定例

**Files:**

- Modify: `README.md`
- Modify: `requirements.txt`

- [ ] **Step 1: READMEに必要な設定の失敗チェックを追加する**

テストコードではなく、READMEの必須項目を`Select-String`で確認できるチェックを先に定義する。必須項目は`EAGLE_BRIDGE_TOKEN`、Eagle起動、ffmpeg、`python eagle_bridge.py`、`/eagle import-all confirm:true`、Render再起動時の再実行である。

- [ ] **Step 2: READMEと依存関係を更新する**

Windows側のBridgeセットアップ、Eagle Web APIの前提、公開動画のみという制限、コマンドの順序、トークン生成・設定方法、Bridgeの常駐起動例、失敗時の確認方法を追記する。既に`yt-dlp`が依存に含まれている場合は重複追加せず、Bridgeの実行に必要な依存関係を維持する。

- [ ] **Step 3: README項目を検証する**

実行: `Select-String -Path README.md -Pattern 'EAGLE_BRIDGE_TOKEN|eagle_bridge.py|import-all|ffmpeg|再実行'`

期待結果: 各項目が1回以上見つかる。

- [ ] **Step 4: Task 6をコミットする**

```bash
git add README.md requirements.txt
git commit -m "docs: document Eagle bridge setup"
```

### Task 7: 全体検証と最終レビュー

**Files:**

- Verify: `eagle_import.py`
- Verify: `eagle_bridge.py`
- Verify: `scrapbox_search.py`
- Verify: `bot.py`
- Verify: `tests/`
- Verify: `README.md`

- [ ] **Step 1: 全テストを実行する**

実行: `python -m unittest discover -s tests -v`

期待結果: 既存テストを含めて全件PASS。

- [ ] **Step 2: Python構文と差分を検証する**

実行: `python -m py_compile bot.py eagle_import.py eagle_bridge.py scrapbox_search.py; git diff --check`

期待結果: 終了コード0。

- [ ] **Step 3: セキュリティと運用設定を静的確認する**

`EAGLE_BRIDGE_TOKEN`のログ出力がないこと、Eagle APIの接続先がlocalhostに固定されていること、外部APIへCookieを送らないこと、確認なしでstore.confirmが呼ばれないことを検索とテストで確認する。

- [ ] **Step 4: 完了状態を確認する**

実行: `git status --short --branch; git log --oneline -8`

期待結果: 未追跡ファイル・未コミット差分がなく、Task 1〜6のコミットが確認できる。

- [ ] **Step 5: ユーザーへ実装結果を報告する**

報告には、実装済みコマンド、Bridgeの起動手順、テスト件数、未検証事項（実Eagle・実Scrapbox・実公開動画でのE2E未実施）を分けて記載する。GitHubへのpushやマージは、別途明示的に依頼された場合だけ行う。

---

## Spec Coverage Review

- 全ページ走査: Task 2・Task 3。
- URL抽出、正規化、重複排除、全出典保持: Task 1。
- 確認前ダウンロード禁止: Task 3・Task 4。
- Render BotとWindows Bridgeの分離: Task 4・Task 5。
- Eagle `addFromPath`登録とメタデータ: Task 5。
- 失敗継続、リトライ、lease、既登録スキップ: Task 3・Task 5。
- Token認証、localhost固定、秘密情報非出力: Task 4・Task 5・Task 7。
- READMEとWindowsセットアップ: Task 6。
- 全体テストと未検証事項の明示: Task 7。

## Self-Review

- 実装内容、対象ファイル、テストコマンド、期待結果を各タスクに記載した。
- Task間の主要インターフェース名と引数を一致させた。
- 既存の`unittest discover -s tests`、`requests`、`yt-dlp`を前提にし、新しいWebフレームワークや永続DBを追加していない。
- 実Eagle・実Scrapbox・実動画への接続はローカル環境依存のため、静的テストとは別に未検証として報告する。
