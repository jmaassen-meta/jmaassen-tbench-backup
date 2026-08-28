import json
import subprocess
import sys
import tempfile
from pathlib import Path

def run_cli(args, cwd=None):
    cmd = [sys.executable, "-m", "dual_index.cli"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result

def test_cli_backfill_verify():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        r = run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["backfill", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert "total" in out
        r = run_cli(["verify", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["inconsistent"] == 0

def test_cli_gc_repairs_dangling():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "bob", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        # Create mismatch via UserStore-like direct put through python
        # Use python to put blob with wrong username
        import subprocess, sys
        code = f"""
from dual_index.user_store import UserStore
us = UserStore("{data}", 4)
us.put(100, {{"username": "alice", "uid": 100, "universe": "ig"}})
"""
        subprocess.run([sys.executable, "-c", code], check=True)
        r = run_cli(["gc", "--dangling", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["dangling_found"] >= 1

def test_cli_user_store_universe_immutable():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        code = f"""
from dual_index.user_store import UserStore
us = UserStore("{data}", 4)
try:
    us.put(100, {{"username": "alice", "uid": 100, "universe": "threads"}})
    print("should have raised")
    exit(1)
except ValueError:
    print("correctly raised")
"""
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert result.returncode == 0

def test_cli_backfill_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "4", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        r1 = run_cli(["backfill", "--base-dir", str(data), "--output", "json"])
        out1 = json.loads(r1.stdout.strip())
        r2 = run_cli(["backfill", "--base-dir", str(data), "--output", "json"])
        out2 = json.loads(r2.stdout.strip())
        assert out1["needs_backfill"] == out2["needs_backfill"]
