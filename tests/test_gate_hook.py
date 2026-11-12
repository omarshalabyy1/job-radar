"""A push to main needs `bash check.sh` to have printed GATE: PASS for exactly the content pushed: .githooks/pre-push
compares the pushed commit's tree with the one check.sh recorded in .git/gate-pass. Each test runs the hook in a
throwaway repository, the way git runs it (the pushed refs on stdin)."""
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / ".githooks" / "pre-push"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="the hook is a bash script")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(repo: Path, text: str) -> None:
    (repo / "a.txt").write_text(text)
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", text)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    commit(tmp_path, "one")
    return tmp_path


def gate_passed(repo: Path) -> None:
    (repo / ".git" / "gate-pass").write_text(git(repo, "rev-parse", "HEAD^{tree}") + "\n")


def push(repo: Path, to: str = "refs/heads/main") -> subprocess.CompletedProcess:
    pushed = f"refs/heads/main {git(repo, 'rev-parse', 'HEAD')} {to} {'0' * 40}\n"
    return subprocess.run([BASH, HOOK.as_posix(), "origin", "https://example.com/repo.git"], cwd=repo, input=pushed,
                          capture_output=True, text=True)


def test_main_is_not_pushed_when_the_gate_never_passed(repo):
    refused = push(repo)
    assert refused.returncode == 1 and "check.sh" in refused.stderr


def test_main_is_pushed_when_the_gate_passed_for_this_content(repo):
    gate_passed(repo)
    assert push(repo).returncode == 0


def test_a_pass_for_earlier_content_does_not_count(repo):
    gate_passed(repo)
    commit(repo, "two")  # changed after the gate ran
    assert push(repo).returncode == 1


def test_a_commit_of_the_content_the_gate_saw_is_pushed(repo):
    """The gate ran on the working copy, then it was committed as it was: the same tree, another commit."""
    gate_passed(repo)
    git(repo, "commit", "-q", "--amend", "-m", "the same content, reworded")
    assert push(repo).returncode == 0


def test_another_branch_is_not_checked(repo):
    assert push(repo, to="refs/heads/feature").returncode == 0
