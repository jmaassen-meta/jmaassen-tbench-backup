import json
import tempfile
from pathlib import Path
import pytest
from dual_index.atomic import AtomicIndex
from dual_index.shard import ShardStore


def _get_ig_threads(base_dir, shards, username):
    ig = ShardStore(str(Path(base_dir) / "ig"), shards)
    threads = ShardStore(str(Path(base_dir) / "threads"), shards)
    return ig.get(username), threads.get(username)


def test_link_creates_both_universes():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        ig, thr = _get_ig_threads(tmp, 4, "alice")
        assert ig is not None
        assert ig["ig_uid"] == 100
        assert ig["link_state"] == "linked"
        assert thr is not None
        assert thr["uid"] == 200


def test_link_updates_existing():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.link("alice", 101, 201)
        ig, thr = _get_ig_threads(tmp, 4, "alice")
        assert ig["ig_uid"] == 101
        assert thr["uid"] == 201


def test_unlink_removes_threads_and_updates_ig():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.unlink("alice")
        ig, thr = _get_ig_threads(tmp, 4, "alice")
        assert ig["link_state"] == "unlinked"
        assert ig["threads_uid"] is None
        assert thr is None


def test_unlink_fails_if_not_exist():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        with pytest.raises(ValueError):
            idx.unlink("nobody")


def test_unlink_fails_if_already_unlinked():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.unlink("alice")
        with pytest.raises(ValueError):
            idx.unlink("alice")


def test_rename_moves_both_universes():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        idx.rename("bob", "alice")
        # bob should be gone
        assert idx.read("bob") is None
        ig_alice, thr_alice = _get_ig_threads(tmp, 4, "alice")
        assert ig_alice is not None
        assert ig_alice["ig_uid"] == 100
        assert thr_alice is not None
        assert thr_alice["uid"] == 200
        # from bob should be absent in both
        ig_bob, thr_bob = _get_ig_threads(tmp, 4, "bob")
        assert ig_bob is None
        assert thr_bob is None


def test_rename_fails_if_to_exists():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        idx.link("alice", 300, 400)
        with pytest.raises(ValueError):
            idx.rename("bob", "alice")


def test_rename_fails_if_from_absent():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        with pytest.raises(ValueError):
            idx.rename("ghost", "alice")


def test_rename_single_to_new():
    with tempfile.TemporaryDirectory() as tmp:
        # Use write_single via shard directly to create single in IG only
        # Instead use AtomicIndex link then unlink to get single? Simpler: create via ShardStore
        ig = ShardStore(str(Path(tmp) / "ig"), 4)
        ig.put("bob", {"username": "bob", "uid": 100, "format": "single"})
        idx = AtomicIndex(tmp, 4)
        idx.rename("bob", "alice")
        assert idx.read("alice") is not None
        assert idx.read("alice")["username"] == "alice"
        assert idx.read("bob") is None


def test_hold_marker_blocks_link():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        # create hold marker
        Path(tmp, ".hold_alice").touch()
        with pytest.raises(ValueError):
            idx.link("alice", 100, 200)
        # after removing hold, should succeed
        Path(tmp, ".hold_alice").unlink()
        idx.link("alice", 100, 200)
        assert idx.read("alice") is not None


def test_hold_marker_blocks_rename():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        Path(tmp, ".hold_alice").touch()
        with pytest.raises(ValueError):
            idx.rename("bob", "alice")
        # alice hold should block, bob->charlie should work
        Path(tmp, ".hold_alice").unlink()
        idx.rename("bob", "alice")
        assert idx.read("alice") is not None


def test_rename_ordered_lock_no_deadlock():
    # Hold on bob should not block rename alice->charlie (different users), but should block rename involving bob
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.link("bob", 300, 400)
        Path(tmp, ".hold_bob").touch()
        # rename alice->charlie does not involve bob, should succeed
        idx.rename("alice", "charlie")
        assert idx.read("charlie") is not None
        assert idx.read("alice") is None
        # Now hold alice, rename bob->alice should fail (to_user held)
        Path(tmp, ".hold_bob").unlink()
        # recreate alice for second part
        idx.link("alice", 100, 200)
        Path(tmp, ".hold_alice").touch()
        with pytest.raises(ValueError):
            idx.rename("bob", "alice")  # to_user alice is held
        Path(tmp, ".hold_alice").unlink()
        # after releasing, should succeed
        # need to ensure bob still exists and alice exists, so rename bob->dave should work
        idx.rename("bob", "dave")
        assert idx.read("dave") is not None


def test_crash_recovery_link_pending():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        # Simulate crash: write pending intent without commit
        wal = Path(tmp) / "wal.jsonl"
        # Find last intent? We'll manually append a pending link for bob without commit
        import json, uuid

        pending_id = str(uuid.uuid4())
        with open(wal, "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": pending_id,
                        "op": "link",
                        "username": "bob",
                        "old_ig": None,
                        "old_threads": None,
                        "state": "intent",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            # Simulate partial write: create bob in IG but not commit
            ig = ShardStore(str(Path(tmp) / "ig"), 4)
            ig.put(
                "bob",
                {
                    "username": "bob",
                    "ig_uid": 999,
                    "threads_uid": 999,
                    "link_state": "linked",
                    "format": "dual",
                },
            )
        # Next operation on bob should detect pending and recover (remove bob)
        idx2 = AtomicIndex(tmp, 4)
        with pytest.raises(ValueError):
            idx2.link("bob", 1, 2)  # should detect pending and error
        # After recovery, bob should be gone (since old was None)
        assert idx2.read("bob") is None
        # Now link should succeed
        idx2.link("bob", 1, 2)
        assert idx2.read("bob") is not None


def test_link_invalid_username():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        with pytest.raises(ValueError):
            idx.link("Alice", 100, 200)


def test_read_returns_none_if_absent():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        assert idx.read("nobody") is None


def test_read_after_link():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        out = idx.read("alice")
        assert out["username"] == "alice"
        assert out["ig_uid"] == 100
        assert out["threads_uid"] == 200
        assert out["link_state"] == "linked"


def test_rename_same_user_fails():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        with pytest.raises(ValueError):
            idx.rename("alice", "alice")


def test_link_no_lock_left_after_success():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        # lock file must be removed after success
        assert not Path(tmp, ".lock_alice").exists()
        # wal must have a commit marker
        wal = Path(tmp) / "wal.jsonl"
        assert wal.exists()
        content = wal.read_text()
        assert "commit" in content
        assert "alice" in content


def test_unlink_hold_marker_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        Path(tmp, ".hold_alice").touch()
        with pytest.raises(ValueError):
            idx.unlink("alice")
        Path(tmp, ".hold_alice").unlink()
        idx.unlink("alice")
        assert idx.read("alice")["link_state"] == "unlinked"


def test_rename_from_hold_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        Path(tmp, ".hold_bob").touch()
        with pytest.raises(ValueError):
            idx.rename("bob", "alice")
        Path(tmp, ".hold_bob").unlink()
        idx.rename("bob", "alice")
        assert idx.read("alice") is not None


def test_link_leaves_wal_intent_and_commit():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        wal = Path(tmp) / "wal.jsonl"
        assert wal.exists()
        txt = wal.read_text()
        # should have at least one intent and one commit for alice
        assert txt.count("intent") >= 1
        assert txt.count("commit") >= 1


def test_wal_intent_and_commit_share_same_id():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        wal = Path(tmp) / "wal.jsonl"
        lines = [l for l in wal.read_text().splitlines() if l.strip()]
        assert len(lines) >= 2
        import json

        first = json.loads(lines[0])
        last = json.loads(lines[-1])
        assert "id" in first and "id" in last
        assert first["id"] == last["id"]
        assert first["state"] == "intent"
        assert last["state"] == "commit"


def test_rename_preserves_link_state_and_uids():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("bob", 100, 200)
        out_before = idx.read("bob")
        assert out_before["link_state"] == "linked"
        idx.rename("bob", "alice")
        out_after = idx.read("alice")
        assert out_after is not None
        assert out_after["username"] == "alice"
        assert out_after["ig_uid"] == 100
        assert out_after["threads_uid"] == 200
        assert out_after["link_state"] == "linked"
        assert idx.read("bob") is None


def test_wal_no_pending_after_success():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.link("bob", 300, 400)
        idx.rename("bob", "charlie")
        wal = Path(tmp) / "wal.jsonl"
        import json

        lines = [json.loads(l) for l in wal.read_text().splitlines() if l.strip()]
        # Every intent should have a matching commit with same id
        intents = {x["id"] for x in lines if x.get("state") == "intent"}
        commits = {x["id"] for x in lines if x.get("state") == "commit"}
        assert intents <= commits, (
            f"pending intents without commit: {intents - commits}"
        )


def test_read_respects_lock_returns_committed():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        # Simulate pending link for bob with partial write 999
        wal = Path(tmp) / "wal.jsonl"
        import uuid, json
        from dual_index.shard import ShardStore

        pending_id = str(uuid.uuid4())
        with open(wal, "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": pending_id,
                        "op": "link",
                        "username": "bob",
                        "old_ig": None,
                        "old_threads": None,
                        "state": "intent",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            ig = ShardStore(str(Path(tmp) / "ig"), 4)
            ig.put(
                "bob",
                {
                    "username": "bob",
                    "ig_uid": 999,
                    "threads_uid": 999,
                    "link_state": "linked",
                    "format": "dual",
                },
            )
        # Read must respect lock and not return partial 999. Spec says crash handler
        # automatically recovers and returns an error (ValueError), leaving pre-op state.
        idx2 = AtomicIndex(tmp, 4)
        try:
            out = idx2.read("bob")
        except ValueError:
            out = None
        # After first operation (which raised ValueError and recovered), bob should be gone (old None) or not partial
        if out is None:
            # Recovery may have happened on first read that raised, so second read should be clean
            out2 = idx2.read("bob")
            assert out2 is None
        else:
            assert out.get("ig_uid") != 999
        # After recovery, link should succeed cleanly
        idx2.link("bob", 100, 200)
        out_final = idx2.read("bob")
        assert out_final is not None
        assert out_final["ig_uid"] == 100


def test_rename_to_hold_blocks():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        # hold on to_user should block rename even though from is free
        Path(tmp, ".hold_charlie").touch()
        with pytest.raises(ValueError):
            idx.rename("alice", "charlie")
        # no partial, charlie still absent
        assert idx.read("charlie") is None
        # cleanup hold and succeed
        Path(tmp, ".hold_charlie").unlink()
        idx.rename("alice", "charlie")
        assert idx.read("charlie") is not None


def test_crash_recovery_rename_with_to_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        idx = AtomicIndex(tmp, 4)
        idx.link("alice", 100, 200)
        idx.link("bob", 300, 400)
        wal = Path(tmp) / "wal.jsonl"
        import uuid, json

        from dual_index.shard import ShardStore

        # Simulate pending rename alice->charlie where old_to was None but partial left charlie
        pending_id = str(uuid.uuid4())
        ig = ShardStore(str(Path(tmp) / "ig"), 4)
        old_from_ig = ig.get("alice")
        old_from_threads = ShardStore(str(Path(tmp) / "threads"), 4).get("alice")
        with open(wal, "a") as f:
            f.write(
                json.dumps(
                    {
                        "id": pending_id,
                        "op": "rename",
                        "from_user": "alice",
                        "to_user": "charlie",
                        "old_from_ig": old_from_ig,
                        "old_from_threads": old_from_threads,
                        "old_to_ig": None,
                        "old_to_threads": None,
                        "state": "intent",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            # partial: charlie created, alice not yet deleted
            ig.put(
                "charlie",
                {
                    "username": "charlie",
                    "ig_uid": 100,
                    "threads_uid": 200,
                    "link_state": "linked",
                    "format": "dual",
                },
            )
        # Next rename should trigger recovery and raise
        idx2 = AtomicIndex(tmp, 4)
        with pytest.raises(ValueError):
            idx2.rename("bob", "charlie")
        # After recovery, alice restored, charlie removed
        assert idx2.read("alice") is not None
        assert idx2.read("charlie") is None
        # Now rename should succeed
        idx2.rename("alice", "charlie")
        assert idx2.read("charlie") is not None
        assert idx2.read("alice") is None
