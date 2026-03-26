import json
import logging
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

    def test_synthetic_model(self):
        self.assertIsNone(tokenbar.short_model("<synthetic>"))
        self.assertIsNone(tokenbar.short_model("unknown"))
        self.assertIsNone(tokenbar.short_model(""))
        self.assertIsNone(tokenbar.short_model(None))


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
        bm = bucket["by_model"]["Opus 4.6"]
        self.assertEqual(bm["messages"], 1)
        self.assertEqual(bm["output"], 500)
        self.assertEqual(bm["input"], 1000)
        self.assertGreater(bm["cost"], 0)

    def test_cost_calculation(self):
        bucket = tokenbar.empty_usage()
        usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        tokenbar.add_message_to(bucket, usage, "claude-opus-4-6")
        self.assertAlmostEqual(bucket["cost"], 3.00)
        self.assertAlmostEqual(bucket["by_model"]["Opus 4.6"]["cost"], 3.00)

    def test_multiple_messages(self):
        bucket = tokenbar.empty_usage()
        usage = VALID_ENTRY["message"]["usage"]
        tokenbar.add_message_to(bucket, usage, "claude-opus-4-6")
        tokenbar.add_message_to(bucket, usage, "claude-sonnet-4-6")

        self.assertEqual(bucket["messages"], 2)
        self.assertEqual(bucket["input"], 2000)
        self.assertIn("Opus 4.6", bucket["by_model"])
        self.assertIn("Sonnet 4.6", bucket["by_model"])
        self.assertEqual(bucket["by_model"]["Opus 4.6"]["messages"], 1)
        self.assertEqual(bucket["by_model"]["Sonnet 4.6"]["messages"], 1)

    def test_empty_usage(self):
        bucket = tokenbar.empty_usage()
        tokenbar.add_message_to(bucket, {}, "claude-opus-4-6")
        self.assertEqual(bucket["messages"], 1)
        self.assertEqual(bucket["cost"], 0.0)
        self.assertEqual(bucket["by_model"]["Opus 4.6"]["cost"], 0.0)


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


class TestTokenRedactFilter(unittest.TestCase):
    def setUp(self):
        self.f = tokenbar.TokenRedactFilter()

    def _make_record(self, msg, args=None):
        return logging.LogRecord("test", logging.WARNING, "", 0, msg, args, None)

    def test_redacts_token_in_msg(self):
        record = self._make_record("token: sk-ant-abc123456789XYZDEFGHIJ")
        self.f.filter(record)
        self.assertIn("REDACTED", record.msg)
        self.assertNotIn("XYZDEFGHIJ", record.msg)

    def test_preserves_msg_without_token(self):
        record = self._make_record("normal log message")
        self.f.filter(record)
        self.assertEqual(record.msg, "normal log message")

    def test_redacts_token_in_args(self):
        record = self._make_record("got %s", ("sk-ant-abc123456789XYZDEFGHIJ",))
        self.f.filter(record)
        self.assertIn("REDACTED", record.args[0])


class TestScrubSecrets(unittest.TestCase):
    def test_removes_access_token(self):
        data = {"accessToken": "secret", "usage": 100}
        clean = tokenbar._scrub_secrets(data)
        self.assertNotIn("accessToken", clean)
        self.assertEqual(clean["usage"], 100)

    def test_removes_nested_tokens(self):
        data = {"inner": {"refreshToken": "bad", "data": 1}}
        clean = tokenbar._scrub_secrets(data)
        self.assertNotIn("refreshToken", clean["inner"])
        self.assertEqual(clean["inner"]["data"], 1)

    def test_handles_lists(self):
        data = [{"token": "secret", "ok": True}]
        clean = tokenbar._scrub_secrets(data)
        self.assertNotIn("token", clean[0])
        self.assertTrue(clean[0]["ok"])

    def test_cache_no_tokens(self):
        cache = {"last_live": {"five_hour": {"utilization": 0.5}, "accessToken": "x"}}
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "cache.json")
            with patch.object(tokenbar, "CACHE_FILE", cache_file), \
                 patch.object(tokenbar, "CACHE_DIR", tmpdir):
                tokenbar.save_cache(cache)
                loaded = tokenbar.load_cache()
        self.assertNotIn("accessToken", str(loaded))


class TestLoadConfig(unittest.TestCase):
    def test_defaults(self):
        with patch.object(tokenbar, "CONFIG_FILE", "/nonexistent/config.json"):
            config = tokenbar.load_config()
        self.assertEqual(config["refresh_interval"], 60)
        self.assertEqual(config["currency"], "$")
        self.assertEqual(config["menubar_display"], "percent")
        self.assertIn("input", config["pricing"])

    def test_user_override(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"currency": "€", "refresh_interval": 30}, f)
            path = f.name
        try:
            with patch.object(tokenbar, "CONFIG_FILE", path):
                config = tokenbar.load_config()
            self.assertEqual(config["currency"], "€")
            self.assertEqual(config["refresh_interval"], 30)
            self.assertIn("input", config["pricing"])
        finally:
            os.unlink(path)

    def test_pricing_merge(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"pricing": {"output": 20.0 / 1_000_000}}, f)
            path = f.name
        try:
            with patch.object(tokenbar, "CONFIG_FILE", path):
                config = tokenbar.load_config()
            self.assertEqual(config["pricing"]["output"], 20.0 / 1_000_000)
            self.assertEqual(config["pricing"]["input"], 3.0 / 1_000_000)
        finally:
            os.unlink(path)

    def test_invalid_config(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("NOT JSON")
            path = f.name
        try:
            with patch.object(tokenbar, "CONFIG_FILE", path):
                config = tokenbar.load_config()
            self.assertEqual(config["refresh_interval"], 60)
        finally:
            os.unlink(path)


class TestUtilizationHistory(unittest.TestCase):
    def test_adds_point(self):
        cache = {}
        tokenbar.update_utilization_history(cache, 50.0)
        self.assertEqual(len(cache["utilization_history"]), 1)
        self.assertEqual(cache["utilization_history"][0]["pct"], 50.0)

    def test_ignores_none(self):
        cache = {}
        tokenbar.update_utilization_history(cache, None)
        self.assertNotIn("utilization_history", cache)

    def test_detects_reset(self):
        cache = {"utilization_history": [
            {"ts": "2026-03-26T10:00:00+00:00", "pct": 80.0},
            {"ts": "2026-03-26T10:01:00+00:00", "pct": 85.0},
        ]}
        tokenbar.update_utilization_history(cache, 10.0)
        self.assertEqual(len(cache["utilization_history"]), 1)
        self.assertEqual(cache["utilization_history"][0]["pct"], 10.0)

    def test_caps_at_max(self):
        cache = {"utilization_history": [
            {"ts": f"2026-03-26T10:{i:02d}:00+00:00", "pct": float(i)}
            for i in range(60)
        ]}
        tokenbar.update_utilization_history(cache, 99.0)
        self.assertEqual(len(cache["utilization_history"]), 60)


class TestEstimateTimeRemaining(unittest.TestCase):
    def test_below_50_returns_none(self):
        self.assertIsNone(tokenbar.estimate_time_remaining({}, 40))

    def test_none_pct_returns_none(self):
        self.assertIsNone(tokenbar.estimate_time_remaining({}, None))

    def test_not_enough_data(self):
        cache = {"utilization_history": [{"ts": "2026-03-26T10:00:00+00:00", "pct": 60}]}
        self.assertIsNone(tokenbar.estimate_time_remaining(cache, 60))

    def test_positive_velocity(self):
        now = datetime.now(timezone.utc)
        cache = {"utilization_history": [
            {"ts": (now - timedelta(minutes=10)).isoformat(), "pct": 60.0},
            {"ts": now.isoformat(), "pct": 70.0},
        ]}
        result = tokenbar.estimate_time_remaining(cache, 70.0)
        self.assertIsNotNone(result)
        self.assertEqual(result, 30)

    def test_decreasing_velocity_returns_none(self):
        now = datetime.now(timezone.utc)
        cache = {"utilization_history": [
            {"ts": (now - timedelta(minutes=10)).isoformat(), "pct": 80.0},
            {"ts": now.isoformat(), "pct": 70.0},
        ]}
        self.assertIsNone(tokenbar.estimate_time_remaining(cache, 70.0))


class TestYesterdaySummary(unittest.TestCase):
    def test_first_run(self):
        cache = {}
        tokenbar.update_yesterday_summary(cache, {"output": 1000, "messages": 5, "cost": 1.0})
        self.assertIn("pending_yesterday", cache)
        self.assertIn("last_refresh_date", cache)
        self.assertNotIn("yesterday_summary", cache)

    def test_new_day_promotes_pending(self):
        cache = {
            "last_refresh_date": "2026-03-25",
            "pending_yesterday": {"date": "2026-03-25", "output": 5000, "messages": 20, "cost": 5.0},
        }
        tokenbar.update_yesterday_summary(cache, {"output": 100, "messages": 1, "cost": 0.1})
        self.assertEqual(cache["yesterday_summary"]["output"], 5000)

    def test_same_day_no_promote(self):
        today = datetime.now().strftime("%Y-%m-%d")
        cache = {
            "last_refresh_date": today,
            "pending_yesterday": {"date": today, "output": 1000, "messages": 5, "cost": 1.0},
        }
        tokenbar.update_yesterday_summary(cache, {"output": 2000, "messages": 10, "cost": 2.0})
        self.assertNotIn("yesterday_summary", cache)


class TestDayComparison(unittest.TestCase):
    def test_no_yesterday(self):
        self.assertIsNone(tokenbar.compute_day_comparison({}, {"output": 1000}))

    def test_yesterday_zero(self):
        cache = {"yesterday_summary": {"output": 0}}
        self.assertIsNone(tokenbar.compute_day_comparison(cache, {"output": 1000}))

    def test_positive_delta(self):
        cache = {"yesterday_summary": {"output": 1000}}
        self.assertEqual(tokenbar.compute_day_comparison(cache, {"output": 1500}), 50)

    def test_negative_delta(self):
        cache = {"yesterday_summary": {"output": 1000}}
        self.assertEqual(tokenbar.compute_day_comparison(cache, {"output": 800}), -20)


if __name__ == "__main__":
    unittest.main()
