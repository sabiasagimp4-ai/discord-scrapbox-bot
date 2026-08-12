"""Helpers for collecting video URLs from Scrapbox pages."""

import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_URL_CHARS = "。、．,.;:!?)]}"
_REMOVED_QUERY_KEYS = {"app", "feature", "si"}


@dataclass(frozen=True)
class SourceOccurrence:
    source_url: str
    page_title: str
    page_url: str
    source_line: str


@dataclass
class VideoSource:
    canonical_url: str
    sources: list[SourceOccurrence] = field(default_factory=list)


@dataclass
class ImportPreview:
    preview_id: str
    page_count: int
    video_count: int
    failed_page_count: int
    sources: list[VideoSource] = field(default_factory=list)
    confirmed: bool = False


@dataclass
class ImportJob:
    job_id: str
    canonical_url: str
    source: VideoSource
    status: str = "pending"
    attempts: int = 0
    error: str = ""
    result: dict = field(default_factory=dict)
    lease_until: float = 0.0

    def to_dict(self):
        representative = self.source.sources[0]
        return {
            "job_id": self.job_id,
            "canonical_url": self.canonical_url,
            "source_url": representative.source_url,
            "page_title": representative.page_title,
            "page_url": representative.page_url,
            "source_line": representative.source_line,
            "sources": [
                {
                    "source_url": source.source_url,
                    "page_title": source.page_title,
                    "page_url": source.page_url,
                    "source_line": source.source_line,
                }
                for source in self.source.sources
            ],
            "attempts": self.attempts,
            "status": self.status,
        }


class EagleImportStore:
    """Process-local preview and import job state for the Eagle bridge."""

    def __init__(self, lease_seconds=300, clock=None):
        self.lease_seconds = lease_seconds
        self._clock = clock or time.time
        self._previews = {}
        self._jobs = {}

    def create_preview(self, page_titles, fetch_page, project_url):
        pages = []
        failed_page_count = 0
        for title in page_titles:
            lines = fetch_page(title)
            if lines is None:
                failed_page_count += 1
                continue
            pages.append(
                {
                    "title": title,
                    "url": f"{project_url.rstrip('/')}/{quote(title)}",
                    "lines": lines,
                }
            )

        preview = ImportPreview(
            preview_id=str(uuid.uuid4()),
            page_count=len(page_titles),
            video_count=0,
            failed_page_count=failed_page_count,
        )
        preview.sources = collect_video_sources(pages)
        preview.video_count = len(preview.sources)
        self._previews[preview.preview_id] = preview
        return preview

    def confirm(self, preview_id):
        preview = self._previews.get(preview_id)
        if preview is None or preview.confirmed:
            return []
        preview.confirmed = True
        jobs = []
        for source in preview.sources:
            job = ImportJob(
                job_id=str(uuid.uuid4()),
                canonical_url=source.canonical_url,
                source=source,
            )
            self._jobs[job.job_id] = job
            jobs.append(job)
        return jobs

    def claim(self, limit=1):
        now = self._clock()
        for job in self._jobs.values():
            if job.status == "running" and job.lease_until <= now:
                job.status = "pending"
                job.lease_until = 0.0

        claimed = []
        for job in self._jobs.values():
            if job.status != "pending" or len(claimed) >= limit:
                continue
            job.status = "running"
            job.attempts += 1
            job.lease_until = now + self.lease_seconds
            claimed.append(job)
        return claimed

    def complete(self, job_id, result):
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job.status = "succeeded"
        job.result = dict(result or {})
        job.error = ""
        job.lease_until = 0.0
        return True

    def fail(self, job_id, error):
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job.status = "failed"
        job.error = str(error)
        job.lease_until = 0.0
        return True

    def retry_failed(self):
        retried = []
        for job in self._jobs.values():
            if job.status != "failed":
                continue
            job.status = "pending"
            job.error = ""
            retried.append(job)
        return retried

    def get(self, job_id):
        return self._jobs.get(job_id)

    def status(self):
        counts = {name: 0 for name in ("pending", "running", "succeeded", "failed")}
        for job in self._jobs.values():
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts


def extract_video_urls(text):
    """Return HTTP(S) URLs in *text*, preserving their order of appearance."""
    urls = []
    for match in _URL_RE.finditer(text or ""):
        urls.append(match.group(0).rstrip(_TRAILING_URL_CHARS))
    return urls


def canonicalize_video_url(url):
    """Normalize a URL enough for duplicate detection without losing video IDs."""
    try:
        parsed = urlsplit((url or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None

    host = parsed.netloc.lower()
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _REMOVED_QUERY_KEYS
    ]

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = parsed.path.strip("/").split("/", 1)[0]
        return f"https://youtu.be/{video_id}" if video_id else None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = dict(query).get("v", "")
            return f"https://youtu.be/{video_id}" if video_id else None
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) == 2 and path_parts[0] in {"shorts", "live"}:
            return f"https://youtu.be/{path_parts[1]}"

    return urlunsplit(
        (
            parsed.scheme.lower(),
            host,
            parsed.path,
            urlencode(query),
            "",
        )
    )


def collect_video_sources(pages):
    """Collect normalized URLs and retain every page occurrence as provenance."""
    grouped = {}
    for page in pages:
        title = str(page.get("title", ""))
        page_url = str(page.get("url", ""))
        for line in page.get("lines", []):
            source_line = str(line)
            for source_url in extract_video_urls(source_line):
                canonical_url = canonicalize_video_url(source_url)
                if not canonical_url:
                    continue
                grouped.setdefault(canonical_url, VideoSource(canonical_url)).sources.append(
                    SourceOccurrence(source_url, title, page_url, source_line)
                )
    return list(grouped.values())
