import re

import requests

PLAYLIST_ITEM_LIMIT = 50


def extract_playlist_id(url):
    match = re.search(r'[?&]list=([A-Za-z0-9_-]+)', url)
    return match.group(1) if match else None


def fetch_playlist_video_urls(playlist_id, api_key, max_videos=PLAYLIST_ITEM_LIMIT):
    """プレイリスト内の動画URLを順番に取得する（重複動画は除外）。api_key未設定時や取得失敗時は空リスト。"""
    if not api_key:
        return []

    video_ids = []
    seen = set()
    page_token = ''
    while len(video_ids) < max_videos:
        try:
            r = requests.get(
                'https://www.googleapis.com/youtube/v3/playlistItems',
                params={
                    'part': 'contentDetails',
                    'playlistId': playlist_id,
                    'maxResults': 50,
                    'pageToken': page_token,
                    'key': api_key,
                },
                timeout=5,
            )
        except Exception:
            break
        if r.status_code != 200:
            break

        data = r.json()
        for item in data.get('items', []):
            video_id = item.get('contentDetails', {}).get('videoId')
            if video_id and video_id not in seen:
                seen.add(video_id)
                video_ids.append(video_id)
                if len(video_ids) >= max_videos:
                    break

        page_token = data.get('nextPageToken', '')
        if not page_token:
            break

    return [f'https://www.youtube.com/watch?v={video_id}' for video_id in video_ids]
