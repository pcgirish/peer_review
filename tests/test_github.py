import pytest

from review_loop.github import GitHubError, checkout_revision, fetch_pr, post_comment, pr_diff


def test_fetch_pr_reads_head_base_and_metadata(fake_pr):
    pr = fetch_pr(7, cwd=fake_pr.repo)
    assert pr.number == 7 and pr.head_sha == fake_pr.head_sha and pr.base_sha == fake_pr.base_sha
    assert pr.base_ref == "main" and pr.title == "Add f" and pr.url.endswith("/pull/7")


def test_pr_diff_and_comment(fake_pr):
    assert pr_diff(7, cwd=fake_pr.repo).startswith("diff --git")
    url = post_comment(7, "hello **world**", cwd=fake_pr.repo)
    assert url.endswith("#issuecomment-1")
    assert fake_pr.gh_calls()[-1] == {"comment": "hello **world**", "number": "7"}


def test_gh_failure_is_github_error(fake_pr):
    fake_pr.write_state(fail=True)
    with pytest.raises(GitHubError, match="HTTP 502"):
        fetch_pr(7, cwd=fake_pr.repo)


def test_checkout_revision_creates_and_updates_a_detached_worktree(fake_pr, tmp_path):
    from subprocess import run

    dest = tmp_path / "wt"
    checkout_revision(7, fake_pr.head_sha, dest=dest, cwd=fake_pr.repo)
    head = run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    assert head.stdout.strip() == fake_pr.head_sha and (dest / "f.py").exists()
    new = fake_pr.push_revision("second")
    checkout_revision(7, new, dest=dest, cwd=fake_pr.repo)
    head = run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    assert head.stdout.strip() == new


def test_checkout_revision_rejects_sha_that_is_not_the_pr_head(fake_pr, tmp_path):
    with pytest.raises(GitHubError, match="is not the head of refs/pull/7/head"):
        checkout_revision(7, fake_pr.base_sha, dest=tmp_path / "wt", cwd=fake_pr.repo)
