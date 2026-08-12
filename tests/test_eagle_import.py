import unittest

from eagle_import import (
    EagleImportStore,
    canonicalize_video_url,
    collect_video_sources,
    extract_video_urls,
)


class VideoUrlExtractionTests(unittest.TestCase):
    def test_extract_video_urls_finds_plain_markdown_and_scrapbox_urls(self):
        text = "動画 https://youtu.be/abc123。 [https://vimeo.com/42 clip]"

        self.assertEqual(
            extract_video_urls(text),
            ["https://youtu.be/abc123", "https://vimeo.com/42"],
        )


class VideoUrlCanonicalizationTests(unittest.TestCase):
    def test_canonicalize_video_url_removes_tracking_fragment_only(self):
        self.assertEqual(
            canonicalize_video_url(
                "https://www.youtube.com/watch?v=abc123&si=tracking#live"
            ),
            "https://youtu.be/abc123",
        )

    def test_canonicalize_video_url_rejects_non_http_url(self):
        self.assertIsNone(canonicalize_video_url("not-a-url"))


class VideoSourceCollectionTests(unittest.TestCase):
    def test_collect_video_sources_deduplicates_url_and_keeps_all_sources(self):
        pages = [
            {
                "title": "A",
                "url": "https://scrapbox.io/p/A",
                "lines": ["https://youtu.be/x"],
            },
            {
                "title": "B",
                "url": "https://scrapbox.io/p/B",
                "lines": ["https://www.youtube.com/watch?v=x"],
            },
        ]

        result = collect_video_sources(pages)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].canonical_url, "https://youtu.be/x")
        self.assertEqual([source.page_title for source in result[0].sources], ["A", "B"])


class ImportStoreTests(unittest.TestCase):
    def test_create_preview_scans_all_pages_and_counts_fetch_failures(self):
        pages = {"A": ["A", "https://youtu.be/x"], "B": None}
        store = EagleImportStore()

        preview = store.create_preview(
            ["A", "B"], lambda title: pages[title], "https://scrapbox.io/proj"
        )

        self.assertEqual(preview.page_count, 2)
        self.assertEqual(preview.video_count, 1)
        self.assertEqual(preview.failed_page_count, 1)
        self.assertEqual(store.status()["pending"], 0)

    def test_confirm_creates_pending_jobs_and_claim_moves_them_to_running(self):
        store = EagleImportStore()
        preview = store.create_preview(
            ["A"], lambda title: [title, "https://youtu.be/x"], "https://scrapbox.io/proj"
        )

        jobs = store.confirm(preview.preview_id)
        self.assertEqual(jobs[0].status, "pending")
        claimed = store.claim(limit=1)

        self.assertEqual(len(jobs), 1)
        self.assertEqual(claimed[0].status, "running")
        self.assertEqual(store.status()["running"], 1)

    def test_complete_is_idempotent_for_finished_job(self):
        store = EagleImportStore()
        preview = store.create_preview(
            ["A"], lambda title: [title, "https://youtu.be/x"], "https://scrapbox.io/proj"
        )
        job = store.confirm(preview.preview_id)[0]
        store.claim(limit=1)

        self.assertTrue(store.complete(job.job_id, {"title": "video"}))
        self.assertFalse(store.complete(job.job_id, {"title": "changed"}))
        self.assertEqual(store.status()["succeeded"], 1)


if __name__ == "__main__":
    unittest.main()
