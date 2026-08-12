"""Pure helpers for polling YouTube's public Atom video feeds."""

import xml.etree.ElementTree as ET


ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"


def parse_feed(xml_text):
    """Parse a YouTube Atom feed into notification-ready dictionaries."""
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
        if not video_id or not title or link is None or not link.get("href"):
            continue
        items.append({
            "video_id": video_id,
            "channel_id": entry.findtext(f"{{{YT_NS}}}channelId") or "",
            "channel_title": author.text if author is not None and author.text else "YouTube",
            "title": title,
            "url": link.get("href"),
        })
    return items


class NotificationTracker:
    """Keep a process-local baseline of already observed video IDs."""

    def __init__(self):
        self._seen_ids = None

    def new_items(self, items):
        current_ids = {item["video_id"] for item in items}
        if self._seen_ids is None:
            self._seen_ids = current_ids
            return []

        new_items = [item for item in items if item["video_id"] not in self._seen_ids]
        self._seen_ids.update(current_ids)
        return new_items

    def forget(self, items):
        """Remove items whose notification failed so the next poll can retry."""
        if self._seen_ids is not None:
            self._seen_ids.difference_update(item["video_id"] for item in items)


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
