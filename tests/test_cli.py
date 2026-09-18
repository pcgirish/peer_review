import fcntl
import json
import subprocess

import pytest

from review_loop.cli import main
from review_loop.state import Store, repo_paths


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def plan_files(repo):
    plan = repo / "docs" / "plan.md"
    plan.parent.mkdir()
    plan.write_text("# Plan\nDo it.")
    intent = repo / "intent.md"
    intent.write_text("Build the thing.")
    return plan, intent


def run_cli(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def plan_cmd(plan, intent, repo, *extra, author="claude"):
    args = ["plan", "--file", str(plan), "--cwd", str(repo), "--timeout", "10"]
    if intent is not None:
        args += ["--intent-file", str(intent)]
    if author is not None:
        args += ["--author", author]
    return args + list(extra)


def log_entries(fake_bin):
    return [json.loads(line) for line in fake_bin.read_text().splitlines()]


@pytest.mark.parametrize("author,reviewer", [("claude", "codex"), ("codex", "claude")])
def test_plan_review_both_directions(fake_bin, plan_files, repo, capsys, author, reviewer):
    plan, intent = plan_files
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, author=author))
    assert code == 0
    result = json.loads(out)
    assert result["status"] == "approved"
    assert result["reviewer"] == reviewer
    assert result["session_id"]
    assert result["artifact"] and result["path"] == "docs/plan.md"
    assert result["round"] == 1 and result["limit"] == 3
    assert "+" in result["version"]
    prompt = log_entries(fake_bin)[0]["argv"][-1]
    assert "Build the thing." in prompt and "# Plan\nDo it." in prompt
    assert result["version"] in prompt


def test_plan_review_changes_requested_exit_1(fake_bin, plan_files, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "changes")
    plan, intent = plan_files
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    assert code == 1
    assert json.loads(out)["review"]["blocking_findings"] == ["bad"]


def test_plan_review_error_exit_2(fake_bin, plan_files, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    plan, intent = plan_files
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    assert code == 2
    assert json.loads(out)["status"] == "error"
    assert json.loads(out)["round"] == 0


def test_known_path_reuses_artifact_and_session(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    r1 = json.loads(out1)
    resp = repo / "resp.md"
    resp.write_text("Fixed it.")
    plan.write_text("# Plan v2")
    # second call: no intent/author; response given; separate main() call = wrapper restart
    code, out2, _ = run_cli(
        capsys, *plan_cmd(plan, None, repo, "--response-file", str(resp), author=None)
    )
    assert code == 0
    r2 = json.loads(out2)
    assert r2["artifact"] == r1["artifact"]
    assert r2["session_id"] == r1["session_id"]
    assert r2["round"] == 2
    _e1, e2 = log_entries(fake_bin)
    assert "resume" in e2["argv"] or "--resume" in e2["argv"]
    assert "Fixed it." in e2["argv"][-1] and "Build the thing." in e2["argv"][-1]


def test_first_call_requires_intent_and_author(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    code, out, _ = run_cli(capsys, *plan_cmd(plan, None, repo))
    assert code == 2 and "intent" in json.loads(out)["error"]
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, author=None))
    assert code == 2 and "author" in json.loads(out)["error"]
    assert not fake_bin.exists()


def test_explicit_move_with_id(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    aid = json.loads(out1)["artifact"]
    moved = repo / "docs" / "renamed.md"
    plan.rename(moved)
    code, out2, _ = run_cli(capsys, *plan_cmd(moved, None, repo, "--id", aid, author=None))
    assert code == 0
    r2 = json.loads(out2)
    assert r2["artifact"] == aid and r2["path"] == "docs/renamed.md" and r2["round"] == 2
    # old path is now unknown; the moved path is known without --id
    code, out3, _ = run_cli(capsys, *plan_cmd(moved, None, repo, author=None))
    assert code == 0 and json.loads(out3)["artifact"] == aid


def test_move_onto_path_owned_by_other_artifact_is_error(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    other = repo / "docs" / "other.md"
    other.write_text("other")
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    run_cli(capsys, *plan_cmd(other, intent, repo))
    aid = json.loads(out1)["artifact"]
    code, out, _ = run_cli(capsys, *plan_cmd(other, None, repo, "--id", aid, author=None))
    assert code == 2 and "owned" in json.loads(out)["error"]


def test_unknown_id_is_error(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, "--id", "nope"))
    assert code == 2 and "unknown artifact" in json.loads(out)["error"]


def test_linked_worktree_shares_state(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    subprocess.run(["git", "-C", str(repo), "add", "docs/plan.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "plan"], check=True)
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "side"], check=True
    )
    wplan = wt / "docs" / "plan.md"
    assert wplan.is_file()
    code, out2, _ = run_cli(capsys, *plan_cmd(wplan, None, wt, author=None))
    assert code == 0
    assert json.loads(out2)["artifact"] == json.loads(out1)["artifact"]
    assert json.loads(out2)["session_id"] == json.loads(out1)["session_id"]


def test_file_outside_repo_is_error(fake_bin, repo, tmp_path, capsys):
    plan = tmp_path / "outside.md"
    plan.write_text("p")
    intent = tmp_path / "i.md"
    intent.write_text("i")
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    assert code == 2 and "inside" in json.loads(out)["error"]


def test_overlap_is_rejected(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    store = Store(repo_paths(repo).common_dir)
    aid = json.loads(out1)["artifact"]
    store.lock_path(aid).parent.mkdir(parents=True, exist_ok=True)
    with open(store.lock_path(aid), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, out, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    assert code == 2 and "locked" in json.loads(out)["error"]
    assert len(log_entries(fake_bin)) == 1


@pytest.mark.parametrize("author", ["claude", "codex"])
def test_interrupted_call_keeps_session_and_reruns_in_it(
    fake_bin, plan_files, repo, capsys, monkeypatch, author
):
    plan, intent = plan_files
    monkeypatch.setenv("FAKE_MODE", "hang")
    code, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, "--timeout", "1", author=author))
    r1 = json.loads(out1)
    assert code == 2 and "timed out" in r1["error"] and r1["session_id"] and r1["round"] == 0
    monkeypatch.delenv("FAKE_MODE")
    code, out2, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    r2 = json.loads(out2)
    assert code == 0 and r2["session_id"] == r1["session_id"] and r2["round"] == 1
    argv = log_entries(fake_bin)[1]["argv"]
    assert "resume" in argv or "--resume" in argv


def test_first_call_session_id_is_saved_while_reviewer_runs(
    fake_bin, plan_files, repo, capsys, monkeypatch
):
    """Kill -9 of the helper mid-call must not lose the session: it is on disk before the call ends."""
    plan, intent = plan_files
    monkeypatch.setenv("FAKE_MODE", "hang")
    store = Store(repo_paths(repo).common_dir)
    seen = {}

    real_save = Store.save

    def spy(self, state):
        real_save(self, state)
        if state.session_id and "sid" not in seen:
            seen["sid"] = state.session_id
            seen["log_lines"] = len(fake_bin.read_text().splitlines()) if fake_bin.exists() else 0
            seen["outcome_recorded"] = bool(state.history)

    monkeypatch.setattr(Store, "save", spy)
    _, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, "--timeout", "1", author="claude"))
    r = json.loads(out)
    assert seen["sid"] == r["session_id"]
    assert seen["outcome_recorded"] is False, "id saved before the outcome, not with it"
    assert store.load(r["artifact"]).session_id == r["session_id"]


def test_session_loss_is_error_until_reset(fake_bin, plan_files, repo, capsys, monkeypatch):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, author="codex"))
    r1 = json.loads(out1)
    monkeypatch.setenv("FAKE_MODE", "newsession")
    code, out2, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    r2 = json.loads(out2)
    assert code == 2 and "reset" in r2["error"]
    assert r2["session_id"] == r1["session_id"] and r2["round"] == 1
    # still stuck on the saved session
    code, _, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    assert code == 2
    code, out, _ = run_cli(capsys, "reset", r1["artifact"], "--cwd", str(repo))
    assert code == 0
    st = json.loads(out)
    assert st["session_id"] is None and st["rounds"] == 0
    assert st["retired_sessions"] == [r1["session_id"]]
    assert len(st["history"]) >= 1
    monkeypatch.delenv("FAKE_MODE")
    code, out3, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    r3 = json.loads(out3)
    assert code == 0 and r3["session_id"] != r1["session_id"] and r3["round"] == 1
    assert "resume" not in log_entries(fake_bin)[-1]["argv"]
    assert "--resume" not in log_entries(fake_bin)[-1]["argv"]


def test_round_limit_then_extend(fake_bin, plan_files, repo, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "changes")
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    aid = json.loads(out1)["artifact"]
    for _ in range(2):
        code, _, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
        assert code == 1
    code, out, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    assert code == 2 and "limit" in json.loads(out)["error"]
    assert len(log_entries(fake_bin)) == 3
    code, out, _ = run_cli(capsys, "extend", aid, "--to", "4", "--cwd", str(repo))
    assert code == 0 and json.loads(out)["limit"] == 4
    code, out, _ = run_cli(capsys, "extend", "docs/plan.md", "--to", "2", "--cwd", str(repo))
    assert code == 2
    code, out, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    assert code == 1 and json.loads(out)["round"] == 4


def test_status_by_id_and_path(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    r1 = json.loads(out1)
    for ref in (r1["artifact"], "docs/plan.md"):
        code, out, _ = run_cli(capsys, "status", ref, "--cwd", str(repo))
        assert code == 0
        st = json.loads(out)
        assert st["id"] == r1["artifact"] and st["session_id"] == r1["session_id"]
        assert st["rounds"] == 1 and st["reviewer"] == "codex" and st["author"] == "claude"
        assert st["history"][0]["status"] == "approved"
        assert st["history"][0]["version"] == r1["version"]
    code, out, _ = run_cli(capsys, "status", "nope", "--cwd", str(repo))
    assert code == 2


def test_recursion_guard_exit_2(fake_bin, plan_files, repo, capsys, monkeypatch):
    monkeypatch.setenv("REVIEW_LOOP_REVIEWER", "1")
    plan, intent = plan_files
    code, out, _err = run_cli(capsys, *plan_cmd(plan, intent, repo))
    assert code == 2
    assert "REVIEW_LOOP_REVIEWER" in json.loads(out)["error"]


def test_missing_plan_file_exit_2(fake_bin, repo, capsys):
    intent = repo / "i.md"
    intent.write_text("x")
    code, out, _ = run_cli(capsys, *plan_cmd(repo / "nope.md", intent, repo))
    assert code == 2
    assert "nope.md" in json.loads(out)["error"]


@pytest.mark.parametrize("value", ["0", "-5", "inf", "nan"])
def test_invalid_timeout_rejected(fake_bin, plan_files, repo, capsys, value):
    plan, intent = plan_files
    with pytest.raises(SystemExit) as e:
        main(plan_cmd(plan, intent, repo, "--timeout", value))
    assert e.value.code == 2
    assert not fake_bin.exists()


def _hold_lock(store, aid):
    store.lock_path(aid).parent.mkdir(parents=True, exist_ok=True)
    fh = open(store.lock_path(aid), "w")  # noqa: SIM115 - held deliberately by the test
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fh


def test_stale_pre_lock_state_cannot_overwrite_a_completed_update(
    fake_bin, plan_files, repo, capsys, monkeypatch
):
    """State must be (re)loaded under the artifact lock, not before it."""
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    aid = json.loads(out1)["artifact"]
    store = Store(repo_paths(repo).common_dir)

    real_lock = Store.lock

    @__import__("contextlib").contextmanager
    def lock_then_bump(self, artifact_id):
        # another process completes a round between this process's pre-lock work and its lock
        other = Store(self.root.parent).load(artifact_id)
        other.rounds = 2
        other.history.append({"round": 2, "status": "approved"})
        other.session_id = "newer-session"
        Store(self.root.parent).save(other)
        with real_lock(self, artifact_id):
            yield

    monkeypatch.setattr(Store, "lock", lock_then_bump)
    code, out, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    assert code in (0, 1)
    final = store.load(aid)
    assert final.rounds == 3 and len(final.history) == 3
    assert json.loads(out)["session_id"] == "newer-session"


def test_move_is_rejected_unchanged_while_artifact_is_locked(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    aid = json.loads(out1)["artifact"]
    store = Store(repo_paths(repo).common_dir)
    before_state = store.load(aid).to_dict()
    before_index = store.index_path.read_text()
    moved = repo / "docs" / "renamed.md"
    plan.rename(moved)
    fh = _hold_lock(store, aid)
    try:
        code, out, _ = run_cli(capsys, *plan_cmd(moved, None, repo, "--id", aid, author=None))
    finally:
        fh.close()
    assert code == 2 and "locked" in json.loads(out)["error"]
    assert store.load(aid).to_dict() == before_state
    assert store.index_path.read_text() == before_index


@pytest.mark.parametrize("cmd", ["reset", "extend"])
def test_reset_and_extend_reload_under_lock(fake_bin, plan_files, repo, capsys, monkeypatch, cmd):
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    aid = json.loads(out1)["artifact"]
    store = Store(repo_paths(repo).common_dir)
    real_lock = Store.lock

    @__import__("contextlib").contextmanager
    def lock_then_bump(self, artifact_id):
        other = Store(self.root.parent).load(artifact_id)
        other.history.append({"round": 2, "status": "approved"})
        other.rounds = 2
        Store(self.root.parent).save(other)
        with real_lock(self, artifact_id):
            yield

    monkeypatch.setattr(Store, "lock", lock_then_bump)
    extra = ["--to", "5"] if cmd == "extend" else []
    code, _, _ = run_cli(capsys, cmd, aid, "--cwd", str(repo), *extra)
    assert code == 0
    final = store.load(aid)
    assert len(final.history) == 2
    if cmd == "extend":
        assert final.rounds == 2 and final.limit == 5
    else:
        assert final.rounds == 0


@pytest.mark.parametrize("cmd", ["reset", "extend"])
def test_reset_and_extend_act_on_the_locked_artifact_even_if_path_is_remapped(
    fake_bin, plan_files, repo, capsys, monkeypatch, cmd
):
    plan, intent = plan_files
    other = repo / "docs" / "other.md"
    other.write_text("other")
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    _, out2, _ = run_cli(capsys, *plan_cmd(other, intent, repo))
    a, b = json.loads(out1)["artifact"], json.loads(out2)["artifact"]
    store = Store(repo_paths(repo).common_dir)
    real_lock = Store.lock

    @__import__("contextlib").contextmanager
    def lock_then_remap(self, artifact_id):
        # between resolution and lock, docs/plan.md is re-pointed at artifact B
        s = Store(self.root.parent)
        idx = s._index()
        idx["plan:docs/plan.md"] = b
        s._write_index(idx)
        with real_lock(self, artifact_id):
            yield

    monkeypatch.setattr(Store, "lock", lock_then_remap)
    extra = ["--to", "5"] if cmd == "extend" else []
    code, out, _ = run_cli(capsys, cmd, "docs/plan.md", "--cwd", str(repo), *extra)
    assert code == 0 and json.loads(out)["id"] == a
    assert store.load(b).limit == 3 and store.load(b).rounds == 1


# --- Step 4: snapshots and stale checks ---------------------------------------


def _mutate_during_review(monkeypatch, mutate):
    """Run the real reviewer, but change author inputs while it is 'thinking'."""
    import review_loop.cli as cli_mod

    real = cli_mod.run_review
    pending = [mutate]  # fires on the first review only

    def wrapped(*a, **kw):
        if pending:
            pending.pop()()
        return real(*a, **kw)

    monkeypatch.setattr(cli_mod, "run_review", wrapped)


def test_snapshot_is_written_per_version_and_prompt_reviews_it(fake_bin, plan_files, repo, capsys):
    plan, intent = plan_files
    _, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    r = json.loads(out)
    store = Store(repo_paths(repo).common_dir)
    snap = store.snapshot_dir(r["artifact"]) / r["version"]
    assert (snap / "plan.md").read_text() == plan.read_text()
    assert (snap / "intent.md").read_text() == intent.read_text()
    st = store.load(r["artifact"])
    assert st.history[0]["snapshot"] == str(snap)
    assert st.history[0]["head"] == _git(repo, "rev-parse", "HEAD")
    prompt = log_entries(fake_bin)[0]["argv"][-1]
    assert str(snap / "plan.md") in prompt, "reviewer is pointed at the frozen snapshot"


@pytest.mark.parametrize("what", ["plan", "intent"])
def test_input_changed_during_review_is_stale_and_never_approves(
    fake_bin, plan_files, repo, capsys, monkeypatch, what
):
    plan, intent = plan_files
    target = plan if what == "plan" else intent
    _mutate_during_review(monkeypatch, lambda: target.write_text("edited mid-review"))
    code, out, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    r = json.loads(out)
    assert code == 3 and r["status"] == "stale"
    assert r["review"] is None and "changed during review" in r["error"]
    assert r["round"] == 0
    st = Store(repo_paths(repo).common_dir).load(r["artifact"])
    assert st.rounds == 0 and st.last_status == "stale" and st.reviewed_version is None
    assert st.history[0]["status"] == "stale" and st.history[0]["round"] is None
    assert st.session_id == r["session_id"], "stale keeps the session"


def test_stale_when_only_intent_saved_state_changes_via_flag(
    fake_bin, plan_files, repo, capsys, monkeypatch
):
    """Intent read from the flag is the reviewed intent; a later saved edit is not."""
    plan, intent = plan_files
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    intent.write_text("new intent")
    code, out2, _ = run_cli(capsys, *plan_cmd(plan, intent, repo, author=None))
    assert code == 0 and json.loads(out2)["version"] != json.loads(out1)["version"]


def test_stale_result_does_not_retry_and_next_call_resumes(
    fake_bin, plan_files, repo, capsys, monkeypatch
):
    plan, intent = plan_files
    _mutate_during_review(monkeypatch, lambda: plan.write_text("edited"))
    _, out1, _ = run_cli(capsys, *plan_cmd(plan, intent, repo))
    assert len(log_entries(fake_bin)) == 1
    code, out2, _ = run_cli(capsys, *plan_cmd(plan, None, repo, author=None))
    r1, r2 = json.loads(out1), json.loads(out2)
    assert code == 0 and r2["round"] == 1 and r2["session_id"] == r1["session_id"]
    assert "resume" in log_entries(fake_bin)[1]["argv"]


def test_git_helper_in_cli_tests(repo):
    assert len(_git(repo, "rev-parse", "HEAD")) == 40


# --- Step 5: PR loop -------------------------------------------------------------


def pr_cmd(fake_pr, *extra, intent=True, author="claude", test_command="true"):
    args = ["pr", "7", "--cwd", str(fake_pr.repo), "--timeout", "10"]
    if intent:
        i = fake_pr.repo / "intent.md"
        i.write_text("Add f.")
        args += ["--intent-file", str(i)]
    if author:
        args += ["--author", author]
    if test_command:
        args += ["--test-command", test_command]
    return args + list(extra)


@pytest.fixture
def pr_env(monkeypatch):
    monkeypatch.setenv("FAKE_PR", "1")  # fake reviewers include tests_run


@pytest.mark.parametrize("author,reviewer", [("claude", "codex"), ("codex", "claude")])
def test_pr_review_reviews_the_fetched_head(fake_pr, pr_env, capsys, fake_bin, author, reviewer):
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr, author=author))
    r = json.loads(out)
    assert code == 0 and r["status"] == "approved" and r["reviewer"] == reviewer
    assert r["pr"] == 7 and r["version"].startswith(fake_pr.head_sha + "+")
    store = Store(repo_paths(fake_pr.repo).common_dir)
    st = store.load(r["artifact"])
    assert st.kind == "pr" and st.path == "7" and st.rounds == 1
    h = st.history[0]
    assert h["pr_head"] == fake_pr.head_sha and h["pr_base"] == fake_pr.base_sha
    prompt = log_entries(fake_bin)[0]["argv"][-1]
    assert "diff --git a/f.py" in prompt and fake_pr.head_sha in prompt
    wt = store.worktree_dir(r["artifact"])
    assert str(wt) in prompt and _git(wt, "rev-parse", "HEAD") == fake_pr.head_sha
    assert "### `true` — exit code 0" in prompt


def test_pr_test_command_runs_in_the_pr_worktree_and_is_saved(fake_pr, pr_env, capsys, fake_bin):
    cmd = "cat f.py && pwd"
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr, "--test-command", cmd))
    r = json.loads(out)
    assert code == 0
    prompt = log_entries(fake_bin)[0]["argv"][-1]
    wt = Store(repo_paths(fake_pr.repo).common_dir).worktree_dir(r["artifact"])
    assert "No tests were run by the helper" not in prompt
    assert f"### `{cmd}` — exit code 0" in prompt and "# feature work" in prompt
    assert str(wt.resolve()) in prompt
    # second call reuses the saved command
    run_cli(capsys, *pr_cmd(fake_pr, intent=False, author=None, test_command=None))
    assert f"### `{cmd}`" in log_entries(fake_bin)[1]["argv"][-1]


def test_pr_head_moved_during_review_is_stale(fake_pr, pr_env, capsys, monkeypatch):
    _mutate_during_review(monkeypatch, lambda: fake_pr.write_state(pr={"headRefOid": "b" * 40}))
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr))
    r = json.loads(out)
    assert code == 3 and r["status"] == "stale" and r["round"] == 0
    assert "PR head moved during review" in r["error"]


def test_pr_base_moved_during_review_is_not_stale(fake_pr, pr_env, capsys, monkeypatch):
    _mutate_during_review(monkeypatch, lambda: fake_pr.write_state(pr={"baseRefOid": "c" * 40}))
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr))
    assert code == 0 and json.loads(out)["status"] == "approved"


def test_pr_pushed_revision_reuses_session_and_reviews_new_head(fake_pr, pr_env, capsys, fake_bin):
    _, out1, _ = run_cli(capsys, *pr_cmd(fake_pr))
    r1 = json.loads(out1)
    new = fake_pr.push_revision("address review")
    fake_pr.write_state()
    resp = fake_pr.repo / "resp.md"
    resp.write_text("Done.")
    code, out2, _ = run_cli(
        capsys, *pr_cmd(fake_pr, "--response-file", str(resp), intent=False, author=None)
    )
    r2 = json.loads(out2)
    assert code == 0 and r2["artifact"] == r1["artifact"] and r2["round"] == 2
    assert r2["session_id"] == r1["session_id"] and r2["version"].startswith(new + "+")
    assert "resume" in log_entries(fake_bin)[1]["argv"]
    wt = Store(repo_paths(fake_pr.repo).common_dir).worktree_dir(r1["artifact"])
    assert _git(wt, "rev-parse", "HEAD") == new


def test_pr_gh_and_head_disagree_is_error(fake_pr, pr_env, capsys):
    fake_pr.write_state(pr={"headRefOid": "d" * 40})
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr))
    assert code == 2 and "is not the head of refs/pull/7/head" in json.loads(out)["error"]


def test_pr_post_comment_is_opt_in_and_posted_by_the_helper(fake_pr, pr_env, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "changes")
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr))
    assert code == 1 and "comment" not in json.loads(out)
    assert not [c for c in fake_pr.gh_calls() if "comment" in c]
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr, "--post-comment", intent=False, author=None))
    r = json.loads(out)
    assert code == 1 and r["comment"].endswith("#issuecomment-1")
    posted = [c for c in fake_pr.gh_calls() if "comment" in c]
    assert len(posted) == 1
    body = posted[0]["comment"]
    assert "review-loop" in body and "codex" in body and r["version"] in body
    assert "round 2/3" in body and "changes_requested" in body
    assert "reply on this PR" in body
    st = Store(repo_paths(fake_pr.repo).common_dir).load(r["artifact"])
    assert st.history[1]["comment"] == r["comment"]


def test_pr_post_comment_failure_keeps_verdict(fake_pr, pr_env, capsys, monkeypatch):
    import review_loop.cli as cli_mod

    def boom(*a, **kw):
        raise cli_mod.GitHubError("gh: rate limited")

    monkeypatch.setattr(cli_mod, "post_comment", boom)
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr, "--post-comment"))
    r = json.loads(out)
    assert code == 0 and r["status"] == "approved" and "rate limited" in r["comment_error"]


def test_pr_post_comment_not_sent_for_error_or_stale(fake_pr, pr_env, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "garbage")
    code, _, _ = run_cli(capsys, *pr_cmd(fake_pr, "--post-comment"))
    assert code == 2
    assert not [c for c in fake_pr.gh_calls() if "comment" in c]


def test_pr_status_by_number(fake_pr, pr_env, capsys):
    _, out1, _ = run_cli(capsys, *pr_cmd(fake_pr))
    code, out, _ = run_cli(capsys, "status", "7", "--cwd", str(fake_pr.repo))
    assert code == 0 and json.loads(out)["id"] == json.loads(out1)["artifact"]


def test_pr_first_review_requires_a_test_command(fake_pr, pr_env, capsys, fake_bin):
    # the plan's contract: the helper runs the project tests before every PR review
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr, test_command=None))
    assert code == 2 and "--test-command is required on the first review of a PR" in out
    assert not fake_bin.exists()  # no reviewer was spawned


def test_pr_intent_changed_during_review_is_stale(fake_pr, pr_env, capsys, monkeypatch):
    intent = fake_pr.repo / "intent.md"
    _mutate_during_review(monkeypatch, lambda: intent.write_text("Add f, differently."))
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr))
    r = json.loads(out)
    assert code == 3 and r["status"] == "stale" and r["round"] == 0
    assert "intent changed during review" in r["error"]


def test_pr_worktree_is_restored_to_the_head_after_tests(fake_pr, pr_env, capsys, fake_bin):
    cmd = "echo dirty >> f.py && echo junk > junk.txt"
    code, out, _ = run_cli(capsys, *pr_cmd(fake_pr, "--test-command", cmd))
    r = json.loads(out)
    assert code == 0
    wt = Store(repo_paths(fake_pr.repo).common_dir).worktree_dir(r["artifact"])
    assert (wt / "f.py").read_text() == "# feature work\n"
    assert not (wt / "junk.txt").exists()
    assert _git(wt, "status", "--porcelain") == ""
