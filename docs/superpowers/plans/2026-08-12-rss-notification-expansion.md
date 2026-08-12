# RSS通知拡張 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube RSS通知を汎用RSS/Atom通知へ拡張し、キーワードフィルタ、フィード別ヘルス表示、所有者向けの一時停止・再開・テストコマンドを追加する。

**Architecture:** RSS/Atomの解析、設定検証、フィルタ、重複排除、通知文生成は新しい純粋モジュール `rss_notifications.py` に集約する。既存の `youtube_notifications.py` は旧テストと既存呼び出しを壊さない互換アダプタとして残し、Discord・HTTP・環境変数の統合は既存の `bot.py` に限定する。

**Tech Stack:** Python 3、標準ライブラリ（`dataclasses`、`json`、`urllib.parse`、`xml.etree.ElementTree`）、既存の `requests`、`discord.py`、`unittest`。

## Global Constraints

- YouTube Data API、Cookie、WebSub、SQLite、外部DB、新規依存パッケージは追加しない。
- `DIARY_OWNER_USER_ID` 未設定時はRSSポーリングを開始せず、RSS管理コマンドも利用不可として応答する。
- 初回ポーリングは各フィードの現在記事をベースラインとし、既存記事を一括通知しない。
- 重複排除状態はプロセスメモリのみで保持し、Bot再起動後の永続的な重複防止は保証しない。
- 1フィードの取得失敗で他フィードの処理を中断しない。
- 外部ネットワーク、実Discord DM、Scrapboxへの接続はユニットテストで行わない。

---

## File Map

- Create: `rss_notifications.py` — RSS/Atom解析、設定、フィルタ、重複排除、通知文、ヘルス状態の純粋ロジック。
- Modify: `youtube_notifications.py` — 既存のYouTube用関数シグネチャを新モジュールへ接続する互換アダプタ。
- Modify: `bot.py` — Feed設定のロード、5分ごとの個別フィード取得、DM送信、`/rss`コマンド、`/status`表示。
- Create: `tests/test_rss_notifications.py` — 新しいRSS共通ロジックのユニットテスト。
- Modify: `tests/test_youtube_notifications.py` — 互換アダプタと既存YouTube出力を確認するテスト。
- Modify: `tests/test_bot.py` — 汎用RSS巡回、フィード失敗分離、所有者ガード、コマンド用ヘルパーのテスト。
- Modify: `README.md` — `RSS_NOTIFICATION_FEEDS`形式、コマンド、再起動時の制約を追記。

## Task 1: RSS/Atom共通ロジック

**Files:**
- Create: `rss_notifications.py`
- Create: `tests/test_rss_notifications.py`
- Modify: `youtube_notifications.py`
- Modify: `tests/test_youtube_notifications.py`

**Interfaces:**
- `FeedConfig(name: str, url: str, include: tuple[str, ...], exclude: tuple[str, ...])`
- `FeedHealth(paused: bool, last_success: float | None, consecutive_failures: int, last_error: str, last_notification_count: int)`
- `parse_feed(xml_text: str, feed_name: str = "RSS") -> list[dict]`
- `load_feed_configs(raw: str, defaults: list[FeedConfig]) -> tuple[list[FeedConfig], list[str]]`
- `filter_items(items: list[dict], config: FeedConfig) -> list[dict]`
- `NotificationTracker.new_items(items: list[dict], feed_name: str = "default") -> list[dict]`
- `NotificationTracker.forget(items: list[dict], feed_name: str = "default") -> None`
- `format_notification(items: list[dict], max_length: int = 2000) -> str`

- [ ] **Step 1: Write failing parser and configuration tests**

Add tests for Atom with `yt:videoId`, RSS 2.0 with `guid`, missing identifiers, malformed XML, valid JSON feed configuration, duplicate names, invalid URL schemes, and fallback to defaults when the environment value is empty.

```python
def test_parse_rss_item_uses_guid_as_item_id():
    items = parse_feed(RSS_XML, "qiita")
    self.assertEqual(items[0]["item_id"], "guid-1")
    self.assertEqual(items[0]["feed_name"], "qiita")

def test_invalid_feed_config_is_reported_without_stopping_valid_feeds():
    feeds, errors = load_feed_configs(
        '[{"name":"ok","url":"https://example.com/feed.xml"},'
        '{"name":"bad","url":"ftp://example.com/feed.xml"}]',
        [],
    )
    self.assertEqual([feed.name for feed in feeds], ["ok"])
    self.assertEqual(len(errors), 1)
```

- [ ] **Step 2: Run the focused tests and verify the expected RED failure**

Run: `python -m unittest tests.test_rss_notifications -v`

Expected: FAIL because `rss_notifications.py` and its parser/configuration functions do not exist yet.

- [ ] **Step 3: Implement minimal `FeedConfig`, `parse_feed`, and `load_feed_configs`**

Use namespace-aware ElementTree parsing. For Atom choose `yt:videoId`, then `<id>`; for RSS choose `<guid>`, then `<link>`. Return canonical item dictionaries with `item_id`, `title`, `url`, `feed_name`, `channel_title`, and `published` keys. Ignore entries without an ID, title, or URL. Validate names as non-empty unique strings and URLs as HTTP(S).

- [ ] **Step 4: Add failing filter, tracker, and formatting tests**

```python
def test_filter_include_and_exclude_keywords_case_insensitively(self):
    config = FeedConfig("feed", "https://example.com", ("python",), ("広告",))
    items = [{"item_id":"1", "title":"Python入門", "url":"https://x/1"},
             {"item_id":"2", "title":"Python広告", "url":"https://x/2"}]
    self.assertEqual([item["item_id"] for item in filter_items(items, config)], ["1"])

def test_tracker_is_scoped_per_feed_and_first_snapshot_is_baseline(self):
    tracker = NotificationTracker()
    self.assertEqual(tracker.new_items([ITEM], "a"), [])
    self.assertEqual(tracker.new_items([ITEM], "b"), [])
    self.assertEqual(tracker.new_items([ITEM, NEW_ITEM], "a"), [NEW_ITEM])
```

- [ ] **Step 5: Run the new tests to verify the expected RED failure**

Run: `python -m unittest tests.test_rss_notifications -v`

Expected: FAIL on the missing filtering/tracker/formatting behavior.

- [ ] **Step 6: Implement filtering, feed-scoped tracking, retry reset, health defaults, and 2000-character notification truncation**

Record all fetched items in the tracker before applying title filters so excluded items do not reappear later. Make `forget` remove only items whose Discord send failed. Keep the old `youtube_notifications` output shape through an adapter that maps `item_id` to `video_id`, and preserve the old optional `feed_name` argument behavior.

- [ ] **Step 7: Run focused common-module tests and the existing YouTube compatibility tests**

Run: `python -m unittest tests.test_rss_notifications tests.test_youtube_notifications -v`

Expected: all focused tests pass with no network access.

- [ ] **Step 8: Commit the common RSS module**

```powershell
git add rss_notifications.py youtube_notifications.py tests/test_rss_notifications.py tests/test_youtube_notifications.py
git commit -m "feat: add generic RSS notification helpers"
```

## Task 2: Bot integration and per-feed health

**Files:**
- Modify: `bot.py` near the environment constants, RSS task, `on_ready`, and `/status` implementation.
- Modify: `tests/test_bot.py` in the existing YouTube RSS test section.

**Interfaces:**
- `fetch_rss_feed(config: FeedConfig) -> list[dict]`
- `run_rss_check() -> None`
- `run_youtube_rss_check() -> None` as a backwards-compatible wrapper calling `run_rss_check()`.
- `build_rss_status_lines(now: float | None = None) -> list[str]`

- [ ] **Step 1: Add failing bot-level tests for generic feeds and failure isolation**

Patch `bot.RSS_FEEDS`, `bot.fetch_rss_feed`, `bot.client`, and `bot.DIARY_OWNER_USER_ID` with test doubles. Verify that the first poll sends no DM, the second poll sends only a new item, a failed feed is recorded while another feed still notifies, and an owner-unset poll makes no fetch calls.

```python
def test_rss_check_notifies_successful_feed_when_another_feed_fails(self):
    with patch.object(bot, "RSS_FEEDS", [GOOD_FEED, BAD_FEED]), \
         patch.object(bot, "fetch_rss_feed", side_effect=[[OLD], RuntimeError("bad")]):
        asyncio.run(bot.run_rss_check())
    with patch.object(bot, "fetch_rss_feed", side_effect=[[OLD, NEW], RuntimeError("bad")]):
        asyncio.run(bot.run_rss_check())
    user.send.assert_awaited_once()
    self.assertIn("new", user.send.await_args.args[0])
```

- [ ] **Step 2: Run the focused bot tests and verify the expected RED failure**

Run: `python -m unittest tests.test_bot.YouTubeRssNotificationTests -v`

Expected: FAIL because `run_rss_check`, `RSS_FEEDS`, and per-feed health do not exist yet.

- [ ] **Step 3: Add configuration loading and generic fetch integration**

Define the five existing YouTube defaults as `FeedConfig` values. Load `RSS_NOTIFICATION_FEEDS` once at startup with `load_feed_configs`. Implement `fetch_rss_feed` using the existing `requests.get(..., timeout=20)` pattern and the new parser. Keep `fetch_youtube_feed(channel_id)` as a small compatibility function for callers that still need the old channel-specific shape.

- [ ] **Step 4: Implement `run_rss_check` and wire the existing five-minute loop**

Create one `NotificationTracker` and one `FeedHealth` per configured feed. Skip paused feeds. Fetch enabled feeds with `asyncio.gather(..., return_exceptions=True)`, update each health record independently, mark every fetched item as seen, then filter newly seen items. Aggregate eligible items into one owner DM. On DM failure call `forget` for the eligible items and record the task error. Keep `youtube_rss_notifications` as the existing `@tasks.loop(minutes=5)` entry point, but make it call `run_rss_check`.

- [ ] **Step 5: Add RSS health lines to `/status` and start the task from `on_ready`**

Add feed status lines without exposing secrets. Start the existing loop only when `DIARY_OWNER_USER_ID` is nonzero, preserving the current startup guard. The status output must include enabled/paused state, last success, consecutive failures, last error, and last notification count.

- [ ] **Step 6: Run focused bot tests and the full suite**

Run: `python -m unittest tests.test_bot.YouTubeRssNotificationTests -v`

Expected: focused RSS bot tests pass.

Run: `python -m unittest discover -s tests -v`

Expected: the complete existing suite passes.

- [ ] **Step 7: Commit bot integration**

```powershell
git add bot.py tests/test_bot.py
git commit -m "feat: run configurable RSS feeds independently"
```

## Task 3: Owner management commands

**Files:**
- Modify: `bot.py` near the existing slash-command definitions.
- Modify: `tests/test_bot.py` with command helper tests.

**Interfaces:**
- `/rss list`
- `/rss pause name`
- `/rss resume name`
- `/rss test name`
- `_rss_owner_allowed(interaction) -> bool`
- `_find_rss_feed(name: str) -> FeedConfig | None`

- [ ] **Step 1: Add failing tests for owner authorization and pause/resume state**

Test an interaction whose user ID equals `DIARY_OWNER_USER_ID`, a different user, and an unset owner ID. Verify mutating commands do not change pause state for unauthorized users and do return an explanatory ephemeral response.

- [ ] **Step 2: Run the focused command tests and verify RED**

Run: `python -m unittest tests.test_bot.RssCommandTests -v`

Expected: FAIL because the `/rss` command group and authorization helpers do not exist.

- [ ] **Step 3: Implement the `/rss` command group**

Register an `app_commands.Group` on the existing `CommandTree`. `list` may show the current configuration; `pause`, `resume`, and `test` require the owner guard. `test` fetches the selected feed and sends the latest parsed item to the owner DM using the same formatter as automatic notifications, then acknowledges the command ephemerally. Unknown feed names and empty feeds return clear ephemeral errors.

- [ ] **Step 4: Run command tests and the full suite**

Run: `python -m unittest tests.test_bot.RssCommandTests -v`

Expected: all command tests pass.

Run: `python -m unittest discover -s tests -v`

Expected: no regression in existing commands or event handlers.

- [ ] **Step 5: Commit RSS management commands**

```powershell
git add bot.py tests/test_bot.py
git commit -m "feat: add RSS management commands"
```

## Task 4: Documentation and final verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document `RSS_NOTIFICATION_FEEDS`**

Add the JSON format, an example for a non-YouTube feed, keyword include/exclude semantics, the `/rss` commands, and the fact that state is process-local and resets after restart. Keep the existing YouTube API/Cookie policy unchanged.

- [ ] **Step 2: Run static and full verification**

Run:

```powershell
python -m unittest discover -s tests -v
python -m py_compile bot.py rss_notifications.py youtube_notifications.py
git diff --check
git status --short
```

Expected: all tests pass, compilation exits 0, `git diff --check` emits no errors, and only intended files are modified before the final commit.

- [ ] **Step 3: Commit documentation and final changes**

```powershell
git add README.md
git commit -m "docs: document configurable RSS notifications"
```
