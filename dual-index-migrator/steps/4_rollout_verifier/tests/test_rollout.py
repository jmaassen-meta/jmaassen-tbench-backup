import tempfile
import json
from pathlib import Path
import pytest
from dual_index.rollout import rollout_status, advance_rollout, rollout_verify
from dual_index.atomic import AtomicIndex

def test_rollout_status_not_started():
    with tempfile.TemporaryDirectory() as tmp:
        status = rollout_status(tmp)
        assert status["phase"] == "not_started"
        assert status["shards_migrated"] == []

def test_advance_rollout_canary_partial_full():
    with tempfile.TemporaryDirectory() as tmp:
        # need to init via AtomicIndex or via cli init? For API, we just need base_dir and shards
        # Create minimal consistent store: init via AtomicIndex (creates ig/threads/users)
        idx = AtomicIndex(tmp, 8)
        idx.link("alice", 100, 200)
        # Now advance
        status = advance_rollout(tmp, 8, "canary")
        assert status["phase"] == "canary"
        assert status["shards_migrated"] == [0]
        status = advance_rollout(tmp, 8, "partial")
        assert status["phase"] == "partial"
        assert status["shards_migrated"] == [0,1,2,3]
        status = advance_rollout(tmp, 8, "full")
        assert status["phase"] == "full"
        assert status["shards_migrated"] == list(range(8))

def test_advance_rollout_fails_if_locked():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 8)
        idx.link("alice", 100, 200)
        # Find which shard alice hashes to
        import hashlib
        h = int(hashlib.md5("alice".encode()).hexdigest(), 16) % 8
        # Put hold marker for alice
        Path(tmp, ".hold_alice").touch()
        # Try to advance canary if alice is in canary shard (0) - need to ensure alice is in canary
        # If alice not in shard 0, this test may not block. So create a user that is in shard 0
        # Find a username that hashes to 0
        target_user = None
        for name in ["alice", "bob", "charlie", "dave", "user0", "user1", "test0"]:
            if int(hashlib.md5(name.encode()).hexdigest(), 16) % 8 == 0:
                target_user = name
                break
        if target_user is None:
            target_user = "alice"
            h = 0
        # Ensure target_user exists and is in canary
        # Create it if not exists
        try:
            idx.link(target_user, 1000, 2000)
        except Exception:
            pass
        Path(tmp, f".hold_{target_user}").touch()
        with pytest.raises(ValueError):
            advance_rollout(tmp, 8, "canary")
        # rollout.json should not have been created or should still be not_started
        status = rollout_status(tmp)
        assert status["phase"] == "not_started" or status["phase"] != "canary"
        Path(tmp, f".hold_{target_user}").unlink()
        # Now should succeed
        status = advance_rollout(tmp, 8, "canary")
        assert status["phase"] == "canary"

def test_advance_rollout_monotonic():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 8)
        idx.link("alice", 100, 200)
        advance_rollout(tmp, 8, "canary")
        with pytest.raises(ValueError):
            advance_rollout(tmp, 8, "canary")  # cannot stay
        with pytest.raises(ValueError):
            advance_rollout(tmp, 8, "not_started")  # cannot go backwards

def test_advance_rollout_fails_if_inconsistent():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 8)
        idx.link("alice", 100, 200)
        # Create dangling by putting blob with wrong username
        from dual_index.user_store import UserStore
        us = UserStore(tmp, 8)
        us.put(100, {"username": "bob", "uid": 100, "universe": "ig"})
        # Now verify should be inconsistent, so advance should fail
        with pytest.raises(ValueError):
            advance_rollout(tmp, 8, "canary")

def test_rollout_verify_overall():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 8)
        idx.link("alice", 100, 200)
        # Before any rollout, overall should be false (needs at least partial)
        res = rollout_verify(tmp, 8)
        assert res["overall"] == False
        advance_rollout(tmp, 8, "canary")
        res = rollout_verify(tmp, 8)
        # canary is not enough for overall true (needs at least partial)
        assert res["overall"] == False
        advance_rollout(tmp, 8, "partial")
        res = rollout_verify(tmp, 8)
        assert res["overall"] == True
        advance_rollout(tmp, 8, "full")
        res = rollout_verify(tmp, 8)
        assert res["overall"] == True
