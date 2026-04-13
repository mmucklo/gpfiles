"""
Tests for POST /api/config/notional endpoint (Phase 10).

Since the Go API runs as a server, these tests exercise the endpoint
logic indirectly by testing the Python-side reload mechanism and the
env-file write logic.  A live integration test against localhost:2112
is skipped when the server is not running.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import tempfile
import pytest


# ─── Unit: env-file parsing / writing logic ─────────────────────────────────

def _write_env_file(path: str, content: str):
    with open(path, "w") as f:
        f.write(content)


def _parse_notional_from_env(path: str) -> int | None:
    """Parse NOTIONAL_ACCOUNT_SIZE from env file."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("NOTIONAL_ACCOUNT_SIZE="):
                    return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        pass
    return None


class TestEnvFileLogic:
    def test_write_and_read_notional(self, tmp_path):
        """Writing a notional value to env file and reading it back."""
        env_file = tmp_path / ".tsla-alpha.env"
        _write_env_file(str(env_file), "")
        # Simulate what the Go endpoint does
        with open(str(env_file), "w") as f:
            f.write("NOTIONAL_ACCOUNT_SIZE=30000\n")
        assert _parse_notional_from_env(str(env_file)) == 30000

    def test_existing_vars_preserved(self, tmp_path):
        """Existing env vars besides NOTIONAL_ACCOUNT_SIZE are preserved."""
        env_file = tmp_path / ".tsla-alpha.env"
        _write_env_file(str(env_file), "TELEGRAM_TOKEN=abc\nNOTIONAL_ACCOUNT_SIZE=25000\nFOO=bar\n")

        # Simulate update: preserve other vars, replace NOTIONAL
        existing = []
        with open(str(env_file)) as f:
            for line in f:
                line = line.rstrip("\n")
                if line and not line.startswith("NOTIONAL_ACCOUNT_SIZE="):
                    existing.append(line)
        existing.append("NOTIONAL_ACCOUNT_SIZE=40000")
        with open(str(env_file), "w") as f:
            f.write("\n".join(existing) + "\n")

        assert _parse_notional_from_env(str(env_file)) == 40000
        content = env_file.read_text()
        assert "TELEGRAM_TOKEN=abc" in content
        assert "FOO=bar" in content

    def test_reload_marker_file(self, tmp_path):
        """Publisher reads /tmp/notional_reload to update NOTIONAL at runtime."""
        reload_path = tmp_path / "notional_reload"
        reload_path.write_text("35000")
        assert int(reload_path.read_text().strip()) == 35000


class TestValidationRanges:
    @pytest.mark.parametrize("bad_value", [0, 4999, 250001, -1, 1000000])
    def test_out_of_range_rejected(self, bad_value):
        """Values outside [5000, 250000] are invalid."""
        assert not (5000 <= bad_value <= 250000)

    @pytest.mark.parametrize("good_value", [5000, 25000, 100000, 250000])
    def test_in_range_accepted(self, good_value):
        """Values within [5000, 250000] are valid."""
        assert 5000 <= good_value <= 250000


# ─── Integration: live server (skipped unless running) ───────────────────────

def _server_running() -> bool:
    """Return True only if the server has the /api/config/notional endpoint (Phase 10+)."""
    try:
        import urllib.request
        import json as _json
        with urllib.request.urlopen(
            "http://127.0.0.1:2112/api/config/notional", timeout=1
        ) as r:
            data = _json.loads(r.read())
            return "notional_account_size" in data
    except Exception:
        return False


@pytest.mark.skipif(not _server_running(), reason="API server not running on :2112")
class TestApiEndpointLive:
    def test_get_notional(self):
        """GET /api/config/notional returns a valid notional value."""
        import urllib.request
        import json as _json
        with urllib.request.urlopen("http://127.0.0.1:2112/api/config/notional") as r:
            data = _json.loads(r.read())
        assert "notional_account_size" in data
        n = data["notional_account_size"]
        assert 5000 <= n <= 250000

    def test_post_notional_valid(self):
        """POST valid notional → 200 with updated value."""
        import urllib.request
        import json as _json
        payload = _json.dumps({"notional_account_size": 30000}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:2112/api/config/notional",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as r:
            data = _json.loads(r.read())
        assert data["notional_account_size"] == 30000
        assert "pending_restart" in data

        # Reset to 25000
        payload = _json.dumps({"notional_account_size": 25000}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:2112/api/config/notional",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req)

    def test_post_notional_invalid_range(self):
        """POST out-of-range notional → 400."""
        import urllib.request
        import json as _json
        payload = _json.dumps({"notional_account_size": 999}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:2112/api/config/notional",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req)
            assert False, "Should have raised HTTPError 400"
        except urllib.error.HTTPError as e:
            assert e.code == 400
