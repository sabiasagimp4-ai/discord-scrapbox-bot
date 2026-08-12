"""Local Windows bridge between the Render bot and Eagle."""

import argparse
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests


EAGLE_API_URL = 'http://127.0.0.1:41595/api/item/addFromPath'


class EagleClient:
    def __init__(self, base_url=EAGLE_API_URL, session=None, timeout=10):
        self.base_url = base_url
        self.session = session or requests.Session()
        self.timeout = timeout

    def add_from_path(self, path, name, website, annotation, tags):
        response = self.session.post(
            self.base_url,
            json={
                'path': str(path),
                'name': name,
                'website': website,
                'annotation': annotation,
                'tags': tags,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()


class BridgeManifest:
    def __init__(self, path):
        self.path = Path(path)
        self._items = {}
        self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(data, dict):
            self._items = data.get('items', {})

    def contains(self, canonical_url):
        return canonical_url in self._items

    def get(self, canonical_url):
        return self._items.get(canonical_url)

    def record_success(self, canonical_url, result):
        self._items[canonical_url] = dict(result or {})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + '.tmp')
        temporary.write_text(
            json.dumps({'items': self._items}, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        temporary.replace(self.path)


@dataclass
class BridgeResult:
    status: str
    title: str = ''
    file_name: str = ''
    eagle_item_id: str = ''
    error: str = ''

    def to_dict(self):
        return {
            'status': self.status,
            'title': self.title,
            'file_name': self.file_name,
            'eagle_item_id': self.eagle_item_id,
            'error': self.error,
        }


def download_with_ytdlp(url, download_dir):
    """Download one public video and return its local path and title."""
    from yt_dlp import YoutubeDL

    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    options = {
        'format': 'bestvideo*+bestaudio/best',
        'merge_output_format': 'mp4',
        'outtmpl': str(download_dir / '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        requested = Path(ydl.prepare_filename(info))
        candidates = [requested, requested.with_suffix('.mp4')]
        path = next((candidate for candidate in candidates if candidate.exists()), None)
        if path is None:
            files = [item for item in download_dir.iterdir() if item.is_file()]
            if not files:
                raise FileNotFoundError('yt-dlp did not create a media file')
            path = max(files, key=lambda item: item.stat().st_mtime)
        return {'path': str(path), 'title': info.get('title') or url}


class EagleBridge:
    def __init__(
        self,
        bot_url='',
        token='',
        download_dir=None,
        manifest=None,
        eagle_client=None,
        downloader=None,
        session=None,
    ):
        self.bot_url = bot_url.rstrip('/')
        self.token = token
        self.download_dir = Path(download_dir or Path.home() / 'eagle-bridge-downloads')
        self.manifest = manifest or BridgeManifest(self.download_dir / 'manifest.json')
        self.eagle_client = eagle_client or EagleClient()
        self.downloader = downloader or download_with_ytdlp
        self.session = session or requests.Session()

    def process_job(self, job):
        canonical_url = job['canonical_url']
        if self.manifest.contains(canonical_url):
            item = self.manifest.get(canonical_url) or {}
            return BridgeResult(
                status='skipped',
                title=item.get('title', ''),
                eagle_item_id=item.get('eagle_item_id', item.get('id', '')),
            )

        downloaded = None
        try:
            downloaded = self.downloader(canonical_url, self.download_dir)
            title = downloaded.get('title') or job.get('page_title') or canonical_url
            annotation = (
                f"Scrapbox: {job.get('page_title', '')}\n"
                f"{job.get('page_url', '')}\n"
                f"元の行: {job.get('source_line', '')}\n"
                f"取り込み日時: {time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            eagle_result = self.eagle_client.add_from_path(
                path=downloaded['path'],
                name=title,
                website=job.get('source_url') or canonical_url,
                annotation=annotation,
                tags=['scrapbox', 'video'],
            )
            item = eagle_result.get('item', {}) if isinstance(eagle_result, dict) else {}
            result = {
                'title': title,
                'file_name': Path(downloaded['path']).name,
                'eagle_item_id': item.get('id', '') if isinstance(item, dict) else '',
            }
            self.manifest.record_success(canonical_url, result)
            return BridgeResult(status='succeeded', **result)
        except Exception as error:
            return BridgeResult(status='failed', error=str(error)[:500])
        finally:
            if downloaded and downloaded.get('path'):
                try:
                    Path(downloaded['path']).unlink(missing_ok=True)
                except OSError:
                    pass

    def poll_once(self, limit=1):
        headers = {'X-Eagle-Bridge-Token': self.token}
        response = self.session.get(
            f'{self.bot_url}/eagle/jobs',
            params={'limit': limit},
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        jobs = response.json().get('jobs', [])
        results = []
        for job in jobs:
            result = self.process_job(job)
            self.session.post(
                f'{self.bot_url}/eagle/jobs/{job["job_id"]}/result',
                headers=headers,
                json=result.to_dict(),
                timeout=15,
            ).raise_for_status()
            results.append(result)
        return results


def main(argv=None):
    parser = argparse.ArgumentParser(description='Scrapbox Eagle local bridge')
    parser.add_argument('--bot-url', default=os.environ.get('EAGLE_BOT_URL', ''))
    parser.add_argument('--token', default=os.environ.get('EAGLE_BRIDGE_TOKEN', ''))
    parser.add_argument('--download-dir', default=os.environ.get('EAGLE_DOWNLOAD_DIR', ''))
    parser.add_argument('--manifest', default=os.environ.get('EAGLE_MANIFEST', ''))
    parser.add_argument('--poll-seconds', type=int, default=30)
    args = parser.parse_args(argv)
    if not args.bot_url or not args.token:
        parser.error('--bot-url and --token are required')

    download_dir = Path(args.download_dir or Path.home() / 'eagle-bridge-downloads')
    manifest = BridgeManifest(args.manifest or download_dir / 'manifest.json')
    bridge = EagleBridge(
        bot_url=args.bot_url,
        token=args.token,
        download_dir=download_dir,
        manifest=manifest,
    )
    while True:
        try:
            bridge.poll_once()
        except Exception as error:
            print(f'bridge error: {error}')
        time.sleep(max(5, args.poll_seconds))


if __name__ == '__main__':
    main()
