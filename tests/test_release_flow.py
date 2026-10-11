"""Release flow: an admin runs scripts/build/publish.sh X.Y.Z, which pushes the
release commit to main, then the X.Y.Z tag. CI never pushes main or creates a
tag; tag_release.sh only recovers a run that pushed main but not the tag.

publish.sh, tag_release.sh and create_github_release.sh run for real against a
throwaway repository and a bare `origin`; `gh` and `uv` are stubs on PATH.
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
    # publish.sh runs `uv sync` to refresh uv.lock: the stub only touches it, so the
    # tests can see that the lock is part of the release commit.
    uv = stub_dir / "uv"
    uv.write_text('#!/bin/sh\n[ "$1" = sync ] && echo "# synced" >> uv.lock\nexit 0\n')
    uv.chmod(0o755)
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
        "publish.sh",
        "tag_release.sh",
        "release_common.sh",
        "check_version_consistency.sh",
        "create_github_release.sh",
    ):
        shutil.copy2(BUILD / name, repo / "scripts" / "build" / name)
    _write_versions(repo, VERSION)
    (repo / "uv.lock").write_text("version = 1\n")
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


# publish.sh: the one command that cuts a release.

NEXT = "9.8.8"
NOTES = f"## [{NEXT}] - 2026-10-12\n\n* Hand-written notes\n\n"
RELEASE_FILES = {
    "CHANGELOG.md",
    "frontend/package.json",
    "meshloom/Dockerfile",
    "meshloom/config.yaml",
    "pyproject.toml",
    "uv.lock",
}


def _write_notes(repo: Path) -> None:
    """The usual flow: the [X.Y.Z] section is written by hand, not committed."""
    old = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n" + NOTES + old.removeprefix("# Changelog\n\n")
    )


def _publish(
    repo: Path, env: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            str(repo / "scripts" / "build" / "publish.sh"),
            *args,
            "--skip-quality",
            "--skip-licenses",
        ],
        cwd=repo,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def _reject_pushes_to(origin: Path, ref_prefix: str) -> None:
    hook = origin / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        f'  case "$ref" in {ref_prefix}*) echo "rejected $ref" >&2; exit 1 ;; esac\n'
        "done\n"
    )
    hook.chmod(0o755)


def test_publish_commits_the_uncommitted_changelog_pushes_main_then_the_tag(
    release_repo,
) -> None:
    repo, _, env = release_repo
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode == 0, result.stderr
    assert f"CHANGELOG.md already has [{NEXT}]; leaving it as-is." in result.stdout

    head = _git(repo, env, "rev-parse", "HEAD")
    assert _git(repo, env, "ls-remote", "origin", "refs/heads/main").split()[0] == head
    assert _git(repo, env, "log", "-1", "--format=%s") == f"Updating changelog + build for {NEXT}"
    changed = _git(repo, env, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    assert set(changed.splitlines()) == RELEASE_FILES
    assert _git(repo, env, "status", "--porcelain") == ""
    assert "* Hand-written notes" in _git(repo, env, "show", "HEAD:CHANGELOG.md")
    for path in ("pyproject.toml", "frontend/package.json", "meshloom/config.yaml"):
        assert NEXT in _git(repo, env, "show", f"HEAD:{path}"), path
    assert f"meshloom:{NEXT}" in _git(repo, env, "show", "HEAD:meshloom/Dockerfile")

    # An annotated, unsigned tag on the pushed commit, carrying the notes.
    assert _git(repo, env, "cat-file", "-t", NEXT) == "tag"
    body = _git(repo, env, "cat-file", "-p", NEXT)
    assert "BEGIN PGP SIGNATURE" not in body and "* Hand-written notes" in body
    remote = _git(repo, env, "ls-remote", "origin", f"refs/tags/{NEXT}^{{}}")
    assert remote.split()[0] == head


def test_publish_writes_the_changelog_section_from_a_notes_file(release_repo) -> None:
    repo, _, env = release_repo
    notes = repo.parent / "notes.txt"
    notes.write_text("A new feature\n- A fix\n")
    result = _publish(repo, env, "--version", NEXT, "--notes-file", str(notes))
    assert result.returncode == 0, result.stderr
    changelog = _git(repo, env, "show", "HEAD:CHANGELOG.md")
    assert f"## [{NEXT}] - " in changelog
    assert "* A new feature\n* A fix" in changelog
    assert f"refs/tags/{NEXT}" in _remote_tags(repo, env)


def test_publish_refuses_off_main(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "switch", "-q", "-c", "feature")
    head = _git(repo, env, "rev-parse", "HEAD")
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert "Releases are cut from main; you are on 'feature'" in result.stderr
    assert _git(repo, env, "rev-parse", "HEAD") == head
    assert _remote_tags(repo, env) == ""


def test_publish_refuses_main_behind_origin(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "switch", "-q", "-c", "upstream")
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", "merged elsewhere")
    _git(repo, env, "push", "-q", "origin", "upstream:main")
    _git(repo, env, "switch", "-q", "main")
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert "main is behind origin/main" in result.stderr
    assert _remote_tags(repo, env) == ""


def test_publish_refuses_main_ahead_of_origin(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", "local only")
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert "main is not at origin/main" in result.stderr
    assert _remote_tags(repo, env) == ""


@pytest.mark.parametrize("stray", ["pyproject.toml", "untracked.txt"])
def test_publish_refuses_any_local_change_but_the_changelog(release_repo, stray: str) -> None:
    repo, _, env = release_repo
    head = _git(repo, env, "rev-parse", "HEAD")
    _write_notes(repo)
    path = repo / stray
    path.write_text((path.read_text() if path.exists() else "") + "# unreviewed\n")
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert "changes other than CHANGELOG.md" in result.stderr
    assert _git(repo, env, "rev-parse", "HEAD") == head
    assert _git(repo, env, "tag", "--list") == ""
    assert _remote_tags(repo, env) == ""


def test_publish_refuses_a_tag_that_exists_locally(release_repo) -> None:
    repo, _, env = release_repo
    head = _git(repo, env, "rev-parse", "HEAD")
    _git(repo, env, "tag", NEXT)
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert f"Tag {NEXT} already exists locally" in result.stderr
    assert _git(repo, env, "rev-parse", "HEAD") == head
    assert _remote_tags(repo, env) == ""


def test_publish_refuses_a_tag_that_exists_on_origin(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "tag", NEXT)
    _git(repo, env, "push", "-q", "origin", NEXT)
    _git(repo, env, "tag", "-d", NEXT)
    head = _git(repo, env, "rev-parse", "HEAD")
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert f"Tag {NEXT} already exists on origin" in result.stderr
    assert _git(repo, env, "rev-parse", "HEAD") == head


def test_publish_pushes_no_tag_when_main_is_rejected(release_repo) -> None:
    repo, origin, env = release_repo
    origin_main = _git(repo, env, "rev-parse", "origin/main")
    _reject_pushes_to(origin, "refs/heads/")
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert "Pushing main failed" in result.stderr
    assert _git(repo, env, "ls-remote", "origin", "refs/heads/main").split()[0] == origin_main
    assert _remote_tags(repo, env) == ""
    assert _git(repo, env, "tag", "--list") == ""


def test_publish_points_at_tag_release_when_only_the_tag_is_rejected(release_repo) -> None:
    repo, origin, env = release_repo
    _reject_pushes_to(origin, "refs/tags/")
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert f"main is pushed but the tag {NEXT} is not" in result.stderr
    assert f"scripts/build/tag_release.sh {NEXT}" in result.stderr
    head = _git(repo, env, "rev-parse", "HEAD")
    assert _git(repo, env, "ls-remote", "origin", "refs/heads/main").split()[0] == head
    # The local tag is gone, so tag_release.sh can recover from here.
    assert _git(repo, env, "tag", "--list") == ""
    (origin / "hooks" / "pre-receive").unlink()
    recovered = _tag_release(repo, env, NEXT, "--yes")
    assert recovered.returncode == 0, recovered.stderr
    assert f"refs/tags/{NEXT}" in _remote_tags(repo, env)
