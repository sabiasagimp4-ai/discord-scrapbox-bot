import unittest
from unittest.mock import patch

import gyazo_uploader


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b''):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.content = content

    def json(self):
        return self._json_data


class UploadThumbnailTests(unittest.TestCase):
    def test_no_access_token_returns_empty_without_network(self):
        with patch.object(gyazo_uploader, 'GYAZO_ACCESS_TOKEN', ''):
            with patch('gyazo_uploader.requests.get') as mock_get:
                with patch('gyazo_uploader.requests.post') as mock_post:
                    result = gyazo_uploader.upload_thumbnail('https://example.com/image.jpg')
        mock_get.assert_not_called()
        mock_post.assert_not_called()
        self.assertEqual(result, '')

    def test_no_image_url_returns_empty_without_network(self):
        with patch.object(gyazo_uploader, 'GYAZO_ACCESS_TOKEN', 'token'):
            with patch('gyazo_uploader.requests.get') as mock_get:
                result = gyazo_uploader.upload_thumbnail('')
        mock_get.assert_not_called()
        self.assertEqual(result, '')

    def test_successful_upload_returns_gyazo_url(self):
        with patch.object(gyazo_uploader, 'GYAZO_ACCESS_TOKEN', 'token'):
            with patch('gyazo_uploader.requests.get', return_value=FakeResponse(200, content=b'fake-bytes')):
                with patch(
                    'gyazo_uploader.requests.post',
                    return_value=FakeResponse(200, {'url': 'https://i.gyazo.com/abc.jpg'}),
                ) as mock_post:
                    result = gyazo_uploader.upload_thumbnail('https://example.com/image.jpg')
        self.assertEqual(result, 'https://i.gyazo.com/abc.jpg')
        mock_post.assert_called_once()

    def test_image_download_failure_returns_empty(self):
        with patch.object(gyazo_uploader, 'GYAZO_ACCESS_TOKEN', 'token'):
            with patch('gyazo_uploader.requests.get', return_value=FakeResponse(404)):
                with patch('gyazo_uploader.requests.post') as mock_post:
                    result = gyazo_uploader.upload_thumbnail('https://example.com/image.jpg')
        mock_post.assert_not_called()
        self.assertEqual(result, '')

    def test_upload_api_failure_returns_empty(self):
        with patch.object(gyazo_uploader, 'GYAZO_ACCESS_TOKEN', 'token'):
            with patch('gyazo_uploader.requests.get', return_value=FakeResponse(200, content=b'fake-bytes')):
                with patch('gyazo_uploader.requests.post', return_value=FakeResponse(401)):
                    result = gyazo_uploader.upload_thumbnail('https://example.com/image.jpg')
        self.assertEqual(result, '')

    def test_request_exception_returns_empty(self):
        with patch.object(gyazo_uploader, 'GYAZO_ACCESS_TOKEN', 'token'):
            with patch('gyazo_uploader.requests.get', side_effect=Exception('network error')):
                result = gyazo_uploader.upload_thumbnail('https://example.com/image.jpg')
        self.assertEqual(result, '')


if __name__ == '__main__':
    unittest.main()
