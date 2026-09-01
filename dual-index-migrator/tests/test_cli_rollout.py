import json
# dual-index-migrator test - deterministic offline migration
import subprocess
import sys
import tempfile
from pathlib import Path

def run_cli(args, cwd=None):
    cmd = [sys.executable, "-m", "dual_index.cli"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return result

def test_cli_rollout_phases():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        r = run_cli(["init", "--shards", "8", "--format", "dual", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        assert r.returncode == 0
        r = run_cli(["rollout", "--phase", "canary", "--base-dir", str(data)])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["phase"] == "canary"
        assert out["shards_migrated"] == [0]
        r = run_cli(["rollout", "--phase", "partial", "--base-dir", str(data)])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["shards_migrated"] == [0,1,2,3]
        r = run_cli(["rollout", "--phase", "full", "--base-dir", str(data)])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["shards_migrated"] == list(range(8))

def test_cli_rollout_verify():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "8", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        r = run_cli(["rollout-verify", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert "overall" in out
        # before rollout, overall false
        assert out["overall"] == False
        run_cli(["rollout", "--phase", "canary", "--base-dir", str(data)])
        run_cli(["rollout", "--phase", "partial", "--base-dir", str(data)])
        r = run_cli(["rollout-verify", "--base-dir", str(data), "--output", "json"])
        assert r.returncode == 0
        out = json.loads(r.stdout.strip())
        assert out["overall"] == True

def test_cli_rollout_fails_if_locked():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "8", "--format", "dual", "--base-dir", str(data)])
        # Find a user that hashes to shard 0
        import hashlib
        target = None
        for name in ["user0", "alice", "bob", "charlie", "test"]:
            if int(hashlib.md5(name.encode()).hexdigest(), 16) % 8 == 0:
                target = name
                break
        if target is None:
            target = "user0"
        run_cli(["link", "--user", target, "--ig-uid", "1000", "--threads-uid", "2000", "--base-dir", str(data)])
        Path(data, f".hold_{target}").touch()
        r = run_cli(["rollout", "--phase", "canary", "--base-dir", str(data)])
        assert r.returncode != 0
        # rollout.json should not have canary
        import json
        rollout_file = data / "rollout.json"
        if rollout_file.exists():
            with open(rollout_file) as f:
                d = json.load(f)
                assert d["phase"] != "canary" or d["shards_migrated"] != [0]
        Path(data, f".hold_{target}").unlink()
        r = run_cli(["rollout", "--phase", "canary", "--base-dir", str(data)])
        assert r.returncode == 0

def test_cli_rollout_monotonic():
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        run_cli(["init", "--shards", "8", "--format", "dual", "--base-dir", str(data)])
        run_cli(["link", "--user", "alice", "--ig-uid", "100", "--threads-uid", "200", "--base-dir", str(data)])
        run_cli(["rollout", "--phase", "canary", "--base-dir", str(data)])
        r = run_cli(["rollout", "--phase", "canary", "--base-dir", str(data)])
        assert r.returncode != 0
        r = run_cli(["rollout", "--phase", "full", "--base-dir", str(data)])
        # Should fail because cannot skip partial
        assert r.returncode != 0
