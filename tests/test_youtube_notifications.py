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

    def test_tracker_can_forget_items_when_notification_fails(self):
        tracker = NotificationTracker()
        old = parse_feed(ATOM)
        tracker.new_items(old)
        newer = old + [{**old[0], "video_id": "next456", "title": "次の動画"}]
        pending = tracker.new_items(newer)
        tracker.forget(pending)
        self.assertEqual([item["video_id"] for item in tracker.new_items(newer)], ["next456"])

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
