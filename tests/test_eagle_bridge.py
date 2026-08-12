import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from eagle_bridge import BridgeManifest, EagleClient, EagleBridge


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.last_json = None

    def post(self, url, json, timeout):
        self.last_json = json
        return FakeResponse(self.payload)


class EagleClientTests(unittest.TestCase):
    def test_posts_local_file_and_metadata(self):
        session = FakeSession({'status': 'success', 'item': {'id': 'e1'}})
        client = EagleClient(session=session)

        result = client.add_from_path(
            'C:/tmp/video.mp4',
            'Title',
            'https://youtu.be/x',
            'Scrapbox: Page',
            ['video'],
        )

        self.assertEqual(result['item']['id'], 'e1')
        self.assertEqual(session.last_json['name'], 'Title')
        self.assertEqual(session.last_json['website'], 'https://youtu.be/x')


class BridgeManifestTests(unittest.TestCase):
    def test_records_success_and_loads_it_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            manifest = BridgeManifest(path)
            manifest.record_success('https://youtu.be/x', {'id': 'e1'})

            loaded = BridgeManifest(path)
            self.assertTrue(loaded.contains('https://youtu.be/x'))
            self.assertEqual(loaded.get('https://youtu.be/x')['id'], 'e1')


class EagleBridgeTests(unittest.TestCase):
    def test_skips_url_already_in_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = BridgeManifest(Path(directory) / 'manifest.json')
            manifest.record_success('https://youtu.be/x', {'id': 'e1'})
            downloader = MagicMock()
            bridge = EagleBridge(
                manifest=manifest,
                eagle_client=MagicMock(),
                downloader=downloader,
                download_dir=Path(directory),
            )

            result = bridge.process_job({'canonical_url': 'https://youtu.be/x'})

            self.assertEqual(result.status, 'skipped')
            downloader.assert_not_called()

    def test_downloads_then_registers_video_and_records_success(self):
        with tempfile.TemporaryDirectory() as directory:
            video_path = Path(directory) / 'video.mp4'
            video_path.write_bytes(b'video')
            manifest = BridgeManifest(Path(directory) / 'manifest.json')
            eagle_client = MagicMock()
            eagle_client.add_from_path.return_value = {'item': {'id': 'e1'}}
            downloader = MagicMock(return_value={'path': str(video_path), 'title': 'Video title'})
            bridge = EagleBridge(
                manifest=manifest,
                eagle_client=eagle_client,
                downloader=downloader,
                download_dir=Path(directory),
            )

            result = bridge.process_job({
                'job_id': 'j1',
                'canonical_url': 'https://youtu.be/x',
                'source_url': 'https://youtu.be/x',
                'page_title': 'Page',
                'page_url': 'https://scrapbox.io/proj/Page',
                'source_line': 'https://youtu.be/x',
            })

            self.assertEqual(result.status, 'succeeded')
            self.assertEqual(eagle_client.add_from_path.call_args.kwargs['website'], 'https://youtu.be/x')
            self.assertTrue(manifest.contains('https://youtu.be/x'))
            self.assertFalse(video_path.exists())


class PollingTests(unittest.TestCase):
    def test_reports_result_to_bot_after_processing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            session = MagicMock()
            session.get.return_value = FakeResponse({
                'jobs': [{
                    'job_id': 'j1',
                    'canonical_url': 'https://youtu.be/x',
                    'source_url': 'https://youtu.be/x',
                    'page_title': 'Page',
                    'page_url': 'https://scrapbox.io/proj/Page',
                    'source_line': 'https://youtu.be/x',
                }]
            })
            session.post.return_value = FakeResponse({'status': 'succeeded'})
            video_path = Path(directory) / 'video.mp4'
            video_path.write_bytes(b'video')
            eagle_client = MagicMock()
            eagle_client.add_from_path.return_value = {'item': {'id': 'e1'}}
            bridge = EagleBridge(
                bot_url='https://bot.example',
                token='secret',
                download_dir=Path(directory),
                manifest=BridgeManifest(Path(directory) / 'manifest.json'),
                eagle_client=eagle_client,
                downloader=MagicMock(return_value={'path': str(video_path), 'title': 'Video'}),
                session=session,
            )

            results = bridge.poll_once()

            self.assertEqual(results[0].status, 'succeeded')
            self.assertEqual(session.post.call_args.args[0], 'https://bot.example/eagle/jobs/j1/result')
            self.assertEqual(session.post.call_args.kwargs['json']['status'], 'succeeded')


if __name__ == '__main__':
    unittest.main()
