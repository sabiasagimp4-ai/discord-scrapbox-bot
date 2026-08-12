# YouTube RSS DM Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** YouTube RSSの新着動画・配信ページを5分ごとに検知し、既存の`DIARY_OWNER_USER_ID`へ重複なくDM通知する。

**Architecture:** `youtube_notifications.py`にAtomフィードの取得・解析・差分管理・メッセージ整形を切り出す。`bot.py`はDiscordの定期タスクとして5チャンネルを巡回し、初回基準化、DM取得、エラー記録を担当する。YouTube Data API、Cookie、ライブ状態の判定は行わない。

**Tech Stack:** Python 3.11、標準ライブラリ`xml.etree.ElementTree`、既存の`requests`、`discord.py`、`unittest`。

## Global Constraints

- YouTube Data API、Cookie、ライブ状態の厳密な判定は使用しない。
- YouTube Atomフィードを5分ごとに取得する。
- 通知先は既存の`DIARY_OWNER_USER_ID`を再利用し、未設定時は機能を無効化する。
- Bot起動後の初回取得では、現在のRSS項目を基準化するだけで通知しない。
- 同じ動画IDは一度だけ通知する。
- RSS取得失敗時は、その回の比較・基準更新・通知を行わず、既知IDを失わない。
- テストはネットワークとDiscordへ接続しないモックテストにする。

---

### Task 1: RSS解析と差分管理の失敗テスト

**Files:**
- Create: `tests/test_youtube_notifications.py`

**Interfaces:**
- Consumes: Atom XML文字列と動画ID集合。
- Produces: 後続タスクが実装する`parse_feed(xml_text)`, `format_notification(items)`, `NotificationTracker`の期待動作。

- [ ] **Step 1: Write the failing tests**

```python
import unittest

from youtube_notifications import NotificationTracker, format_notification, parse_feed


ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>YouTube video feed</title>
  <entry>
    <yt:videoId>new123</yt:videoId>
    <yt:channelId>UCchannel</yt:channelId>
    <title>新しい動画</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=new123" />
    <author><name>テストチャンネル</name></author>
  </entry>
</feed>"""


class YouTubeNotificationTests(unittest.TestCase):
    def test_parse_atom_entry(self):
        self.assertEqual(parse_feed(ATOM), [{
            "video_id": "new123",
            "channel_id": "UCchannel",
            "channel_title": "テストチャンネル",
            "title": "新しい動画",
            "url": "https://www.youtube.com/watch?v=new123",
        }])

    def test_parse_empty_feed_returns_empty_list(self):
        self.assertEqual(parse_feed('<feed xmlns="http://www.w3.org/2005/Atom" />'), [])

    def test_parse_malformed_xml_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_feed('<feed>')

    def test_tracker_first_snapshot_is_baseline(self):
        tracker = NotificationTracker()
        items = parse_feed(ATOM)
        self.assertEqual(tracker.new_items(items), [])
        self.assertEqual(tracker.new_items(items), [])

    def test_tracker_returns_only_new_items_and_deduplicates(self):
        tracker = NotificationTracker()
        old = parse_feed(ATOM)
        tracker.new_items(old)
        newer = old + [{**old[0], "video_id": "next456", "title": "次の動画"}]
        self.assertEqual([item["video_id"] for item in tracker.new_items(newer)], ["next456"])
        self.assertEqual(tracker.new_items(newer), [])

    def test_format_notification_groups_multiple_items(self):
        message = format_notification([
            {"channel_title": "A", "title": "動画1", "url": "https://youtu.be/1"},
            {"channel_title": "B", "title": "動画2", "url": "https://youtu.be/2"},
        ])
        self.assertIn("A", message)
        self.assertIn("動画1", message)
        self.assertIn("B", message)
        self.assertIn("動画2", message)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests.test_youtube_notifications -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'youtube_notifications'`.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_youtube_notifications.py
git commit -m "test: specify YouTube RSS notification behavior"
```

### Task 2: RSS解析と差分管理を実装

**Files:**
- Create: `youtube_notifications.py`
- Test: `tests/test_youtube_notifications.py`

**Interfaces:**
- Consumes: `parse_feed(xml_text: str) -> list[dict]`, `NotificationTracker`, `format_notification(items: list[dict]) -> str`。
- Produces: Discordタスクから利用できる、ネットワーク非依存のRSS処理部品。

- [ ] **Step 1: Implement the minimal parser and tracker**

```python
ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"


def parse_feed(xml_text):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("invalid YouTube Atom feed") from exc
    items = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        video_id = entry.findtext(f"{{{YT_NS}}}videoId")
        title = entry.findtext(f"{{{ATOM_NS}}}title")
        link = entry.find(f"{{{ATOM_NS}}}link[@rel='alternate']")
        author = entry.find(f"{{{ATOM_NS}}}author/{{{ATOM_NS}}}name")
        if not video_id or title is None or link is None or not link.get("href"):
            continue
        items.append({
            "video_id": video_id,
            "channel_id": entry.findtext(f"{{{YT_NS}}}channelId") or "",
            "channel_title": author.text if author is not None and author.text else "YouTube",
            "title": title,
            "url": link.get("href"),
        })
    return items
```

`NotificationTracker.new_items(items)`は、初回は全IDを基準化して空リストを返し、2回目以降は未確認IDだけを入力順で返す。取得失敗時に呼び出さなければ基準値は変わらない。

- [ ] **Step 2: Run focused tests to verify they pass**

Run: `python -m unittest tests.test_youtube_notifications -v`

Expected: PASS for all parser, tracker, and message-formatting tests.

- [ ] **Step 3: Run static diff validation and commit**

Run: `git diff --check`

```bash
git add youtube_notifications.py tests/test_youtube_notifications.py
git commit -m "feat: add YouTube RSS parsing and deduplication"
```

### Task 3: Discord定期タスクとDM通知の失敗テスト

**Files:**
- Modify: `tests/test_bot.py`
- Modify: `bot.py`

**Interfaces:**
- Consumes: `youtube_notifications.parse_feed`, `NotificationTracker`, `format_notification`、既存の`_fetch_with_retry`。
- Produces: `youtube_rss_notifications` Discordタスクと`on_ready`からの起動。

- [ ] **Step 1: Add failing integration tests**

既存の`tests/test_bot.py`の`bot`モック構成に合わせ、次を追加する。

```python
def test_youtube_rss_task_sends_one_dm_for_new_items(self):
    with patch.object(bot, "fetch_youtube_feed", return_value=[{
        "video_id": "abc",
        "channel_title": "テストチャンネル",
        "title": "新着",
        "url": "https://youtu.be/abc",
    }]), patch.object(bot, "_youtube_tracker", bot.NotificationTracker()), patch.object(
        bot, "DIARY_OWNER_USER_ID", 123
    ), patch.object(bot.client, "get_user", return_value=self.user):
        asyncio.run(bot.run_youtube_rss_check())
        self.user.send.assert_not_called()
        asyncio.run(bot.run_youtube_rss_check())
        self.user.send.assert_awaited_once()
        self.assertIn("新着", self.user.send.await_args.args[0])


def test_youtube_rss_task_skips_when_owner_is_unset(self):
    with patch.object(bot, "DIARY_OWNER_USER_ID", None), patch.object(bot.client, "get_user") as get_user:
        asyncio.run(bot.run_youtube_rss_check())
        get_user.assert_not_called()
```

`tests/test_bot.py`の`SendDiaryReminderDmTests`の直後に`YouTubeRssNotificationTests(unittest.TestCase)`を追加する。各テストでは`user = AsyncMock()`、`fake_client = MagicMock()`、`fake_client.get_user.return_value = user`を作り、`patch.object(bot, "client", fake_client)`で差し替える。これにより既存のDMテストと同じ`get_user`→`fetch_user`経路を検証する。

- [ ] **Step 2: Run the focused bot tests to verify they fail**

Run: `python -m unittest tests.test_bot.YouTubeNotificationTests -v`

Expected: FAIL because `fetch_youtube_feed`, `_youtube_tracker`, and `run_youtube_rss_check` do not exist yet.

- [ ] **Step 3: Implement feed retrieval and the one-shot check function**

`bot.py`に次を追加する。

- `YOUTUBE_RSS_CHANNELS`: 設計書に記載した5チャンネルIDと表示名のタプル。
- `YOUTUBE_RSS_URL`: `https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}`。
- `fetch_youtube_feed(channel_id)`: `requests.get(..., timeout=20)`、HTTPエラーを例外化、`parse_feed(response.text)`を返す同期関数。
- `_youtube_tracker = NotificationTracker()`。
- `async def run_youtube_rss_check()`: 未設定ならreturn。各フィードを`asyncio.to_thread(fetch_youtube_feed, channel_id)`で取得し、1件でも失敗した回は通知・tracker更新を行わない。全取得成功後に項目を統合し、trackerから新着を取り出す。初回は空になる。
- 新着があれば`_fetch_with_retry(client.get_user, client.fetch_user, DIARY_OWNER_USER_ID)`でユーザーを取得し、`await user.send(format_notification(new_items))`する。
- 例外は既存の`record_error`へ記録し、バックグラウンドタスクを終了させない。

RSS取得では既存の`requests`を使い、Discordの非同期イベントループをブロックしない。

- [ ] **Step 4: Make focused tests pass**

Run: `python -m unittest tests.test_youtube_notifications tests.test_bot.YouTubeNotificationTests -v`

Expected: PASS.

- [ ] **Step 5: Add the five-minute Discord loop and startup guard**

`bot.py`に次を追加する。

```python
@tasks.loop(minutes=5)
async def youtube_rss_notifications():
    await run_youtube_rss_check()
```

`on_ready()`では、`DIARY_OWNER_USER_ID`があり、`youtube_rss_notifications.is_running()`が偽のときだけ`.start()`する。既存タスクの起動順序と再接続時の二重起動防止パターンを維持する。

- [ ] **Step 6: Add task lifecycle tests and run them**

既存の`on_ready`テストに、通知先設定時に`youtube_rss_notifications.start()`が1回だけ呼ばれること、未設定時に呼ばれないことを追加する。

Run: `python -m unittest tests.test_bot -v`

Expected: PASS when project dependencies are installed. In the current environment, `discord.py`が未インストールなら既存のbotテストはimport時点で停止するため、その事実を検証結果に記録する。

- [ ] **Step 7: Commit the Discord integration**

```bash
git add bot.py tests/test_bot.py
git commit -m "feat: notify Discord DM from YouTube RSS"
```

### Task 4: READMEと全体検証

**Files:**
- Modify: `README.md`
- Test: `tests/test_youtube_notifications.py`, `tests/test_bot.py`

**Interfaces:**
- Consumes: 実装済みのRSS定期タスクと`DIARY_OWNER_USER_ID`設定。
- Produces: 利用者がデプロイ後に確認できる設定・制約ドキュメントと検証済み差分。

- [ ] **Step 1: Update README**

自動通知の一覧、RSS通知の対象5チャンネル、`DIARY_OWNER_USER_ID`がDM先として必要であること、5分間隔はアプリ側の確認間隔でありYouTube側のキャッシュ遅延を含めた保証ではないこと、ライブ開始の厳密判定をしないことを追記する。`YOUTUBE_API_KEY`は既存の別機能のため、RSS通知に不要であることも明記する。

- [ ] **Step 2: Run all available tests**

Run: `python -m unittest discover -s tests -v`

Expected: 新規純粋テストを含む依存関係が利用できるテストはPASS。`discord.py`未インストールの環境では`tests/test_bot.py`だけimportエラーになるため、依存関係をインストールできない場合はそのまま報告する。

- [ ] **Step 3: Run repository checks**

Run: `git diff --check; git status --short`

Expected: whitespace errorなし。意図しない未追跡・未コミットファイルがない。

- [ ] **Step 4: Review the final diff and commit documentation**

```bash
git add README.md
git commit -m "docs: document YouTube RSS DM notifications"
git log --oneline -4
```

最終確認では、YouTube Data APIやCookieが追加されていないこと、初回基準化と重複排除、DM送信先、RSS失敗時の状態保持を差分から確認する。
