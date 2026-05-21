"""
Unit tests for the pure-logic functions in the backend services.
No API keys or network access required.

Run with:  python -m pytest backend/tests/test_units.py -v
       or:  python -m unittest backend/tests/test_units.py -v
"""
import sys
import os
import tempfile
import unittest
from pathlib import Path
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
        mock_col.query.return_value = {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

        import backend.services.ingestion_service as ing
        with patch.object(ing, "get_collection", return_value=mock_col):
            result = ing.search_chunks([0.1] * 768, video_id="abc12345678")

        self.assertEqual(result, [])
        mock_col.query.assert_called_once()

    def test_caps_n_results_at_collection_size(self):
        # ChromaDB returns fewer results than k when not enough docs match.
        # search_chunks passes k directly; no pre-count needed.
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "documents": [["chunk a", "chunk b"]],
            "metadatas": [[{"video_id": "x", "title": "T", "chunk_index": 0}, {"video_id": "x", "title": "T", "chunk_index": 1}]],
            "distances": [[0.1, 0.2]],
        }

        import backend.services.ingestion_service as ing
        with patch.object(ing, "get_collection", return_value=mock_col):
            result = ing.search_chunks([0.1] * 768, video_id="x", k=4)

        # n_results is always k; ChromaDB returns however many it has
        call_kwargs = mock_col.query.call_args[1]
        self.assertEqual(call_kwargs["n_results"], 4)
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


class TestCacheService(unittest.TestCase):
    """save/load, transcript stripping on embed, and failed-state tracking."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        import backend.services.cache_service as cs
        self.cs = cs
        self._patcher = patch.object(cs, "CACHE_DIR", Path(self.tmpdir.name))
        self._patcher.start()
        cs._index.clear()

    def tearDown(self):
        self._patcher.stop()
        self.cs._index.clear()
        self.tmpdir.cleanup()

    def _meta(self, vid="vid1"):
        return {"video_id": vid, "title": "T", "creator": "C", "engagement_rate": 1.0}

    def test_save_load_roundtrip(self):
        self.cs.save("vid1", self._meta(), "the transcript")
        entry = self.cs.load("vid1")
        self.assertEqual(entry["transcript"], "the transcript")
        self.assertEqual(entry["metadata"]["title"], "T")

    def test_has_metadata_after_save(self):
        self.assertFalse(self.cs.has_metadata("vid1"))
        self.cs.save("vid1", self._meta(), "text")
        self.assertTrue(self.cs.has_metadata("vid1"))

    def test_is_embedded_starts_false(self):
        self.cs.save("vid1", self._meta(), "text")
        self.assertFalse(self.cs.is_embedded("vid1"))

    def test_mark_embedded_sets_flag(self):
        self.cs.save("vid1", self._meta(), "text")
        self.cs.mark_embedded("vid1")
        self.assertTrue(self.cs.is_embedded("vid1"))

    def test_mark_embedded_strips_transcript_from_disk(self):
        self.cs.save("vid1", self._meta(), "the raw transcript")
        self.cs.mark_embedded("vid1")
        entry = self.cs.load("vid1")
        self.assertNotIn("transcript", entry)
        self.assertIn("metadata", entry)

    def test_mark_embedded_clears_failed_flag(self):
        self.cs.save("vid1", self._meta(), "text")
        self.cs.mark_failed("vid1")
        self.assertTrue(self.cs.is_failed("vid1"))
        self.cs.mark_embedded("vid1")
        self.assertFalse(self.cs.is_failed("vid1"))

    def test_mark_failed_then_is_failed(self):
        self.cs.save("vid1", self._meta(), "text")
        self.assertFalse(self.cs.is_failed("vid1"))
        self.cs.mark_failed("vid1")
        self.assertTrue(self.cs.is_failed("vid1"))

    def test_is_failed_unknown_video(self):
        self.assertFalse(self.cs.is_failed("no-such-id"))

    def test_is_embedded_unknown_video(self):
        self.assertFalse(self.cs.is_embedded("no-such-id"))

    def test_delete_removes_entry_and_file(self):
        self.cs.save("vid1", self._meta(), "text")
        self.cs.delete("vid1")
        self.assertFalse(self.cs.has_metadata("vid1"))
        self.assertIsNone(self.cs.load("vid1"))

    def test_multiple_videos_tracked_independently(self):
        self.cs.save("a", self._meta("a"), "ta")
        self.cs.save("b", self._meta("b"), "tb")
        self.cs.mark_embedded("a")
        self.assertTrue(self.cs.is_embedded("a"))
        self.assertFalse(self.cs.is_embedded("b"))

    def test_mark_failed_does_not_affect_other_videos(self):
        self.cs.save("a", self._meta("a"), "ta")
        self.cs.save("b", self._meta("b"), "tb")
        self.cs.mark_failed("a")
        self.assertTrue(self.cs.is_failed("a"))
        self.assertFalse(self.cs.is_failed("b"))


class TestNormalizeUrl(unittest.TestCase):
    def setUp(self):
        import backend.main as bm
        self._fn = bm._normalize_url

    def test_valid_https_passthrough(self):
        url = "https://www.youtube.com/watch?v=abc"
        self.assertEqual(self._fn(url), url)

    def test_valid_http_passthrough(self):
        url = "http://www.youtube.com/watch?v=abc"
        self.assertEqual(self._fn(url), url)

    def test_strips_leading_and_trailing_whitespace(self):
        self.assertEqual(self._fn("  https://youtu.be/abc  "), "https://youtu.be/abc")

    def test_mangled_scheme_corrected(self):
        result = self._fn("htttps://www.youtube.com/watch?v=abc")
        self.assertEqual(result, "https://www.youtube.com/watch?v=abc")

    def test_htp_scheme_corrected(self):
        result = self._fn("htp://www.youtube.com/watch?v=abc")
        self.assertEqual(result, "https://www.youtube.com/watch?v=abc")

    def test_path_preserved_after_scheme_correction(self):
        result = self._fn("htttps://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertIn("dQw4w9WgXcQ", result)


class TestEmbedVideoBackground(unittest.TestCase):
    """_embed_video must call mark_failed on every failure path and mark_embedded on success."""

    def setUp(self):
        import backend.main as bm
        self.bm = bm

    def test_no_action_when_cache_entry_missing(self):
        with patch.object(self.bm.cache_service, "load", return_value=None), \
             patch.object(self.bm.cache_service, "mark_failed") as mf, \
             patch.object(self.bm.cache_service, "mark_embedded") as me:
            self.bm._embed_video("vid1")
            mf.assert_not_called()
            me.assert_not_called()

    def test_marks_failed_when_transcript_missing_from_cache(self):
        entry = {"metadata": {"video_id": "vid1", "title": "T"}}
        with patch.object(self.bm.cache_service, "load", return_value=entry), \
             patch.object(self.bm.cache_service, "mark_failed") as mf:
            self.bm._embed_video("vid1")
            mf.assert_called_once_with("vid1")

    def test_marks_failed_when_transcript_is_empty_string(self):
        entry = {"metadata": {"video_id": "vid1", "title": "T"}, "transcript": ""}
        with patch.object(self.bm.cache_service, "load", return_value=entry), \
             patch.object(self.bm.cache_service, "mark_failed") as mf:
            self.bm._embed_video("vid1")
            mf.assert_called_once_with("vid1")

    def test_marks_embedded_on_success(self):
        entry = {"metadata": {"video_id": "vid1", "title": "T"}, "transcript": "some text"}
        chunks = [{"id": "c1", "text": "t", "metadata": {}}]
        with patch.object(self.bm.cache_service, "load", return_value=entry), \
             patch.object(self.bm, "chunk_transcript", return_value=chunks), \
             patch.object(self.bm, "store_chunks"), \
             patch.object(self.bm.cache_service, "mark_embedded") as me:
            self.bm._embed_video("vid1")
            me.assert_called_once_with("vid1")

    def test_marks_failed_when_store_chunks_raises(self):
        entry = {"metadata": {"video_id": "vid1", "title": "T"}, "transcript": "text"}
        chunks = [{"id": "c1", "text": "t", "metadata": {}}]
        with patch.object(self.bm.cache_service, "load", return_value=entry), \
             patch.object(self.bm, "chunk_transcript", return_value=chunks), \
             patch.object(self.bm, "store_chunks", side_effect=RuntimeError("API down")), \
             patch.object(self.bm.cache_service, "mark_failed") as mf:
            self.bm._embed_video("vid1")
            mf.assert_called_once_with("vid1")

    def test_mark_embedded_not_called_on_failure(self):
        entry = {"metadata": {"video_id": "vid1", "title": "T"}, "transcript": "text"}
        chunks = [{"id": "c1", "text": "t", "metadata": {}}]
        with patch.object(self.bm.cache_service, "load", return_value=entry), \
             patch.object(self.bm, "chunk_transcript", return_value=chunks), \
             patch.object(self.bm, "store_chunks", side_effect=RuntimeError("fail")), \
             patch.object(self.bm.cache_service, "mark_embedded") as me, \
             patch.object(self.bm.cache_service, "mark_failed"):
            self.bm._embed_video("vid1")
            me.assert_not_called()


class TestIngestStatus(unittest.TestCase):
    """ingest_status returns failed > ready > indexing, handles multiple IDs and edge cases."""

    def setUp(self):
        import backend.main as bm
        self.fn = bm.ingest_status

    def test_failed_when_is_failed_true(self):
        with patch("backend.main.cache_service.is_failed", return_value=True), \
             patch("backend.main.cache_service.is_embedded", return_value=False):
            self.assertEqual(self.fn("vid1")["vid1"], "failed")

    def test_failed_takes_priority_over_embedded(self):
        with patch("backend.main.cache_service.is_failed", return_value=True), \
             patch("backend.main.cache_service.is_embedded", return_value=True):
            self.assertEqual(self.fn("vid1")["vid1"], "failed")

    def test_ready_when_embedded_and_not_failed(self):
        with patch("backend.main.cache_service.is_failed", return_value=False), \
             patch("backend.main.cache_service.is_embedded", return_value=True):
            self.assertEqual(self.fn("vid1")["vid1"], "ready")

    def test_indexing_when_neither_failed_nor_embedded(self):
        with patch("backend.main.cache_service.is_failed", return_value=False), \
             patch("backend.main.cache_service.is_embedded", return_value=False):
            self.assertEqual(self.fn("vid1")["vid1"], "indexing")

    def test_multiple_ids_with_different_states(self):
        def is_failed(vid):
            return vid == "f"
        def is_embedded(vid):
            return vid == "r"
        with patch("backend.main.cache_service.is_failed", side_effect=is_failed), \
             patch("backend.main.cache_service.is_embedded", side_effect=is_embedded):
            result = self.fn("f,r,i")
            self.assertEqual(result["f"], "failed")
            self.assertEqual(result["r"], "ready")
            self.assertEqual(result["i"], "indexing")

    def test_empty_input_returns_empty_dict(self):
        result = self.fn("")
        self.assertEqual(result, {})

    def test_whitespace_around_ids_is_stripped(self):
        with patch("backend.main.cache_service.is_failed", return_value=False), \
             patch("backend.main.cache_service.is_embedded", return_value=True):
            result = self.fn(" vid1 , vid2 ")
            self.assertIn("vid1", result)
            self.assertIn("vid2", result)


class TestVideoMetadataSchema(unittest.TestCase):
    """VideoMetadata pydantic schema: source_url field and nullable fields."""

    def setUp(self):
        from backend.models.schemas import VideoMetadata
        self.VM = VideoMetadata
        self._base = dict(
            video_id="abc123",
            title="Title",
            creator="Creator",
            upload_date="2024-01-01",
            duration_seconds=120,
            views=1000,
            likes=50,
            comments=10,
            hashtags=[],
            thumbnail_url="https://img.youtube.com/vi/abc123/0.jpg",
            engagement_rate=5.0,
            subscriber_count=10000,
        )

    def test_source_url_defaults_to_empty_string(self):
        m = self.VM(**self._base)
        self.assertEqual(m.source_url, "")

    def test_source_url_accepts_youtube_url(self):
        m = self.VM(**self._base, source_url="https://www.youtube.com/watch?v=abc123")
        self.assertEqual(m.source_url, "https://www.youtube.com/watch?v=abc123")

    def test_source_url_accepts_instagram_url(self):
        m = self.VM(**self._base, source_url="https://www.instagram.com/reel/abc123/")
        self.assertEqual(m.source_url, "https://www.instagram.com/reel/abc123/")

    def test_nullable_views_likes_comments(self):
        data = {**self._base, "views": None, "likes": None, "comments": None}
        m = self.VM(**data)
        self.assertIsNone(m.views)
        self.assertIsNone(m.likes)
        self.assertIsNone(m.comments)

    def test_nullable_subscriber_count(self):
        m = self.VM(**{**self._base, "subscriber_count": None})
        self.assertIsNone(m.subscriber_count)

    def test_required_fields_are_set(self):
        m = self.VM(**self._base)
        self.assertEqual(m.video_id, "abc123")
        self.assertEqual(m.title, "Title")
        self.assertEqual(m.engagement_rate, 5.0)


class TestRetrieveContext(unittest.TestCase):
    """retrieve_context: context format, source structure, empty case."""

    def setUp(self):
        import backend.services.rag_service as rs
        self.rs = rs
        self.retrieve = rs.retrieve_context

    def _chunk(self, text, idx=0, total=5):
        return {
            "text": text,
            "metadata": {
                "video_id": "vid1",
                "title": "Test Video",
                "creator": "Channel",
                "engagement_rate": 3.5,
                "chunk_index": idx,
                "total_chunks": total,
            },
            "distance": round(0.1 + idx * 0.01, 3),
        }

    def test_empty_results_returns_empty_context_and_no_sources(self):
        with patch.object(self.rs, "search_chunks", return_value=[]):
            context, sources = self.retrieve("vid1", [0.1] * 768)
        self.assertEqual(context, "")
        self.assertEqual(sources, [])

    def test_context_contains_video_title_and_creator(self):
        with patch.object(self.rs, "search_chunks", return_value=[self._chunk("text")]):
            context, _ = self.retrieve("vid1", [0.1] * 768)
        self.assertIn("Test Video", context)
        self.assertIn("Channel", context)

    def test_context_contains_engagement_rate(self):
        with patch.object(self.rs, "search_chunks", return_value=[self._chunk("text")]):
            context, _ = self.retrieve("vid1", [0.1] * 768)
        self.assertIn("3.5", context)

    def test_context_contains_chunk_text(self):
        with patch.object(self.rs, "search_chunks", return_value=[self._chunk("specific content here")]):
            context, _ = self.retrieve("vid1", [0.1] * 768)
        self.assertIn("specific content here", context)

    def test_sources_have_required_fields(self):
        with patch.object(self.rs, "search_chunks", return_value=[self._chunk("t", idx=2)]):
            _, sources = self.retrieve("vid1", [0.1] * 768)
        self.assertEqual(len(sources), 1)
        s = sources[0]
        self.assertEqual(s["video_id"], "vid1")
        self.assertEqual(s["title"], "Test Video")
        self.assertEqual(s["chunk_index"], 2)
        self.assertIn("distance", s)

    def test_distance_is_rounded(self):
        chunk = self._chunk("t")
        chunk["distance"] = 0.123456789
        with patch.object(self.rs, "search_chunks", return_value=[chunk]):
            _, sources = self.retrieve("vid1", [0.1] * 768)
        self.assertEqual(sources[0]["distance"], round(0.123456789, 4))

    def test_multiple_chunks_all_appear_in_context(self):
        chunks = [self._chunk(f"chunk {i}", i) for i in range(3)]
        with patch.object(self.rs, "search_chunks", return_value=chunks):
            context, sources = self.retrieve("vid1", [0.1] * 768)
        for i in range(3):
            self.assertIn(f"chunk {i}", context)
        self.assertEqual(len(sources), 3)

    def test_search_chunks_called_with_correct_video_id(self):
        with patch.object(self.rs, "search_chunks", return_value=[]) as mock_search:
            self.retrieve("target_vid", [0.5] * 768)
        call_kwargs = mock_search.call_args[1]
        self.assertEqual(call_kwargs["video_id"], "target_vid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
