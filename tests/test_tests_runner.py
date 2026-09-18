import sys

from review_loop.tests_runner import run_tests


def test_run_tests_captures_output_and_exit_code(tmp_path):
    res = run_tests(
        f"{sys.executable} -c \"print('hi'); raise SystemExit(3)\"", cwd=tmp_path, timeout=10
    )
    assert res.exit_code == 3
    assert "hi" in res.output
    assert res.run_by == "helper"
    assert res.command.startswith(sys.executable)


def test_run_tests_timeout_reports_nonzero(tmp_path):
    res = run_tests(f"{sys.executable} -c 'import time; time.sleep(30)'", cwd=tmp_path, timeout=1)
    assert res.exit_code != 0
    assert "timed out" in res.output


def test_run_tests_truncates_long_output(tmp_path):
    res = run_tests(
        f"{sys.executable} -c \"print('x'*100000)\"", cwd=tmp_path, timeout=10, max_chars=1000
    )
    assert len(res.output) < 1200
    assert "truncated" in res.output
