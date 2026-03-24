import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tokenbar


VALID_ENTRY = {
    "type": "assistant",
    "timestamp": "2026-03-24T12:00:00Z",
    "message": {
        "model": "claude-opus-4-6",
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 100,
        },
    },
}


def make_entry(ts, model="claude-opus-4-6", output=500):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": output,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 100,
            },
        },
    }


class TestFmtTokens(unittest.TestCase):
    def test_small(self):
        self.assertEqual(tokenbar.fmt_tokens(0), "0")
        self.assertEqual(tokenbar.fmt_tokens(999), "999")

    def test_thousands(self):
        self.assertEqual(tokenbar.fmt_tokens(1000), "1K")
        self.assertEqual(tokenbar.fmt_tokens(1500), "2K")
        self.assertEqual(tokenbar.fmt_tokens(999_999), "1000K")

    def test_millions(self):
        self.assertEqual(tokenbar.fmt_tokens(1_000_000), "1.0M")
        self.assertEqual(tokenbar.fmt_tokens(2_500_000), "2.5M")


class TestShortModel(unittest.TestCase):
    def test_known_models(self):
        self.assertEqual(tokenbar.short_model("claude-opus-4-6"), "Opus 4.6")
        self.assertEqual(tokenbar.short_model("claude-sonnet-4-6"), "Sonnet 4.6")
        self.assertEqual(tokenbar.short_model("claude-haiku-4-5-20251001"), "Haiku 4.5")

    def test_unknown_model(self):
        result = tokenbar.short_model("some-unknown-model-name")
        self.assertEqual(len(result), 16)


class TestAddMessageTo(unittest.TestCase):
    def test_basic(self):
        bucket = tokenbar.empty_usage()
        usage = VALID_ENTRY["message"]["usage"]
        tokenbar.add_message_to(bucket, usage, "claude-opus-4-6")

        self.assertEqual(bucket["messages"], 1)
        self.assertEqual(bucket["input"], 1000)
        self.assertEqual(bucket["output"], 500)
        self.assertEqual(bucket["cache_read"], 200)
        self.assertEqual(bucket["cache_create"], 100)
        self.assertIn("Opus 4.6", bucket["by_model"])

    def test_cost_calculation(self):
        bucket = tokenbar.empty_usage()
        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        tokenbar.add_message_to(bucket, usage, "claude-opus-4-6")
        self.assertAlmostEqual(bucket["cost"], 3.00)

    def test_multiple_messages(self):
        bucket = tokenbar.empty_usage()
        usage = VALID_ENTRY["message"]["usage"]
        tokenbar.add_message_to(bucket, usage, "claude-opus-4-6")
        tokenbar.add_message_to(bucket, usage, "claude-sonnet-4-6")

        self.assertEqual(bucket["messages"], 2)
        self.assertEqual(bucket["input"], 2000)
        self.assertIn("Opus 4.6", bucket["by_model"])
        self.assertIn("Sonnet 4.6", bucket["by_model"])

    def test_empty_usage(self):
        bucket = tokenbar.empty_usage()
        tokenbar.add_message_to(bucket, {}, "claude-opus-4-6")
        self.assertEqual(bucket["messages"], 1)
        self.assertEqual(bucket["cost"], 0.0)


class TestValidateJsonlEntry(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(tokenbar._validate_jsonl_entry(VALID_ENTRY))

    def test_wrong_type(self):
        self.assertFalse(tokenbar._validate_jsonl_entry({"type": "user", "timestamp": "2026-01-01", "message": {"usage": {}}}))

    def test_no_timestamp(self):
        self.assertFalse(tokenbar._validate_jsonl_entry({"type": "assistant", "message": {"usage": {}}}))

    def test_no_message(self):
        self.assertFalse(tokenbar._validate_jsonl_entry({"type": "assistant", "timestamp": "2026-01-01"}))

    def test_message_not_dict(self):
        self.assertFalse(tokenbar._validate_jsonl_entry({"type": "assistant", "timestamp": "2026-01-01", "message": "string"}))

    def test_not_dict(self):
        self.assertFalse(tokenbar._validate_jsonl_entry("string"))
        self.assertFalse(tokenbar._validate_jsonl_entry(42))
        self.assertFalse(tokenbar._validate_jsonl_entry(None))


class TestValidateLiveResponse(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(tokenbar._validate_live_response({"five_hour": {}, "seven_day": {}}))

    def test_partial(self):
        self.assertTrue(tokenbar._validate_live_response({"five_hour": {}}))

    def test_empty(self):
        self.assertFalse(tokenbar._validate_live_response({}))

    def test_not_dict(self):
        self.assertFalse(tokenbar._validate_live_response("error"))


class TestParseWithFixtures(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_dir = os.path.join(self.tmpdir, "projects", "test-project")
        os.makedirs(self.project_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _write_jsonl(self, filename, entries):
        path = os.path.join(self.project_dir, filename)
        with open(path, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return path

    def test_parse_sessions_normal(self):
        now = datetime.now(timezone.utc)
        entries = [
            make_entry(now.isoformat(), "claude-opus-4-6", 500),
            make_entry((now - timedelta(hours=2)).isoformat(), "claude-sonnet-4-6", 300),
        ]
        self._write_jsonl("session.jsonl", entries)

        with patch.object(tokenbar, "CLAUDE_DIR", self.tmpdir):
            result = tokenbar.parse_sessions()

        self.assertEqual(result["today"]["messages"], 2)
        self.assertEqual(result["today"]["output"], 800)
        self.assertEqual(result["7d"]["messages"], 2)

    def test_parse_sessions_corrupted_line(self):
        now = datetime.now(timezone.utc)
        entries = [make_entry(now.isoformat())]
        path = os.path.join(self.project_dir, "session.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(entries[0]) + "\n")
            f.write("NOT VALID JSON\n")
            f.write("{}\n")

        with patch.object(tokenbar, "CLAUDE_DIR", self.tmpdir):
            result = tokenbar.parse_sessions()

        self.assertEqual(result["today"]["messages"], 1)

    def test_parse_sessions_empty_file(self):
        self._write_jsonl("empty.jsonl", [])

        with patch.object(tokenbar, "CLAUDE_DIR", self.tmpdir):
            result = tokenbar.parse_sessions()

        self.assertEqual(result["today"]["messages"], 0)

    def test_parse_sessions_old_entries_excluded(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        entries = [make_entry(old_ts)]
        self._write_jsonl("old.jsonl", entries)

        with patch.object(tokenbar, "CLAUDE_DIR", self.tmpdir):
            result = tokenbar.parse_sessions()

        self.assertEqual(result["today"]["messages"], 0)

    def test_parse_history_normal(self):
        entries = [
            make_entry("2026-03-01T10:00:00Z", "claude-opus-4-6", 1000),
            make_entry("2026-02-01T10:00:00Z", "claude-sonnet-4-6", 2000),
        ]
        self._write_jsonl("history.jsonl", entries)

        with patch.object(tokenbar, "CLAUDE_DIR", self.tmpdir):
            result = tokenbar.parse_history()

        self.assertEqual(result["msgs"], 2)
        self.assertEqual(result["out"], 3000)
        self.assertGreater(result["cost"], 0)

    def test_parse_history_before_cutoff(self):
        entries = [make_entry("2025-12-01T10:00:00Z")]
        self._write_jsonl("old.jsonl", entries)

        with patch.object(tokenbar, "CLAUDE_DIR", self.tmpdir):
            result = tokenbar.parse_history()

        self.assertEqual(result["msgs"], 0)


class TestGetOAuthToken(unittest.TestCase):
    @patch("tokenbar.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"claudeAiOauth":{"accessToken":"sk-ant-test-token"}}'
        )
        token = tokenbar.get_oauth_token()
        self.assertEqual(token, "sk-ant-test-token")

    @patch("tokenbar.subprocess.run")
    def test_keychain_not_found(self, mock_run):
        mock_run.return_value = MagicMock(returncode=44, stdout="")
        token = tokenbar.get_oauth_token()
        self.assertIsNone(token)

    @patch("tokenbar.subprocess.run")
    def test_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        token = tokenbar.get_oauth_token()
        self.assertIsNone(token)


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache_file = os.path.join(self.tmpdir, "cache.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_load_empty(self):
        with patch.object(tokenbar, "CACHE_FILE", os.path.join(self.tmpdir, "nope.json")):
            result = tokenbar.load_cache()
        self.assertEqual(result, {})

    def test_save_and_load(self):
        with patch.object(tokenbar, "CACHE_FILE", self.cache_file), \
             patch.object(tokenbar, "CACHE_DIR", self.tmpdir):
            tokenbar.save_cache({"key": "value"})
            result = tokenbar.load_cache()
        self.assertEqual(result["key"], "value")


if __name__ == "__main__":
    unittest.main()
