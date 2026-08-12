"""Backward-compatible YouTube wrappers around the generic RSS helpers."""

from rss_notifications import NotificationTracker as _NotificationTracker
from rss_notifications import parse_feed as _parse_feed


def parse_feed(xml_text):
    """Parse a YouTube Atom feed into notification-ready dictionaries."""
    items = _parse_feed(xml_text)
    return [{
        "video_id": item["item_id"],
        "channel_id": item.get("channel_id", ""),
        "channel_title": item.get("channel_title", "YouTube"),
        "title": item["title"],
        "url": item["url"],
    } for item in items]


class NotificationTracker(_NotificationTracker):
    """Keep a process-local baseline of already observed video IDs."""

    def new_items(self, items):
        normalized = [{**item, "item_id": item["video_id"]} for item in items]
        new_items = super().new_items(normalized)
        return [{key: value for key, value in item.items() if key != "item_id"} for item in new_items]

    def forget(self, items):
        """Remove items whose notification failed so the next poll can retry."""
        return super().forget([{**item, "item_id": item["video_id"]} for item in items])


def format_notification(items):
    """Build one compact DM for one or more newly observed feed entries."""
    lines = ["📺 YouTube新着通知"]
    for item in items:
        lines.extend([
            "",
            f"{item['channel_title']}",
            f"{item['title']}",
            item["url"],
        ])
    return "\n".join(lines)
