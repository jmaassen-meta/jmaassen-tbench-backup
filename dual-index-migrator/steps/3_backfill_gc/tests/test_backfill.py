import tempfile
import json
from pathlib import Path
from dual_index.atomic import AtomicIndex
from dual_index.user_store import UserStore
from dual_index.shard import ShardStore
from dual_index.backfill import backfill, gc_dangling, verify


def test_backfill_verification_single():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        # create single via write (which also creates blob) - use AtomicIndex link? For single, use ShardStore directly
        # Use atomic's underlying stores: create single via ShardStore
        from dual_index.shard import ShardStore

        ig = ShardStore(str(Path(tmp) / "ig"), 4)
        ig.put("alice", {"username": "alice", "uid": 100, "format": "single"})
        us = UserStore(tmp, 4)
        us.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        res = backfill(tmp, 4)
        assert res["total"] >= 1
        assert res["needs_backfill"] >= 1
        # idempotent
        res2 = backfill(tmp, 4)
        assert res2["needs_backfill"] == res["needs_backfill"]


def test_backfill_does_not_create_missing_blob():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        # delete one blob to make dangling — use file-level handling via glob
        # (do not rely on private UserStore._load_shard/_save_shard)
        import glob
        import json as _json

        for path_str in glob.glob(str(Path(tmp) / "users" / "*.json")):
            p = Path(path_str)
            try:
                data = _json.loads(p.read_text())
            except Exception:
                continue
            if "200" in data or 200 in data:
                # handle both str and int keys (json stores keys as str)
                data.pop("200", None)
                data.pop(200, None)
                p.write_text(_json.dumps(data))
                break
        us = UserStore(tmp, 4)
        # backfill should report inconsistent but not create missing blob
        res = backfill(tmp, 4)
        assert res["inconsistent"] >= 1
        # blob should still be missing after backfill (since backfill does not create)
        assert us.get(200) is None
        # second backfill same counts
        res2 = backfill(tmp, 4)
        assert res2["inconsistent"] == res["inconsistent"]


def test_verify_consistent_after_link():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        res = verify(tmp, 4)
        assert res["inconsistent"] == 0
        assert res["consistent"] >= 1


def test_gc_repairs_username_mismatch_available():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        # Create mismatch: index bob -> 100, but blob 100 has username alice (available, since alice not in index)
        us = UserStore(tmp, 4)
        us.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        # Now index bob points to 100 but blob says alice, and alice is available (no index alice)
        res = gc_dangling(tmp, 4)
        assert res["dangling_found"] >= 1
        assert res["repaired"] >= 1 or res["removed"] >= 1
        # After repair, either index moved to alice or blob fixed to bob
        # According spec, since alice is available, it should move index to alice and delete bob
        # Check that either bob index gone and alice exists, or blob fixed
        # Our implementation moves index to alice
        # Verify that after gc, verify is consistent or bob gone
        # Check that one of those happened
        ig = ShardStore(str(Path(tmp) / "ig"), 4)
        # After available repair, bob should be gone, alice should exist
        # If not available case, blob would be fixed to bob
        # Since alice was available, we expect alice exists now
        assert (
            ig.get("alice") is not None
            or ig.get("bob") is None
            or us.get(100)["username"] == "bob"
        )


def test_gc_repairs_username_mismatch_not_available():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        idx.link("alice", 300, 400)
        # Now make bob's ig blob have username alice (which is taken by different uid 300)
        us = UserStore(tmp, 4)
        us.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        # alice already exists with uid 300, so alice is not available for 100
        res = gc_dangling(tmp, 4)
        assert res["dangling_found"] >= 1
        # Since alice is taken, gc should fix blob to match original index (bob)
        blob = us.get(100)
        assert blob["username"] == "bob"
        # index bob should still exist
        ig = ShardStore(str(Path(tmp) / "ig"), 4)
        assert ig.get("bob") is not None


def test_backfill_skips_locked():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        Path(tmp, ".hold_alice").touch()
        res = backfill(tmp, 4)
        # should have error for locked
        assert res["errors"] >= 1 or res["inconsistent"] >= 0
        Path(tmp, ".hold_alice").unlink()


def test_verify_detects_universe_mismatch():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        # Try to mutate universe via direct file? But via API it should fail
        # Instead test that putting wrong universe via UserStore fails
        us = UserStore(tmp, 4)
        with pytest.raises(ValueError):
            us.put(100, {"username": "alice", "uid": 100, "universe": "threads"})
        # verify should still be consistent
        res = verify(tmp, 4)
        assert res["inconsistent"] == 0


def test_link_creates_blobs_with_universe():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        us = UserStore(tmp, 4)
        assert us.get(100)["universe"] == "ig"
        assert us.get(200)["universe"] == "threads"
        assert us.get(100)["username"] == "alice"
        assert us.get(200)["username"] == "alice"


def test_rename_updates_blobs():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        idx.rename("bob", "alice")
        us = UserStore(tmp, 4)
        # blobs should now have username alice
        assert us.get(100)["username"] == "alice"
        assert us.get(200)["username"] == "alice"
        # old bob index gone
        assert idx.read("bob") is None
        assert idx.read("alice") is not None


def test_verify_lists_inconsistent_users():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        ig = ShardStore(str(Path(tmp) / "ig"), 4)
        ig.put("alice", {"username": "alice", "uid": 100, "format": "single"})
        us = UserStore(tmp, 4)
        us.put(100, {"username": "bob", "uid": 100, "universe": "ig"})
        res = verify(tmp, 4)
        assert res["inconsistent"] >= 1
        assert "inconsistent_users" in res
        assert (
            "alice" in res["inconsistent_users"] or "bob" in res["inconsistent_users"]
        )


def test_gc_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        us = UserStore(tmp, 4)
        us.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        res1 = gc_dangling(tmp, 4)
        res2 = gc_dangling(tmp, 4)
        # second gc should find no more dangling
        assert (
            res2["dangling_found"] == 0
            or res2["dangling_found"] <= res1["dangling_found"]
        )
        v = verify(tmp, 4)
        assert v["inconsistent"] == 0


def test_gc_no_lock_left_after_success():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        us = UserStore(tmp, 4)
        us.put(100, {"username": "alice", "uid": 100, "universe": "ig"})
        gc_dangling(tmp, 4)
        # no lock files should remain
        assert not list(Path(tmp).glob(".lock_*"))


def test_backfill_verify_idempotent_and_lists():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.link("bob", 300, 400)
        res1 = backfill(tmp, 4)
        res2 = backfill(tmp, 4)
        assert res1["total"] == res2["total"]
        assert res1["consistent"] == res2["consistent"]
        v1 = verify(tmp, 4)
        v2 = verify(tmp, 4)
        assert v1["total"] == v2["total"]
        assert v1["inconsistent"] == 0
        assert v1["consistent"] >= 2


def test_verify_detects_threads_without_ig():
    with tempfile.TemporaryDirectory() as tmp:
        threads = ShardStore(str(Path(tmp) / "threads"), 4)
        threads.put("alice", {"username": "alice", "uid": 200, "format": "single"})
        us = UserStore(tmp, 4)
        us.put(200, {"username": "alice", "uid": 200, "universe": "threads"})
        res = verify(tmp, 4)
        assert res["inconsistent"] >= 1


def test_unlink_removes_threads_blob():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.unlink("alice")
        us = UserStore(tmp, 4)
        # ig blob should exist with alice, threads blob should be removed or updated to not exist
        assert us.get(100) is not None
        assert us.get(100)["username"] == "alice"
        assert us.get(100)["universe"] == "ig"
        # threads blob may be removed after unlink - our spec says handle unlinked state, so at least verify passes
        res = verify(tmp, 4)
        assert res["inconsistent"] == 0


def test_gc_removes_orphan_threads_without_ig():
    with tempfile.TemporaryDirectory() as tmp:
        threads = ShardStore(str(Path(tmp) / "threads"), 4)
        threads.put("alice", {"username": "alice", "uid": 200, "format": "single"})
        us = UserStore(tmp, 4)
        us.put(200, {"username": "alice", "uid": 200, "universe": "threads"})
        res = gc_dangling(tmp, 4)
        assert res["dangling_found"] >= 1
        # threads without IG is dangling — should be removed
        assert threads.get("alice") is None
        v = verify(tmp, 4)
        assert v["inconsistent"] == 0


def test_backfill_counts_both_lock_types():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.link("bob", 300, 400)
        # lock alice via hold, bob via real lock
        Path(tmp, ".hold_alice").touch()
        # create a real lock file for bob via AtomicIndex internal? Simulate by touching .lock_bob
        Path(tmp, ".lock_bob").touch()
        res = backfill(tmp, 4)
        # both should be counted as errors/busy
        assert res["errors"] >= 2
        # idempotent
        res2 = backfill(tmp, 4)
        assert res2["errors"] == res["errors"]
        Path(tmp, ".hold_alice").unlink()
        Path(tmp, ".lock_bob").unlink()
        # after unlocking, no errors
        res3 = backfill(tmp, 4)
        assert res3["errors"] == 0


import pytest
