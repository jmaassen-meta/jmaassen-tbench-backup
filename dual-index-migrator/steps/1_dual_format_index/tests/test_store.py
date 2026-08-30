import tempfile
from dual_index.store import DualIndex


def test_write_single_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "single")
        idx.write_single("alice", 1001)
        out = idx.read("alice")
        assert out == {"username": "alice", "uid": 1001, "format": "single"}


def test_write_dual_linked_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "dual")
        idx.write_dual("alice", 100, 200, "linked")
        out = idx.read("alice")
        assert out == {
            "username": "alice",
            "ig_uid": 100,
            "threads_uid": 200,
            "link_state": "linked",
            "format": "dual",
        }


def test_write_dual_unlinked_and_read():
    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "dual")
        idx.write_dual("bob", 100, None, "unlinked")
        out = idx.read("bob")
        assert out["threads_uid"] is None
        assert out["link_state"] == "unlinked"


def test_read_absent_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "single")
        assert idx.read("nobody") is None


def test_write_single_invalid_username_raises():
    import pytest

    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "single")
        with pytest.raises(ValueError):
            idx.write_single("Alice", 1)


def test_overwrite_single():
    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "single")
        idx.write_single("alice", 1)
        idx.write_single("alice", 2)
        assert idx.read("alice")["uid"] == 2


def test_write_dual_over_single_key():
    with tempfile.TemporaryDirectory() as tmp:
        idx = DualIndex(tmp, 4, "dual")
        idx.write_single("alice", 1)
        idx.write_dual("alice", 10, 20, "linked")
        out = idx.read("alice")
        assert out["format"] == "dual"
        assert out["ig_uid"] == 10


def test_fmt_is_metadata_not_restriction():
    with tempfile.TemporaryDirectory() as tmp:
        idx_single = DualIndex(tmp, 4, "single")
        idx_single.write_dual("alice", 100, 200, "linked")
        assert idx_single.read("alice")["format"] == "dual"
        idx_dual = DualIndex(tmp, 4, "dual")
        idx_dual.write_single("bob", 999)
        assert idx_dual.read("bob")["format"] == "single"
        assert idx_dual.read("bob")["uid"] == 999
