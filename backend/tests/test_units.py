"""
Unit tests for the pure-logic functions in the backend services.
No API keys or network access required.

Run with:  python -m pytest backend/tests/test_units.py -v
       or:  python -m unittest backend/tests/test_units.py -v
"""
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestExtractVideoId(unittest.TestCase):
    def setUp(self):
        from backend.services.transcript_service import extract_video_id
        self.extract = extract_video_id

    def test_standard_watch_url(self):
        self.assertEqual(self.extract("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_watch_url_with_extra_params(self):
        self.assertEqual(self.extract("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42&list=PL"), "dQw4w9WgXcQ")

    def test_short_youtu_be_url(self):
        self.assertEqual(self.extract("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_short_url_with_si_param(self):
        self.assertEqual(self.extract("https://youtu.be/dQw4w9WgXcQ?si=abc123"), "dQw4w9WgXcQ")

    def test_shorts_url(self):
        self.assertEqual(self.extract("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_invalid_url_raises(self):
        with self.assertRaises(ValueError):
            self.extract("https://example.com/not-youtube")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            self.extract("")

    def test_random_string_raises(self):
        with self.assertRaises(ValueError):
            self.extract("not a url at all")

    def test_youtube_homepage_raises(self):
        with self.assertRaises(ValueError):
            self.extract("https://www.youtube.com/")


class TestChunkTranscript(unittest.TestCase):
    META = {
        "video_id": "abc12345678",
        "title": "Test Video",
        "creator": "Test Channel",
        "engagement_rate": 4.2,
    }

    def setUp(self):
        from backend.services.ingestion_service import chunk_transcript
        self.chunk = chunk_transcript

    def test_normal_transcript_produces_chunks(self):
        text = "word " * 600  # about 3000 chars, well above the 1000-char chunk size
        chunks = self.chunk(text, self.META)
        self.assertGreater(len(chunks), 1)

    def test_short_transcript_gives_one_chunk(self):
        chunks = self.chunk("A short transcript.", self.META)
        self.assertEqual(len(chunks), 1)

    def test_empty_transcript_gives_no_chunks(self):
        chunks = self.chunk("", self.META)
        self.assertEqual(chunks, [])

    def test_chunk_metadata_fields(self):
        chunks = self.chunk("Some text here.", self.META)
        self.assertEqual(len(chunks), 1)
        meta = chunks[0]["metadata"]
        self.assertEqual(meta["video_id"], "abc12345678")
        self.assertEqual(meta["title"], "Test Video")
        self.assertEqual(meta["creator"], "Test Channel")
        self.assertEqual(meta["engagement_rate"], 4.2)
        self.assertIn("chunk_index", meta)
        self.assertIn("total_chunks", meta)

    def test_chunk_indices_are_sequential(self):
        text = "word " * 600
        chunks = self.chunk(text, self.META)
        for i, c in enumerate(chunks):
            self.assertEqual(c["metadata"]["chunk_index"], i)

    def test_total_chunks_is_consistent(self):
        text = "word " * 600
        chunks = self.chunk(text, self.META)
        total = len(chunks)
        for c in chunks:
            self.assertEqual(c["metadata"]["total_chunks"], total)

    def test_each_chunk_has_unique_id(self):
        text = "word " * 600
        chunks = self.chunk(text, self.META)
        ids = [c["id"] for c in chunks]
        self.assertEqual(len(ids), len(set(ids)))

    def test_chunk_text_is_non_empty(self):
        text = "word " * 600
        chunks = self.chunk(text, self.META)
        for c in chunks:
            self.assertTrue(c["text"].strip())


class TestSearchChunksEmptyCollection(unittest.TestCase):
    def test_returns_empty_when_collection_is_empty(self):
        mock_col = MagicMock()
        mock_col.count.return_value = 0

        import backend.services.ingestion_service as ing
        with patch.object(ing, "get_collection", return_value=mock_col):
            result = ing.search_chunks([0.1] * 768, video_id="abc12345678")

        self.assertEqual(result, [])
        mock_col.query.assert_not_called()

    def test_caps_n_results_at_collection_size(self):
        mock_col = MagicMock()
        mock_col.count.return_value = 2
        mock_col.query.return_value = {
            "documents": [["chunk a", "chunk b"]],
            "metadatas": [[{"video_id": "x", "title": "T", "chunk_index": 0}, {"video_id": "x", "title": "T", "chunk_index": 1}]],
            "distances": [[0.1, 0.2]],
        }

        import backend.services.ingestion_service as ing
        with patch.object(ing, "get_collection", return_value=mock_col):
            result = ing.search_chunks([0.1] * 768, video_id="x", k=4)

        call_kwargs = mock_col.query.call_args[1]
        self.assertEqual(call_kwargs["n_results"], 2)
        self.assertEqual(len(result), 2)


class TestTranscriptCleaning(unittest.TestCase):
    """Verify that newlines in caption snippets are removed from the transcript."""

    def test_newlines_stripped_from_snippets(self):
        mock_snippet = MagicMock()
        mock_snippet.text = "line one\nline two"

        from unittest.mock import MagicMock as MM
        mock_api = MM()
        mock_api.fetch.return_value = [mock_snippet]

        import backend.services.transcript_service as ts
        with patch.object(ts, "YouTubeTranscriptApi", return_value=mock_api):
            result = ts.get_transcript("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        self.assertNotIn("\n", result)
        self.assertIn("line one", result)
        self.assertIn("line two", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
