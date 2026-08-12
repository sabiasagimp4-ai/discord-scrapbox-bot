"""Pure helpers for polling public RSS and Atom feeds."""

from dataclasses import dataclass, field
import json
from urllib.parse import urlparse
import xml.etree.ElementTree as ET


ATOM_NS = "http://www.w3.org/2005/Atom"
YT_NS = "http://www.youtube.com/xml/schemas/2015"


@dataclass(frozen=True)
class FeedConfig:
    name: str
    url: str
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass
class FeedHealth:
    paused: bool = False
    last_success: float | None = None
    consecutive_failures: int = 0
    last_error: str = ""
    last_notification_count: int = 0


def _text(element, path=""):
    value = element.findtext(path) if path else element.text
    return (value or "").strip()


def _atom_items(root, feed_name):
    items = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        item_id = _text(entry, f"{{{YT_NS}}}videoId") or _text(entry, f"{{{ATOM_NS}}}id")
        title = _text(entry, f"{{{ATOM_NS}}}title")
        link = next((
            candidate for candidate in entry.findall(f"{{{ATOM_NS}}}link")
            if candidate.get("href") and candidate.get("rel", "alternate") == "alternate"
        ), None)
        if link is None:
            link = next((candidate for candidate in entry.findall(f"{{{ATOM_NS}}}link") if candidate.get("href")), None)
        url = link.get("href", "").strip() if link is not None else ""
        if not item_id or not title or not url:
            continue
        items.append({
            "item_id": item_id,
            "title": title,
            "url": url,
            "feed_name": feed_name,
            "channel_title": _text(entry, f"{{{ATOM_NS}}}author/{{{ATOM_NS}}}name") or feed_name,
            "published": _text(entry, f"{{{ATOM_NS}}}published") or _text(entry, f"{{{ATOM_NS}}}updated"),
            "channel_id": _text(entry, f"{{{YT_NS}}}channelId"),
        })
    return items


def _rss_items(root, feed_name):
    channel = root.find("channel")
    if channel is None:
        return []
    channel_title = _text(channel, "title") or feed_name
    items = []
    for entry in channel.findall("item"):
        item_id = _text(entry, "guid") or _text(entry, "link")
        title = _text(entry, "title")
        url = _text(entry, "link")
        if not item_id or not title or not url:
            continue
        items.append({
            "item_id": item_id,
            "title": title,
            "url": url,
            "feed_name": feed_name,
            "channel_title": channel_title,
            "published": _text(entry, "pubDate") or _text(entry, "published"),
            "channel_id": "",
        })
    return items


def parse_feed(xml_text: str, feed_name: str = "RSS"):
    """Parse RSS 2.0 or Atom XML into notification-ready dictionaries."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("invalid RSS or Atom feed") from exc

    if root.tag == f"{{{ATOM_NS}}}feed":
        return _atom_items(root, feed_name)
    if root.tag == "rss":
        return _rss_items(root, feed_name)
    return []


def _keywords(value):
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("keywords must be an array")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("keywords must contain strings")
        item = item.strip()
        if item and item.casefold() not in {existing.casefold() for existing in result}:
            result.append(item)
    return tuple(result)


def _feed_from_dict(raw):
    if not isinstance(raw, dict):
        raise ValueError("feed must be an object")
    name = raw.get("name")
    url = raw.get("url")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("feed name must be a non-empty string")
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
        raise ValueError("feed URL must be an HTTP(S) URL")
    return FeedConfig(name.strip(), url.strip(), _keywords(raw.get("include")), _keywords(raw.get("exclude")))


def load_feed_configs(raw: str, defaults):
    """Load JSON feed definitions, returning valid feeds and non-fatal errors."""
    if not raw or not raw.strip():
        return list(defaults), []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        return list(defaults), [f"RSS_NOTIFICATION_FEEDS JSON: {exc.msg}"]
    if not isinstance(values, list):
        return list(defaults), ["RSS_NOTIFICATION_FEEDS must be a JSON array"]

    feeds = []
    errors = []
    names = set()
    for index, value in enumerate(values):
        try:
            feed = _feed_from_dict(value)
            if feed.name in names:
                raise ValueError("duplicate feed name")
            names.add(feed.name)
            feeds.append(feed)
        except ValueError as exc:
            errors.append(f"feed[{index}]: {exc}")
    return feeds, errors


def filter_items(items, config: FeedConfig):
    """Keep items matching include keywords and not matching exclude keywords."""
    include = tuple(keyword.casefold() for keyword in config.include)
    exclude = tuple(keyword.casefold() for keyword in config.exclude)
    result = []
    for item in items:
        title = item.get("title", "").casefold()
        if include and not any(keyword in title for keyword in include):
            continue
        if any(keyword in title for keyword in exclude):
            continue
        result.append(item)
    return result


def _item_id(item):
    return item.get("item_id") or item.get("video_id") or item.get("url")


class NotificationTracker:
    """Keep process-local baselines and seen IDs independently per feed."""

    def __init__(self):
        self._seen_ids = {}

    def new_items(self, items, feed_name="default"):
        current_ids = {_item_id(item) for item in items if _item_id(item)}
        seen_ids = self._seen_ids.get(feed_name)
        if seen_ids is None:
            self._seen_ids[feed_name] = current_ids
            return []
        new_items = [item for item in items if _item_id(item) not in seen_ids]
        seen_ids.update(current_ids)
        return new_items

    def forget(self, items, feed_name="default"):
        """Remove items whose notification failed so the next poll can retry."""
        seen_ids = self._seen_ids.get(feed_name)
        if seen_ids is not None:
            seen_ids.difference_update(_item_id(item) for item in items if _item_id(item))


def format_notification(items, max_length=2000):
    """Build one compact DM for one or more newly observed feed entries."""
    lines = ["📡 RSS新着通知"]
    included = 0
    for item in items:
        block = [
            "",
            item.get("feed_name") or item.get("channel_title") or "RSS",
            item["title"],
            item["url"],
        ]
        candidate = "\n".join(lines + block)
        if len(candidate) > max_length:
            remaining = len(items) - included
            suffix = f"\n…{remaining}件を省略（メッセージ上限）"
            return ("\n".join(lines) + suffix)[:max_length]
        lines.extend(block)
        included += 1
    return "\n".join(lines)
