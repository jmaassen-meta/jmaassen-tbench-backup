import json
import subprocess
import sys
import tempfile
from pathlib import Path

def run_cli(args, cwd=None):
    cmd = [sys.executable, "-m", "dual_index.cli"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result

def test_cli_link_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        r = run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["read", "--user", "alice", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["username"] == "alice"
        assert out["ig_uid"] == 100
        assert out["threads_uid"] == 200

def test_cli_unlink():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        r = run_cli(["unlink", "--user", "alice", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["read", "--user", "alice", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["link_state"] == "unlinked"
        assert out["threads_uid"] is None

def test_cli_rename():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "bob", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        r = run_cli(["rename", "--from", "bob", "--to", "alice", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["read", "--user", "alice", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["username"] == "alice"
        r = run_cli(["read", "--user", "bob", "--base-dir", str(data), "--output", "json"])
        assert r.stdout.strip() == "null"

def test_cli_rename_fails_if_to_exists():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "bob", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "300", "--threads-uid", "400", "--base-dir", str(data)])
        r = run_cli(["rename", "--from", "bob", "--to", "alice", "--base-dir", str(data)])
        assert r.returncode != 0

def test_cli_hold_marker_blocks_link():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        Path(data, ".hold_alice").touch()
        r = run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        assert r.returncode != 0
        Path(data, ".hold_alice").unlink()
        r = run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        assert r.returncode == 0

def test_cli_hold_marker_blocks_rename():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "bob", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        Path(data, ".hold_alice").touch()
        r = run_cli(["rename", "--from", "bob", "--to", "alice", "--base-dir", str(data)])
        assert r.returncode != 0
        Path(data, ".hold_alice").unlink()
        r = run_cli(["rename", "--from", "bob", "--to", "alice", "--base-dir", str(data)])
        assert r.returncode == 0

def test_cli_rename_no_deadlock_with_hold():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        run_cli(["link", "--user", "bob", "--ig-uid", "300", "--threads-uid", "400", "--base-dir", str(data)])
        # Hold bob, try rename alice->charlie (not involving bob) should succeed
        Path(data, ".hold_bob").touch()
        r = run_cli(["rename", "--from", "alice", "--to", "charlie", "--base-dir", str(data)])
        assert r.returncode == 0
        # Hold alice, rename bob->alice should fail (to held)
        Path(data, ".hold_bob").unlink()
        Path(data, ".hold_alice").touch()
        r = run_cli(["rename", "--from", "bob", "--to", "alice", "--base-dir", str(data)])
        assert r.returncode != 0
        Path(data, ".hold_alice").unlink()

def test_cli_link_invalid_username():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        r = run_cli(["link", "--user", "Alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        assert r.returncode != 0
