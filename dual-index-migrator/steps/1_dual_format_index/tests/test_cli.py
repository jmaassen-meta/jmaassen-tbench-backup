import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run_cli(args, cwd=None):
    # run python -m dual_index.cli with PYTHONPATH=/app already set in harness, but for local we set PYTHONPATH
    env = None
    # Use sys.executable
    cmd = [sys.executable, "-m", "dual_index.cli"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result


def test_cli_init_creates_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        result = run_cli(
            ["init", "--shards", "8", "--format", "single", "--base-dir", str(data)]
        )
        assert result.returncode == 0
        assert (data / "metadata.json").exists()
        with open(data / "metadata.json") as f:
            meta = json.load(f)
            assert meta["shards"] == 8
            assert meta["format"] == "single"


def test_cli_write_single_and_read_json():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(
            ["init", "--shards", "4", "--format", "single", "--base-dir", str(data)]
        )
        r = run_cli(
            ["write", "--user", "alice", "--uid", "1001", "--base-dir", str(data)]
        )
        assert r.returncode == 0
        r = run_cli(
            ["read", "--user", "alice", "--base-dir", str(data), "--output", "json"]
        )
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["username"] == "alice"
        assert out["uid"] == 1001
        assert out["format"] == "single"


def test_cli_write_dual_linked_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        r = run_cli(
            [
                "write",
                "--user",
                "bob",
                "--ig-uid",
                "100",
                "--threads-uid",
                "200",
                "--link-state",
                "linked",
                "--base-dir",
                str(data),
            ]
        )
        assert r.returncode == 0
        r = run_cli(
            ["read", "--user", "bob", "--base-dir", str(data), "--output", "json"]
        )
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["ig_uid"] == 100
        assert out["threads_uid"] == 200
        assert out["link_state"] == "linked"


def test_cli_write_dual_unlinked():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        r = run_cli(
            [
                "write",
                "--user",
                "charlie",
                "--ig-uid",
                "300",
                "--link-state",
                "unlinked",
                "--base-dir",
                str(data),
            ]
        )
        assert r.returncode == 0
        r = run_cli(
            ["read", "--user", "charlie", "--base-dir", str(data), "--output", "json"]
        )
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["threads_uid"] is None
        assert out["link_state"] == "unlinked"


def test_cli_read_absent_returns_null():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(
            ["init", "--shards", "4", "--format", "single", "--base-dir", str(data)]
        )
        r = run_cli(
            ["read", "--user", "nobody", "--base-dir", str(data), "--output", "json"]
        )
        assert r.returncode == 0
        assert r.stdout.strip() == "null"


def test_cli_username_validation_fails_for_capital():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(
            ["init", "--shards", "4", "--format", "single", "--base-dir", str(data)]
        )
        r = run_cli(["write", "--user", "Alice", "--uid", "1", "--base-dir", str(data)])
        assert r.returncode != 0


def test_cli_link_state_linked_requires_threads_uid():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        r = run_cli(
            [
                "write",
                "--user",
                "bob",
                "--ig-uid",
                "100",
                "--link-state",
                "linked",
                "--base-dir",
                str(data),
            ]
        )
        assert r.returncode != 0


def test_cli_default_base_dir():
    with tempfile.TemporaryDirectory() as tmp:
        # use tmp as cwd, default ./data
        result = run_cli(["init", "--shards", "2", "--format", "single"], cwd=tmp)
        assert result.returncode == 0
        assert (Path(tmp) / "data").exists()


def test_cli_help():
    r = run_cli(["--help"])
    assert r.returncode == 0
    low = (r.stdout + r.stderr).lower()
    assert "usage:" in low and ("dual_index" in low or "init" in low)
    r = run_cli(["init", "--help"])
    assert r.returncode == 0
