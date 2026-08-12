import unittest

from rss_notifications import (
    FeedConfig,
    NotificationTracker,
    filter_items,
    format_notification,
    load_feed_configs,
    parse_feed,
)


ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry>
    <yt:videoId>video-1</yt:videoId>
    <yt:channelId>channel-1</yt:channelId>
    <title>新しい動画</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=video-1" />
    <published>2026-08-12T00:00:00+00:00</published>
    <author><name>テストチャンネル</name></author>
  </entry>
</feed>"""

RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Qiita</title>
    <item>
      <guid>guid-1</guid>
      <title>Python入門</title>
      <link>https://example.com/items/1</link>
      <pubDate>Wed, 12 Aug 2026 09:00:00 +0900</pubDate>
      <author>author@example.com</author>
    </item>
    <item>
      <title>IDがない記事</title>
    </item>
  </channel>
</rss>"""


ITEM = {
    "item_id": "old",
    "title": "Python入門",
    "url": "https://example.com/items/old",
    "feed_name": "qiita",
    "channel_title": "Qiita",
    "published": "",
}
NEW_ITEM = {**ITEM, "item_id": "new", "title": "Python新着"}


class ParseFeedTests(unittest.TestCase):
    def test_parse_atom_with_youtube_identifier(self):
        self.assertEqual(parse_feed(ATOM_XML, "sana_natori"), [{
            "item_id": "video-1",
            "title": "新しい動画",
            "url": "https://www.youtube.com/watch?v=video-1",
            "feed_name": "sana_natori",
            "channel_title": "テストチャンネル",
            "published": "2026-08-12T00:00:00+00:00",
            "channel_id": "channel-1",
        }])

    def test_parse_rss_item_uses_guid_as_item_id(self):
        items = parse_feed(RSS_XML, "qiita")
        self.assertEqual(items[0]["item_id"], "guid-1")
        self.assertEqual(items[0]["feed_name"], "qiita")
        self.assertEqual(items[0]["channel_title"], "Qiita")
        self.assertEqual(len(items), 1)

    def test_parse_malformed_xml_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_feed("<feed>", "broken")


class FeedConfigTests(unittest.TestCase):
    def test_loads_valid_json_and_normalizes_keywords(self):
        feeds, errors = load_feed_configs(
            '[{"name":"qiita","url":"https://example.com/feed.xml",'
            '"include":[" Python ","python"],"exclude":["広告"]}]',
            [],
        )
        self.assertEqual(errors, [])
        self.assertEqual(feeds, [FeedConfig(
            "qiita", "https://example.com/feed.xml", ("Python",), ("広告",)
        )])

    def test_invalid_feed_config_is_reported_without_stopping_valid_feeds(self):
        feeds, errors = load_feed_configs(
            '[{"name":"ok","url":"https://example.com/feed.xml"},'
            '{"name":"bad","url":"ftp://example.com/feed.xml"},'
            '{"name":"ok","url":"https://example.com/other.xml"}]',
            [],
        )
        self.assertEqual([feed.name for feed in feeds], ["ok"])
        self.assertEqual(len(errors), 2)

    def test_empty_raw_value_uses_defaults(self):
        defaults = [FeedConfig("default", "https://example.com/default.xml")]
        self.assertEqual(load_feed_configs("", defaults), (defaults, []))


class FilterTests(unittest.TestCase):
    def test_filter_include_and_exclude_keywords_case_insensitively(self):
        config = FeedConfig("feed", "https://example.com", ("python",), ("広告",))
        items = [
            {**ITEM, "item_id": "1", "title": "Python入門"},
            {**ITEM, "item_id": "2", "title": "PYTHON広告"},
            {**ITEM, "item_id": "3", "title": "Java入門"},
        ]
        self.assertEqual([item["item_id"] for item in filter_items(items, config)], ["1"])


class TrackerTests(unittest.TestCase):
    def test_first_snapshot_is_baseline_and_tracking_is_scoped_per_feed(self):
        tracker = NotificationTracker()
        self.assertEqual(tracker.new_items([ITEM], "a"), [])
        self.assertEqual(tracker.new_items([ITEM], "b"), [])
        self.assertEqual(tracker.new_items([ITEM, NEW_ITEM], "a"), [NEW_ITEM])

    def test_forget_makes_failed_items_available_for_retry(self):
        tracker = NotificationTracker()
        tracker.new_items([ITEM], "feed")
        pending = tracker.new_items([ITEM, NEW_ITEM], "feed")
        tracker.forget(pending, "feed")
        self.assertEqual(tracker.new_items([ITEM, NEW_ITEM], "feed"), [NEW_ITEM])


class FormattingTests(unittest.TestCase):
    def test_format_notification_groups_generic_items(self):
        message = format_notification([ITEM, NEW_ITEM])
        self.assertIn("RSS新着通知", message)
        self.assertIn("qiita", message)
        self.assertIn("Python新着", message)

    def test_format_notification_truncates_at_message_limit(self):
        items = [{**ITEM, "title": "x" * 100} for _ in range(10)]
        message = format_notification(items, max_length=200)
        self.assertLessEqual(len(message), 200)
        self.assertIn("省略", message)


if __name__ == "__main__":
    unittest.main()
