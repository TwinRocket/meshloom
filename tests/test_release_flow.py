"""Release flow: a release PR, then a tag pushed by a human. CI never pushes main
or creates a tag, so both can be protected by rulesets.

The guards of tag_release.sh and create_github_release.sh run for real against a
throwaway repository and a bare `origin`; `gh` is a stub on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build"
WORKFLOWS = ROOT / ".github" / "workflows"
VERSION = "9.8.7"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _env(tmp_path: Path, check_state: str = "completed/success") -> dict[str, str]:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    gh = stub_dir / "gh"
    # tag_release.sh asks `gh api ... --jq ...` for "<status>/<conclusion>".
    gh.write_text('#!/bin/sh\n[ -z "$GH_STUB_FAIL" ] || exit 1\necho "$GH_STUB_STATE"\n')
    gh.chmod(0o755)
    return {
        **os.environ,
        "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_STUB_STATE": check_state,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_versions(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n')
    (repo / "frontend").mkdir(exist_ok=True)
    (repo / "frontend" / "package.json").write_text(f'{{"name": "x", "version": "{version}"}}\n')
    (repo / "meshloom").mkdir(exist_ok=True)
    (repo / "meshloom" / "config.yaml").write_text(f'name: x\nversion: "{version}"\n')
    (repo / "meshloom" / "Dockerfile").write_text(f"FROM ghcr.io/twinrocket/meshloom:{version}\n")


@pytest.fixture
def release_repo(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """A clone on main at origin/main whose HEAD is the merged release VERSION."""
    env = _env(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], env=env, check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "main")
    (repo / "scripts" / "build").mkdir(parents=True)
    for name in (
        "tag_release.sh",
        "release_common.sh",
        "check_version_consistency.sh",
        "create_github_release.sh",
    ):
        shutil.copy2(BUILD / name, repo / "scripts" / "build" / name)
    _write_versions(repo, VERSION)
    (repo / "CHANGELOG.md").write_text(f"# Changelog\n\n## [{VERSION}] - 2026-10-11\n\n* A fix\n")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", f"Updating changelog + build for {VERSION}")
    _git(repo, env, "remote", "add", "origin", str(origin))
    _git(repo, env, "push", "-q", "origin", "main")
    _git(repo, env, "fetch", "-q", "origin")
    return repo, origin, env


def _tag_release(
    repo: Path, env: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / "build" / "tag_release.sh"), *args],
        cwd=repo,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _remote_tags(repo: Path, env: dict[str, str]) -> str:
    return _git(repo, env, "ls-remote", "--tags", "origin")


def test_dry_run_passes_every_check_and_creates_nothing(release_repo) -> None:
    repo, _, env = release_repo
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout
    assert _git(repo, env, "tag", "--list") == ""
    assert _remote_tags(repo, env) == ""


def test_confirmed_push_creates_an_annotated_unsigned_tag_on_head(release_repo) -> None:
    repo, _, env = release_repo
    result = _tag_release(repo, env, VERSION, stdin="y\n")
    assert result.returncode == 0, result.stderr
    assert _git(repo, env, "cat-file", "-t", VERSION) == "tag"
    assert _git(repo, env, "rev-parse", f"{VERSION}^{{commit}}") == _git(
        repo, env, "rev-parse", "HEAD"
    )
    body = _git(repo, env, "cat-file", "-p", VERSION)
    assert "BEGIN PGP SIGNATURE" not in body
    assert "* A fix" in body  # the CHANGELOG section (git strips the "## [X]" line)
    assert f"refs/tags/{VERSION}" in _remote_tags(repo, env)


def test_declined_confirmation_pushes_nothing_and_drops_the_local_tag(release_repo) -> None:
    repo, _, env = release_repo
    result = _tag_release(repo, env, VERSION, stdin="n\n")
    assert result.returncode != 0
    assert _git(repo, env, "tag", "--list") == ""
    assert _remote_tags(repo, env) == ""


def test_refuses_off_main(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "switch", "-q", "-c", f"release/{VERSION}")
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "cut from main" in result.stderr


def test_refuses_a_dirty_tree(release_repo) -> None:
    repo, _, env = release_repo
    (repo / "stray.txt").write_text("x")
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "not clean" in result.stderr


def test_refuses_main_ahead_of_origin(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", "local only")
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "origin/main" in result.stderr


def test_refuses_when_head_is_not_the_release(release_repo) -> None:
    repo, _, env = release_repo
    result = _tag_release(repo, env, "9.8.8", "--dry-run")
    assert result.returncode != 0
    assert "does not carry version 9.8.8" in result.stderr


def test_refuses_a_tag_that_exists_on_origin(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "tag", VERSION)
    _git(repo, env, "push", "-q", "origin", VERSION)
    _git(repo, env, "tag", "-d", VERSION)
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "already exists on origin" in result.stderr


def test_refuses_a_tag_that_exists_locally(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "tag", VERSION)
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "already exists locally" in result.stderr


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ("missing", "No 'all-quality' check"),
        ("in_progress/null", "still running"),
        ("completed/failure", "is not green"),
        ("completed/cancelled", "is not green"),
    ],
)
def test_refuses_unless_all_quality_is_green(release_repo, state: str, message: str) -> None:
    repo, _, env = release_repo
    result = _tag_release(repo, {**env, "GH_STUB_STATE": state}, VERSION, "--dry-run")
    assert result.returncode != 0
    assert message in result.stderr


def test_refuses_when_the_checks_cannot_be_read(release_repo) -> None:
    repo, _, env = release_repo
    result = _tag_release(repo, {**env, "GH_STUB_FAIL": "1"}, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "Could not read the checks" in result.stderr


def _create_release(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    asset = repo.parent / "asset.zip"
    asset.write_text("x")
    return subprocess.run(
        [
            "bash",
            str(repo / "scripts" / "build" / "create_github_release.sh"),
            "--version",
            VERSION,
            "--asset",
            str(asset),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_ci_release_fails_without_the_tag_instead_of_creating_it(release_repo) -> None:
    repo, _, env = release_repo
    result = _create_release(repo, env)
    assert result.returncode != 0
    assert "does not exist on origin" in result.stderr
    assert _git(repo, env, "tag", "--list") == ""
    assert _remote_tags(repo, env) == ""


def test_ci_release_fails_when_the_tag_is_not_the_built_commit(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "tag", "-a", VERSION, "-m", "x")
    _git(repo, env, "push", "-q", "origin", VERSION)
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", "later")
    result = _create_release(repo, env)
    assert result.returncode != 0
    assert "Run the workflow on the tag" in result.stderr


def test_no_script_or_workflow_creates_tags_or_pushes() -> None:
    create = (BUILD / "create_github_release.sh").read_text(encoding="utf-8")
    assert "git tag" not in create and "git push" not in create and "--full-git-hash" not in create
    for workflow in WORKFLOWS.glob("*.yml"):
        text = workflow.read_text(encoding="utf-8")
        assert "git push" not in text, workflow.name
        assert "git tag " not in text, workflow.name


def test_release_workflow_dispatch_only_republishes_a_tag() -> None:
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "inputs.version" not in release
    publish = release[release.index("scripts/build/create_github_release.sh") :]
    assert "--full-git-hash" not in publish.split("\n\n")[0]
    assert 'if [ "$REF_TYPE" != "tag" ]' in release


def test_prepare_release_never_touches_main_or_tags() -> None:
    prepare = (BUILD / "prepare_release.sh").read_text(encoding="utf-8")
    assert 'release_switch_carrying_changelog "$BRANCH" origin/main' in prepare
    assert 'git push --set-upstream origin "$BRANCH"' in prepare
    assert "gh pr create" in prepare and "--body-file" in prepare
    assert "git tag" not in prepare
    assert "git push origin main" not in prepare


def test_retired_publish_script_points_at_the_new_flow() -> None:
    result = subprocess.run(
        ["bash", str(BUILD / "publish.sh")], capture_output=True, text=True, check=False
    )
    assert result.returncode == 1
    assert "prepare_release.sh" in result.stderr and "tag_release.sh" in result.stderr


def test_docker_pr_build_runs_but_pushes_nothing() -> None:
    docker = (WORKFLOWS / "docker.yml").read_text(encoding="utf-8")
    build = docker[docker.index("\n  build:\n") : docker.index("\n  merge:\n")]
    # `quality` is skipped on PRs; without an explicit status check the build is too.
    assert "if: ${{ !cancelled() && needs.matrix.result == 'success' }}" in build
    assert "if: github.event_name != 'pull_request'" in build  # GHCR login and digests
    assert "github.event_name != 'pull_request' && format('type=image" in build
    assert "|| 'type=cacheonly'" in build
    merge = docker[docker.index("\n  merge:\n") :]
    assert "if: github.event_name != 'pull_request'" in merge.split("steps:")[0]


def _carry(
    repo: Path, env: dict[str, str], branch: str = "release/9.8.8"
) -> subprocess.CompletedProcess[str]:
    script = (
        f'source "{BUILD / "release_common.sh"}"; '
        "release_tree_clean_but_changelog || { echo DIRTY; exit 3; }; "
        f'release_switch_carrying_changelog "{branch}" origin/main'
    )
    return subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True, check=False
    )


def test_prepare_carries_an_uncommitted_changelog_onto_the_release_branch(release_repo) -> None:
    repo, _, env = release_repo
    notes = "# Changelog\n\n## [9.8.8] - 2026-10-12\n\n* Hand-written notes\n\n"
    old = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(notes + old.removeprefix("# Changelog\n\n"))
    result = _carry(repo, env)
    assert result.returncode == 0, result.stderr
    assert _git(repo, env, "rev-parse", "--abbrev-ref", "HEAD") == "release/9.8.8"
    assert "## [9.8.8]" in (repo / "CHANGELOG.md").read_text()
    assert _git(repo, env, "status", "--porcelain") == "M CHANGELOG.md"


def test_prepare_refuses_local_changes_other_than_the_changelog(release_repo) -> None:
    repo, _, env = release_repo
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## [9.8.8] - 2026-10-12\n\n* Notes\n")
    (repo / "stray.txt").write_text("unreviewed\n")
    result = _carry(repo, env)
    assert result.returncode == 3 and "DIRTY" in result.stdout
    assert _git(repo, env, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_prepare_keeps_the_changelog_edit_when_it_does_not_apply(release_repo) -> None:
    repo, _, env = release_repo
    # origin/main rewrote CHANGELOG.md after the local clone was taken.
    _git(repo, env, "switch", "-q", "-c", "upstream")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\nRewritten upstream\n")
    _git(repo, env, "commit", "-q", "-am", "rewrite")
    _git(repo, env, "push", "-q", "origin", "upstream:main")
    _git(repo, env, "switch", "-q", "main")
    _git(repo, env, "fetch", "-q", "origin")
    edit = (repo / "CHANGELOG.md").read_text().replace("* A fix", "* A fix\n* Another one")
    (repo / "CHANGELOG.md").write_text(edit)
    result = _carry(repo, env)
    assert result.returncode != 0
    assert "saved in" in result.stderr
    backup = Path(result.stderr.split("saved in ")[1].split(";")[0])
    assert backup.read_text() == edit
