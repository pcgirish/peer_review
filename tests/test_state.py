import fcntl
import json
import subprocess

import pytest

from review_loop.state import (
    ArtifactState,
    LockedError,
    StateError,
    Store,
    repo_paths,
)


def git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_repo_paths_outside_repo_is_error(tmp_path):
    with pytest.raises(StateError, match="git repository"):
        repo_paths(tmp_path)


def test_repo_paths_linked_worktree_shares_common_dir(repo):
    wt = repo.parent / "wt"
    git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    a = repo_paths(repo)
    b = repo_paths(wt)
    assert a.common_dir == b.common_dir
    assert b.toplevel == wt.resolve()


def test_store_assigns_id_and_reuses_known_path(repo):
    store = Store(repo_paths(repo).common_dir)
    st, created = store.for_path("plan", "docs/p.md")
    assert created and st.id and st.path == "docs/p.md"
    store.save(st)
    again, created2 = store.for_path("plan", "docs/p.md")
    assert not created2 and again.id == st.id
    assert store.load(st.id).path == "docs/p.md"


def test_store_state_survives_new_store_instance(repo):
    common = repo_paths(repo).common_dir
    st, _ = Store(common).for_path("plan", "p.md")
    st.session_id = "sid"
    st.intent = "i"
    Store(common).save(st)
    loaded = Store(common).load(st.id)
    assert loaded.session_id == "sid" and loaded.intent == "i" and loaded.limit == 3


def test_store_move_updates_mapping_and_rejects_owned_path(repo):
    store = Store(repo_paths(repo).common_dir)
    a, _ = store.for_path("plan", "a.md")
    store.save(a)
    b, _ = store.for_path("plan", "b.md")
    store.save(b)
    moved = store.move(a.id, "moved.md")
    assert moved.path == "moved.md"
    assert store.for_path("plan", "moved.md")[0].id == a.id
    assert store.for_path("plan", "a.md")[1] is True
    with pytest.raises(StateError, match="owned"):
        store.move(a.id, "b.md")


def test_store_load_unknown_is_error(repo):
    store = Store(repo_paths(repo).common_dir)
    with pytest.raises(StateError, match="unknown artifact"):
        store.load("nope")


def test_store_resolve_accepts_id_or_path(repo):
    store = Store(repo_paths(repo).common_dir)
    a, _ = store.for_path("plan", "a.md")
    store.save(a)
    assert store.resolve(a.id).id == a.id
    assert store.resolve("a.md").id == a.id


def test_lock_rejects_overlap(repo):
    store = Store(repo_paths(repo).common_dir)
    a, _ = store.for_path("plan", "a.md")
    store.save(a)
    with store.lock(a.id), pytest.raises(LockedError), store.lock(a.id):
        pass
    # released on exit
    with store.lock(a.id):
        pass


def test_lock_held_by_other_process_is_rejected(repo):
    store = Store(repo_paths(repo).common_dir)
    a, _ = store.for_path("plan", "a.md")
    store.save(a)
    store.lock_path(a.id).parent.mkdir(parents=True, exist_ok=True)
    with open(store.lock_path(a.id), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LockedError), store.lock(a.id):
            pass


def test_save_is_atomic_json(repo):
    store = Store(repo_paths(repo).common_dir)
    a, _ = store.for_path("plan", "a.md")
    store.save(a)
    p = store.artifact_path(a.id)
    assert json.loads(p.read_text())["id"] == a.id
    assert not list(p.parent.glob("*.tmp*"))


def test_reset_retires_session_and_keeps_history():
    st = ArtifactState(id="x", kind="plan", path="p.md")
    st.session_id = "old"
    st.rounds = 2
    st.limit = 5
    st.history.append({"round": 1})
    st.reset()
    assert st.session_id is None and st.rounds == 0 and st.limit == 3
    assert st.retired_sessions == ["old"]
    assert st.history == [{"round": 1}]


def test_reserve_persists_identity_and_is_idempotent(repo):
    store = Store(repo_paths(repo).common_dir)
    st = store.reserve("plan", "new.md")
    assert st.id and store.load(st.id).path == "new.md"
    assert Store(store.root.parent).reserve("plan", "new.md").id == st.id
    # index lock is released after reserve returns
    with store.lock_index():
        pass


def test_reserve_index_lock_held_elsewhere_is_rejected(repo):
    store = Store(repo_paths(repo).common_dir)
    store.index_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(store.index_lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LockedError), store.reserve("plan", "x.md"):
            pass
