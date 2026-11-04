"""Release flow: an admin runs scripts/build/publish.sh X.Y.Z, which pushes the
release commit to main, then the X.Y.Z tag (X.Y.Z-rc.N: see the end of this file). CI never pushes main or creates a
tag; tag_release.sh only recovers a run that pushed main but not the tag.

publish.sh, tag_release.sh and create_github_release.sh run for real against a
throwaway repository and a bare `origin`; `gh` and `uv` are stubs on PATH.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
    gate = merge.split("steps:")[0]
    assert "github.event_name != 'pull_request'" in gate
    # `quality` is skipped on branch pushes too; publication must not skip with it.
    assert "needs.build.result == 'success'" in gate
    assert "!cancelled()" in gate
    # Only version tags and manual runs (the forward-merge dispatch) run `quality`.
    quality = docker[docker.index("\n  quality:\n") : docker.index("\n  matrix:\n")]
    assert (
        "github.event_name == 'workflow_dispatch' || startsWith(github.ref, 'refs/tags/')"
        in quality
    )
    deploy = docker[docker.index("\n  deploy:\n") :]
    assert "needs.merge.result == 'success'" in deploy.split("steps:")[0]


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


# Release candidates: a tag X.Y.Z-rc.N publishes a signed pre-release and reaches
# no stable user. The workflows cannot run here, so their shell runs for real where
# it is self-contained, and their wiring is read from the parsed YAML. What GitHub
# itself does with these files (filters, environments, Pages) is not exercised.

RC = "9.8.8-rc.1"
RC_NOTES = f"## [{RC}] - 2026-10-12\n\n* Hand-written notes\n\n"
RC_RELEASE_FILES = {"CHANGELOG.md", "frontend/package.json", "pyproject.toml", "uv.lock"}
KEY_SECRET = "secrets.MESHLOOM_REPO_GPG_PRIVATE_KEY"


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8"))


def _job(workflow: str, job: str) -> dict:
    return _workflow(workflow)["jobs"][job]


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def _job_text(job: dict) -> str:
    return yaml.safe_dump(job, width=10_000)


def _filter_matches(pattern: str, value: str) -> bool:
    """GitHub's tag filter: a whole match, `.` and `-` literal, `+` one or more."""
    return re.fullmatch(pattern.replace(".", r"\."), value) is not None


def _on(workflow: str) -> dict:
    # YAML 1.1 reads the bare key `on` as True.
    return _workflow(workflow)[True]


def _run_step(script: str, tmp_path: Path, **env: str) -> tuple[int, dict[str, str], str]:
    output = tmp_path / "github_output"
    output.write_text("")
    # -e: what GitHub runs a `run:` step with when it names no shell.
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={**os.environ, "GITHUB_OUTPUT": str(output), **env},
        capture_output=True,
        text=True,
        check=False,
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
    return result.returncode, values, result.stdout + result.stderr


@pytest.mark.parametrize("workflow", ["release", "docker"])
def test_the_tag_filters_take_stable_and_rc_tags_only(workflow: str) -> None:
    patterns = _on(workflow)["push"]["tags"]

    def matches(tag: str) -> bool:
        return any(_filter_matches(p, tag) for p in patterns)

    assert matches("5.0.0")
    assert matches("5.0.0-rc.1")
    assert matches("5.0.0-rc.12")
    for tag in ("5.0.0-beta.1", "v5.0.0", "5.0", "5.0.0-rc", "5.0.0-rc.1-hotfix"):
        assert not matches(tag), tag


@pytest.mark.parametrize(
    ("ref_type", "name", "prerelease"),
    [
        ("tag", "5.0.0", "false"),
        ("tag", "5.0.0-rc.1", "true"),
        ("tag", "5.0.0-rc.12", "true"),
        ("tag", "5.0.0-rc.0", None),
        ("tag", "5.0.0-rc.01", None),
        ("tag", "5.0.0-beta.1", None),
        ("tag", "v5.0.0", None),
        ("branch", "5.0.0", None),
    ],
)
def test_release_version_job_classifies_the_tag(
    tmp_path: Path, ref_type: str, name: str, prerelease: str | None
) -> None:
    script = _step(_job("release", "version"), "Resolve version")["run"]
    code, values, output = _run_step(script, tmp_path, REF_TYPE=ref_type, RAW_VERSION=name)
    if prerelease is None:
        assert code != 0, output
        assert "prerelease" not in values
    else:
        assert code == 0, output
        assert values == {"version": name, "prerelease": prerelease}


@pytest.mark.parametrize(
    ("ref", "stable"),
    [
        ("refs/tags/5.0.0", "true"),
        ("refs/tags/5.0.0-rc.1", "false"),
        ("refs/tags/foo", "false"),
        ("refs/heads/main", "false"),
        ("refs/heads/5.0.0", "false"),
        ("refs/pull/1/merge", "false"),
    ],
)
def test_docker_only_a_stable_tag_counts_as_stable(tmp_path: Path, ref: str, stable: str) -> None:
    """Pushed or dispatched alike: the decision reads the ref, not the event."""
    script = _step(_job("docker", "matrix"), "Classify the ref")["run"]
    code, values, output = _run_step(script, tmp_path, GITHUB_REF=ref)
    assert code == 0, output
    assert values["stable_tag"] == stable


def test_docker_publishes_latest_and_major_minor_on_stable_tags_only() -> None:
    meta = _step(_job("docker", "merge"), "Compute tags and labels")["with"]
    gate = "enable=${{ needs.matrix.outputs.stable_tag == 'true' }}"
    tags = meta["tags"].splitlines()
    assert f"type=raw,value=latest,{gate}" in tags
    assert f"type=semver,pattern={{{{major}}}}.{{{{minor}}}},{gate}" in tags
    assert "type=semver,pattern={{version}}" in tags
    # metadata-action's automatic `latest` would bypass the gate above.
    assert meta["flavor"] == "latest=false"
    assert sum("latest" in tag for tag in tags) == 1


def test_docker_attests_rc_tags_too() -> None:
    attest = _step(_job("docker", "merge"), "Attest image provenance")
    assert attest["if"] == "startsWith(github.ref, 'refs/tags/')"
    assert _job("docker", "matrix")["outputs"]["stable_tag"]
    assert "matrix" in _job("docker", "merge")["needs"]


def test_an_rc_release_skips_armhf_and_the_site_but_signs_and_publishes() -> None:
    gate = "needs.version.outputs.prerelease == 'false'"
    for job in ("dispatch-armhf", "docs-site"):
        assert _job("release", job)["if"] == gate, job
        assert "version" in _job("release", job)["needs"], job
    for job in ("nfpm-stage", "nfpm", "publish", "linux-repo", "sign-manifest", "rpi-image"):
        assert "if" not in _job("release", job), job


def test_a_hand_published_rc_does_not_rebuild_the_site() -> None:
    gate = _job("docs-site", "deploy")["if"]
    assert gate == "github.event_name != 'release' || !github.event.release.prerelease"


@pytest.mark.parametrize(
    ("workflow", "job", "step"),
    [
        ("nfpm-armhf", "version", "Resolve version"),
        ("rpi-image", "image", "Resolve version"),
    ],
)
def test_armhf_and_the_manual_pi_image_stay_strict(
    tmp_path: Path, workflow: str, job: str, step: str
) -> None:
    """Their recipe comes from the default branch: an rc never goes through them."""
    script = _step(_job(workflow, job), step)["run"]
    for version in ("5.0.0-rc.1", "5.0.0-beta.1"):
        code, values, output = _run_step(script, tmp_path, VERSION=version, INPUT_VERSION=version)
        assert code != 0, output
        assert "version" not in values
    code, values, output = _run_step(script, tmp_path, VERSION="5.0.0", INPUT_VERSION="5.0.0")
    assert code == 0, output
    assert values["version"] == "5.0.0"


def test_sign_manifest_accepts_rc_versions(tmp_path: Path) -> None:
    script = _step(_job("sign-manifest", "sign-manifest"), "Validate version")["run"]
    for version, accepted in (
        ("5.0.0", True),
        ("5.0.0-rc.1", True),
        ("5.0.0-beta.1", False),
        ("5.0.0-rc.0", False),
    ):
        code, _, output = _run_step(script, tmp_path, VERSION=version)
        assert (code == 0) is accepted, output


# Signing key exposure: nothing that runs third-party code shares a job with the key.

THIRD_PARTY_CODE = ("npm ci", "npm run build", "uv sync", "setup-uv", "setup-node", "--stage-only")


def _uses_key(job: dict) -> bool:
    return KEY_SECRET in _job_text(job)


def test_no_workflow_inherits_every_secret() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        assert "secrets: inherit" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.parametrize(
    ("workflow", "stage_jobs", "key_job"),
    [
        ("release", ["frontend", "nfpm-stage"], "nfpm"),
        ("nfpm-armhf", ["frontend", "assemble"], "package"),
    ],
)
def test_packages_are_built_without_the_key_and_signed_without_third_party_code(
    workflow: str, stage_jobs: list[str], key_job: str
) -> None:
    for name in stage_jobs:
        job = _job(workflow, name)
        assert not _uses_key(job), name
        assert job["permissions"].get("contents") == "read", name
        assert "environment" not in job, name
    signer = _job(workflow, key_job)
    assert _uses_key(signer)
    text = _job_text(signer)
    for needle in (*THIRD_PARTY_CODE, "build-push-action"):
        assert needle not in text, needle
    assert "--package-only" in text
    assert "MESHLOOM_REQUIRE_SIGNED" in text
    assert "scripts/build/install_nfpm.sh" in text


def test_every_job_holding_the_key_is_in_the_signing_environment() -> None:
    holders = []
    for path in WORKFLOWS.glob("*.yml"):
        for name, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items():
            if "uses" in job:
                continue
            if _uses_key(job):
                holders.append((path.stem, name))
                assert job["environment"] == "release-signing", (path.stem, name)
    assert sorted(holders) == [
        ("nfpm-armhf", "package"),
        ("publish-linux-repo", "build"),
        ("release", "nfpm"),
        ("release", "preflight"),
        ("sign-manifest", "sign-manifest"),
    ]


def test_the_pages_token_and_the_key_are_in_different_jobs() -> None:
    build = _job("publish-linux-repo", "build")
    deploy = _job("publish-linux-repo", "deploy")
    assert build["permissions"] == {"contents": "read"}
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert deploy["environment"]["name"] == "github-pages"
    assert not _uses_key(deploy)
    assert deploy["needs"] == "build"
    declared = _on("publish-linux-repo")["workflow_call"]["secrets"]
    assert declared == {"MESHLOOM_REPO_GPG_PRIVATE_KEY": {"required": False}}


def test_release_pins_uv() -> None:
    setup = next(
        step
        for step in _job("release", "nfpm-stage")["steps"]
        if "setup-uv" in step.get("uses", "")
    )
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", setup["with"]["version"])


def _stub_curl(tmp_path: Path) -> dict[str, str]:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(exist_ok=True)
    curl = stub_dir / "curl"
    # Writes junk where -o points, or to stdout: a tampered download either way.
    curl.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
        'if [ -n "$out" ]; then echo tampered > "$out"; else echo tampered; fi\n'
    )
    curl.chmod(0o755)
    uv = stub_dir / "uv"
    uv.write_text("#!/bin/sh\nexit 0\n")
    uv.chmod(0o755)
    return {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}


needs_sha256sum = pytest.mark.skipif(
    shutil.which("sha256sum") is None, reason="sha256sum not installed"
)


@needs_sha256sum
def test_install_nfpm_refuses_a_download_that_does_not_match(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(BUILD / "install_nfpm.sh")],
        env={**_stub_curl(tmp_path), "NFPM_INSTALL_DIR": str(tmp_path / "bin")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert not (tmp_path / "bin" / "nfpm").exists()


@needs_sha256sum
@pytest.mark.parametrize("arch", ["amd64", "arm64", "armhf"])
def test_standalone_python_is_refused_when_its_checksum_differs(tmp_path: Path, arch: str) -> None:
    stage = tmp_path / "stage"
    result = subprocess.run(
        [
            "bash",
            str(BUILD / "build_nfpm_packages.sh"),
            "--version",
            "0.0.0",
            "--arch",
            arch,
            "--stage-dir",
            str(stage),
            "--deps-only",
        ],
        env=_stub_curl(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stdout + result.stderr
    assert not (stage / "opt" / "meshloom" / "python").exists()


def test_standalone_python_override_needs_its_own_checksum(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(BUILD / "build_nfpm_packages.sh"),
            "--version",
            "0.0.0",
            "--arch",
            "amd64",
            "--stage-dir",
            str(tmp_path / "stage"),
            "--deps-only",
        ],
        env={**_stub_curl(tmp_path), "MESHLOOM_STANDALONE_PYVER": "3.13.99"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "MESHLOOM_STANDALONE_PYSHA256" in result.stderr


# What each suite of the apt/dnf repository serves.

RESOLVER = BUILD / "resolve_linux_repo_channels.py"
PACKAGES = ["_amd64.deb", "_arm64.deb", ".x86_64.rpm", ".aarch64.rpm"]


def _release(
    tag: str, *, prerelease: bool = False, draft: bool = False, assets: list[str] | None = None
) -> dict:
    names = [f"meshloom{suffix}" for suffix in PACKAGES] if assets is None else assets
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "assets": names}


def _resolve(
    tmp_path: Path,
    releases: list[dict],
    version: str,
    *,
    latest: str = "4.18.2",
    armhf: bool = False,
) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "releases.jsonl"
    path.write_text("".join(json.dumps(release) + "\n" for release in releases))
    args = ["--releases", str(path), "--latest", latest, "--version", version]
    if armhf:
        args.append("--require-armhf")
    return subprocess.run(
        [sys.executable, "-I", str(RESOLVER), *args], capture_output=True, text=True, check=False
    )


def _channels(result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    return values["stable"], values["testing"]


STABLE = _release("4.18.2")


def test_an_rc_goes_to_testing_and_stable_stays_on_the_latest_release(tmp_path: Path) -> None:
    rc = _release("5.0.0-rc.1", prerelease=True)
    assert _channels(_resolve(tmp_path, [rc, STABLE], "5.0.0-rc.1")) == ("4.18.2", "5.0.0-rc.1")


def test_testing_serves_stable_when_no_rc_is_newer(tmp_path: Path) -> None:
    assert _channels(_resolve(tmp_path, [STABLE], "4.18.2")) == ("4.18.2", "4.18.2")
    rc = _release("5.0.0-rc.2", prerelease=True)
    final = _release("5.0.0")
    assert _channels(_resolve(tmp_path, [final, rc, STABLE], "5.0.0", latest="5.0.0")) == (
        "5.0.0",
        "5.0.0",
    )


def test_a_stable_hotfix_keeps_the_running_rc_in_testing(tmp_path: Path) -> None:
    hotfix = _release("4.18.3")
    rc = _release("5.0.0-rc.1", prerelease=True)
    assert _channels(_resolve(tmp_path, [hotfix, rc, STABLE], "4.18.3", latest="4.18.3")) == (
        "4.18.3",
        "5.0.0-rc.1",
    )


def test_testing_picks_the_highest_rc_numerically(tmp_path: Path) -> None:
    releases = [
        _release("5.0.0-rc.9", prerelease=True),
        _release("5.0.0-rc.10", prerelease=True),
        _release("5.0.0-rc.2", prerelease=True),
        STABLE,
    ]
    assert _channels(_resolve(tmp_path, releases, "5.0.0-rc.9"))[1] == "5.0.0-rc.10"


def test_testing_ignores_drafts_incomplete_and_unflagged_rcs(tmp_path: Path) -> None:
    releases = [
        _release("5.0.0-rc.4", prerelease=True, draft=True),
        _release("5.0.0-rc.3", prerelease=True, assets=["meshloom_amd64.deb"]),
        _release("5.0.0-rc.2"),
        _release("5.0.0-beta.9", prerelease=True),
        _release("5.0.0-rc.1", prerelease=True),
        STABLE,
    ]
    assert _channels(_resolve(tmp_path, releases, "4.18.2")) == ("4.18.2", "5.0.0-rc.1")


def test_a_broken_rc_never_blocks_a_stable_publication(tmp_path: Path) -> None:
    broken = _release("5.0.0-rc.1", prerelease=True, assets=[])
    assert _channels(_resolve(tmp_path, [broken, STABLE], "4.18.2")) == ("4.18.2", "4.18.2")


def test_no_version_means_the_latest_release(tmp_path: Path) -> None:
    assert _channels(_resolve(tmp_path, [STABLE], "")) == ("4.18.2", "4.18.2")


@pytest.mark.parametrize(
    ("releases", "version", "latest", "message"),
    [
        ([STABLE], "5.0.0-rc.1", "4.18.2", "no published release"),
        ([_release("5.0.0-rc.1"), STABLE], "5.0.0-rc.1", "4.18.2", "not flagged"),
        (
            [_release("5.0.0-rc.1", prerelease=True, assets=[]), STABLE],
            "5.0.0-rc.1",
            "4.18.2",
            "lacks",
        ),
        # A tag X.Y.Z flagged pre-release used to reach every stable user.
        ([_release("5.0.0", prerelease=True), STABLE], "5.0.0", "4.18.2", "flagged pre-release"),
        ([STABLE], "5.0.0-beta.1", "4.18.2", "not X.Y.Z"),
        ([STABLE], "nope", "4.18.2", "not X.Y.Z"),
        # GitHub's `latest` pointing at an rc must not put it in stable.
        (
            [_release("5.0.0-rc.1"), STABLE],
            "",
            "5.0.0-rc.1",
            "not X.Y.Z",
        ),
        ([STABLE], "", "", "not X.Y.Z"),
    ],
)
def test_the_resolver_fails_closed(
    tmp_path: Path, releases: list[dict], version: str, latest: str, message: str
) -> None:
    result = _resolve(tmp_path, releases, version, latest=latest)
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert "stable=" not in result.stdout


def test_an_rc_cannot_require_armhf(tmp_path: Path) -> None:
    rc = _release("5.0.0-rc.1", prerelease=True)
    result = _resolve(tmp_path, [rc, STABLE], "5.0.0-rc.1", armhf=True)
    assert result.returncode != 0
    assert "armhf" in result.stderr


def test_the_repo_workflow_downloads_no_armhf_for_an_rc() -> None:
    script = _step(_job("publish-linux-repo", "build"), "Download release packages")["run"]
    testing = script[script.index('"$TESTING"') :]
    for pattern in ("*_amd64.deb", "*_arm64.deb", "*.x86_64.rpm", "*.aarch64.rpm"):
        assert pattern in testing
    assert '"*.deb"' not in testing and "armhf" not in testing.split("--dir")[1].split("\n\n")[0]


def test_the_repo_workflow_signs_and_verifies_both_suites() -> None:
    build = _job("publish-linux-repo", "build")
    sign = _step(build, "Sign metadata and publish the public key")["run"]
    verify = _step(build, "Verify published signatures")["run"]
    for script in (sign, verify):
        assert "for suite in stable testing" in script
        assert "rpm/testing/${arch}" in script or '"public/rpm/testing/' in script


needs_apt_tools = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("dpkg-deb", "dpkg-scanpackages", "apt-ftparchive")),
    reason="dpkg-dev or apt-utils not installed",
)


def _fake_deb(directory: Path, version: str, arch: str) -> None:
    root = directory.parent / f"build-{directory.name}-{arch}"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "DEBIAN" / "control").write_text(
        f"Package: meshloom\nVersion: {version}\nArchitecture: {arch}\n"
        "Maintainer: t <t@example.invalid>\nDescription: t\n"
    )
    directory.mkdir(exist_ok=True)
    # The name GitHub leaves on an asset: `~` becomes `.`.
    name = f"meshloom_{version.replace('~', '.')}_{arch}.deb"
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-b", str(root), str(directory / name)],
        check=True,
        capture_output=True,
    )


def _fake_rpm_files(directory: Path, version: str) -> None:
    directory.mkdir(exist_ok=True)
    for arch in ("x86_64", "aarch64"):
        (directory / f"meshloom-{version}-1.{arch}.rpm").write_text("rpm")


def _repo_env(tmp_path: Path) -> dict[str, str]:
    """createrepo_c reads real rpm files: a stub leaves what the script checks."""
    stub_dir = tmp_path / "repo-stubs"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "createrepo_c"
    stub.write_text('#!/bin/sh\nmkdir -p "$1/repodata"\nls "$1" > "$1/repodata/repomd.xml"\n')
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}


def _build_repo(
    tmp_path: Path, stable: Path, testing: Path | None
) -> subprocess.CompletedProcess[str]:
    args = ["--stable-dir", str(stable), "--output", str(tmp_path / "public")]
    if testing is not None:
        args += ["--testing-dir", str(testing)]
    return subprocess.run(
        ["bash", str(BUILD / "build_linux_repo.sh"), *args],
        env=_repo_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


def _packages(public: Path, suite: str, arch: str) -> str:
    return (public / "apt" / "dists" / suite / "main" / f"binary-{arch}" / "Packages").read_text()


@pytest.fixture
def stable_packages(tmp_path: Path) -> Path:
    stable = tmp_path / "stable"
    for arch in ("amd64", "arm64", "armhf"):
        _fake_deb(stable, "4.18.2-1", arch)
    _fake_rpm_files(stable, "4.18.2")
    return stable


@pytest.fixture
def rc_packages(tmp_path: Path) -> Path:
    testing = tmp_path / "testing"
    for arch in ("amd64", "arm64"):
        _fake_deb(testing, "5.0.0~rc.1-1", arch)
    _fake_rpm_files(testing, "5.0.0.rc.1")
    return testing


@needs_apt_tools
def test_the_stable_suite_is_untouched_by_an_rc(
    tmp_path: Path, stable_packages: Path, rc_packages: Path
) -> None:
    result = _build_repo(tmp_path, stable_packages, rc_packages)
    assert result.returncode == 0, result.stdout + result.stderr
    public = tmp_path / "public"

    for arch in ("amd64", "arm64", "armhf"):
        stable = _packages(public, "stable", arch)
        assert "Version: 4.18.2-1\n" in stable
        assert "rc" not in stable.replace("Architecture", "")
        assert f"Filename: pool/main/meshloom_4.18.2-1_{arch}.deb\n" in stable
    assert "Suite: stable\n" in (public / "apt/dists/stable/Release").read_text()
    assert sorted(p.name for p in (public / "rpm/x86_64").glob("*.rpm")) == [
        "meshloom-4.18.2-1.x86_64.rpm"
    ]
    assert sorted(p.name for p in (public / "rpm/aarch64").glob("*.rpm")) == [
        "meshloom-4.18.2-1.aarch64.rpm"
    ]
    assert not list((public / "apt/pool/main").glob("*rc*"))


@needs_apt_tools
def test_the_testing_suite_serves_the_rc_without_armhf(
    tmp_path: Path, stable_packages: Path, rc_packages: Path
) -> None:
    assert _build_repo(tmp_path, stable_packages, rc_packages).returncode == 0
    public = tmp_path / "public"
    for arch in ("amd64", "arm64"):
        testing = _packages(public, "testing", arch)
        assert "Version: 5.0.0~rc.1-1\n" in testing
        assert "4.18.2" not in testing
        assert f"Filename: pool/testing/meshloom_5.0.0.rc.1-1_{arch}.deb\n" in testing
    release = (public / "apt/dists/testing/Release").read_text()
    assert "Suite: testing\n" in release
    assert "Codename: testing\n" in release
    assert "armhf" not in release
    assert not (public / "apt/dists/testing/main/binary-armhf").exists()
    assert [p.name for p in (public / "rpm/testing/x86_64").glob("*.rpm")] == [
        "meshloom-5.0.0.rc.1-1.x86_64.rpm"
    ]


@needs_apt_tools
def test_without_an_rc_testing_serves_the_stable_packages_from_the_same_pool(
    tmp_path: Path, stable_packages: Path
) -> None:
    result = _build_repo(tmp_path, stable_packages, None)
    assert result.returncode == 0, result.stdout + result.stderr
    public = tmp_path / "public"
    for arch in ("amd64", "arm64", "armhf"):
        assert f"Filename: pool/main/meshloom_4.18.2-1_{arch}.deb\n" in _packages(
            public, "testing", arch
        )
    assert not (public / "apt/pool/testing").exists()
    assert list((public / "rpm/testing/aarch64").glob("*.rpm"))


@needs_apt_tools
def test_an_rc_package_handed_to_the_stable_suite_is_refused(
    tmp_path: Path, stable_packages: Path, rc_packages: Path
) -> None:
    result = _build_repo(tmp_path, rc_packages, None)
    assert result.returncode != 0
    assert "pre-release" in result.stdout + result.stderr


@needs_apt_tools
def test_an_rc_rpm_handed_to_the_stable_suite_is_refused(
    tmp_path: Path, stable_packages: Path
) -> None:
    _fake_rpm_files(stable_packages, "5.0.0.rc.1")
    result = _build_repo(tmp_path, stable_packages, None)
    assert result.returncode != 0
    assert "pre-release" in result.stdout + result.stderr


def test_the_repo_script_refuses_an_empty_stable_directory(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    result = _build_repo(tmp_path, tmp_path / "empty", None)
    assert result.returncode != 0
    assert not (tmp_path / "public" / "apt" / "dists").exists()


# The scripts: an rc from preversion-5, with the add-on and main left alone.


def _check_versions(repo: Path, version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(BUILD / "check_version_consistency.sh"), version],
        env={**os.environ, "MESHLOOM_VERSION_ROOT": str(repo)},
        capture_output=True,
        text=True,
        check=False,
    )


def _set_app_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n')
    (repo / "frontend" / "package.json").write_text(f'{{"name": "x", "version": "{version}"}}\n')


@pytest.fixture
def rc_tree(tmp_path: Path) -> Path:
    """The add-on at the last stable release, the application at the rc."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _write_versions(tree, "4.18.2")
    _set_app_version(tree, RC)
    return tree


def test_version_check_on_an_rc_leaves_the_addon_at_its_stable_version(rc_tree: Path) -> None:
    result = _check_versions(rc_tree, RC)
    assert result.returncode == 0, result.stdout


def test_version_check_on_an_rc_still_compares_the_application(rc_tree: Path) -> None:
    (rc_tree / "frontend" / "package.json").write_text('{"version": "9.8.8-rc.2"}\n')
    result = _check_versions(rc_tree, RC)
    assert result.returncode != 0
    assert "frontend/package.json" in result.stdout


@pytest.mark.parametrize("path", ["config.yaml", "Dockerfile"])
def test_version_check_refuses_an_rc_in_the_addon(rc_tree: Path, path: str) -> None:
    target = rc_tree / "meshloom" / path
    target.write_text(target.read_text().replace("4.18.2", RC))
    result = _check_versions(rc_tree, RC)
    assert result.returncode != 0
    assert "add-on" in result.stdout


def test_version_check_on_a_stable_tag_still_compares_the_addon(rc_tree: Path) -> None:
    _set_app_version(rc_tree, "5.0.0")
    result = _check_versions(rc_tree, "5.0.0")
    assert result.returncode != 0
    assert "meshloom/config.yaml" in result.stdout


@pytest.mark.parametrize("version", ["5.0.0-rc.0", "5.0.0-rc", "5.0.0-beta.1", "5.0.0rc1"])
def test_version_check_refuses_other_pre_releases(rc_tree: Path, version: str) -> None:
    assert _check_versions(rc_tree, version).returncode == 2


@pytest.fixture
def rc_repo(release_repo) -> tuple[Path, Path, dict[str, str]]:
    """The same repository, on preversion-5 at origin/preversion-5."""
    repo, origin, env = release_repo
    _git(repo, env, "switch", "-q", "-c", "preversion-5")
    _git(repo, env, "push", "-q", "origin", "preversion-5")
    _git(repo, env, "fetch", "-q", "origin")
    return repo, origin, env


def _write_rc_notes(repo: Path) -> None:
    old = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n" + RC_NOTES + old.removeprefix("# Changelog\n\n")
    )


def test_publish_cuts_an_rc_from_preversion5_and_leaves_the_addon_alone(rc_repo) -> None:
    repo, _, env = rc_repo
    main_before = _git(repo, env, "rev-parse", "origin/main")
    addon_before = _git(repo, env, "show", "HEAD:meshloom/config.yaml")
    dockerfile_before = _git(repo, env, "show", "HEAD:meshloom/Dockerfile")
    _write_rc_notes(repo)

    result = _publish(repo, env, RC)
    assert result.returncode == 0, result.stdout + result.stderr

    head = _git(repo, env, "rev-parse", "HEAD")
    assert _git(repo, env, "ls-remote", "origin", "refs/heads/preversion-5").split()[0] == head
    assert _git(repo, env, "ls-remote", "origin", "refs/heads/main").split()[0] == main_before
    changed = _git(repo, env, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    assert set(changed.splitlines()) == RC_RELEASE_FILES
    assert _git(repo, env, "show", "HEAD:meshloom/config.yaml") == addon_before
    assert _git(repo, env, "show", "HEAD:meshloom/Dockerfile") == dockerfile_before
    assert RC in _git(repo, env, "show", "HEAD:pyproject.toml")
    assert RC in _git(repo, env, "show", "HEAD:frontend/package.json")
    assert _git(repo, env, "ls-remote", "origin", f"refs/tags/{RC}^{{}}").split()[0] == head
    assert ":latest" not in result.stdout


def test_publish_still_cuts_a_stable_release_from_main_with_the_addon(release_repo) -> None:
    repo, _, env = release_repo
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode == 0, result.stderr
    assert f'version: "{NEXT}"' in _git(repo, env, "show", "HEAD:meshloom/config.yaml")
    assert f"meshloom:{NEXT}" in _git(repo, env, "show", "HEAD:meshloom/Dockerfile")
    assert ":latest" in result.stdout


def test_publish_refuses_a_stable_release_from_preversion5(rc_repo) -> None:
    repo, _, env = rc_repo
    head = _git(repo, env, "rev-parse", "HEAD")
    _write_notes(repo)
    result = _publish(repo, env, NEXT)
    assert result.returncode != 0
    assert "Releases are cut from main" in result.stderr
    assert _git(repo, env, "rev-parse", "HEAD") == head
    assert _remote_tags(repo, env) == ""


def test_publish_refuses_an_rc_from_another_branch(release_repo) -> None:
    repo, _, env = release_repo
    _git(repo, env, "switch", "-q", "-c", "feature")
    _write_rc_notes(repo)
    result = _publish(repo, env, RC)
    assert result.returncode != 0
    assert "you are on 'feature'" in result.stderr
    assert _remote_tags(repo, env) == ""


def test_publish_refuses_preversion5_behind_origin(rc_repo) -> None:
    repo, _, env = rc_repo
    _git(repo, env, "switch", "-q", "-c", "upstream")
    _git(repo, env, "commit", "-q", "--allow-empty", "-m", "merged elsewhere")
    _git(repo, env, "push", "-q", "origin", "upstream:preversion-5")
    _git(repo, env, "switch", "-q", "preversion-5")
    _write_rc_notes(repo)
    result = _publish(repo, env, RC)
    assert result.returncode != 0
    assert "preversion-5 is behind origin/preversion-5" in result.stderr
    assert _remote_tags(repo, env) == ""


@pytest.mark.parametrize("version", ["9.8.8-beta.1", "9.8.8-rc.0", "9.8.8-rc"])
def test_publish_refuses_other_pre_releases(rc_repo, version: str) -> None:
    repo, _, env = rc_repo
    result = _publish(repo, env, version)
    assert result.returncode != 0
    assert "X.Y.Z or X.Y.Z-rc.N" in result.stderr
    assert _remote_tags(repo, env) == ""


def _commit_rc(repo: Path, env: dict[str, str]) -> None:
    _set_app_version(repo, RC)
    _write_rc_notes(repo)
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", f"Updating changelog + build for {RC}")
    _git(repo, env, "push", "-q", "origin", "preversion-5")
    _git(repo, env, "fetch", "-q", "origin")


def test_tag_release_recovers_an_rc_on_preversion5(rc_repo) -> None:
    repo, _, env = rc_repo
    _commit_rc(repo, env)
    result = _tag_release(repo, env, RC, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"refs/tags/{RC}" in _remote_tags(repo, env)


def test_tag_release_refuses_a_stable_tag_on_preversion5(rc_repo) -> None:
    repo, _, env = rc_repo
    result = _tag_release(repo, env, VERSION, "--dry-run")
    assert result.returncode != 0
    assert "cut from main" in result.stderr


def test_tag_release_refuses_an_rc_from_another_branch(rc_repo) -> None:
    repo, _, env = rc_repo
    _commit_rc(repo, env)
    _git(repo, env, "switch", "-q", "-c", "feature")
    result = _tag_release(repo, env, RC, "--dry-run")
    assert result.returncode != 0
    assert "you are on 'feature'" in result.stderr


def _gh_release_stub(tmp_path: Path, env: dict[str, str]) -> tuple[dict[str, str], Path]:
    """A `gh` that keeps the release's state in a directory and logs every call."""
    stub_dir = tmp_path / "gh-stub"
    stub_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = stub_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{log}"\n'
        f'state="{stub_dir}/created"\n'
        'case "$1 $2" in\n'
        '  "release create") touch "$state" ;;\n'
        '  "release view") [ -f "$state" ] || exit 1; echo true ;;\n'
        "esac\n"
        "exit 0\n"
    )
    gh.chmod(0o755)
    return {**env, "PATH": f"{stub_dir}{os.pathsep}{env['PATH']}"}, log


def _gh_calls(log: Path, action: str) -> str:
    return "\n".join(ln for ln in log.read_text().splitlines() if ln.startswith(action))


def _create_release_of(
    repo: Path, env: dict[str, str], version: str
) -> subprocess.CompletedProcess[str]:
    asset = repo.parent / "asset.zip"
    asset.write_text("x")
    return subprocess.run(
        [
            "bash",
            str(repo / "scripts" / "build" / "create_github_release.sh"),
            "--version",
            version,
            "--asset",
            str(asset),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_rc_release_is_a_prerelease_and_never_latest_at_every_step(
    rc_repo, tmp_path: Path
) -> None:
    repo, _, env = rc_repo
    _commit_rc(repo, env)
    _git(repo, env, "tag", "-a", RC, "-m", "x")
    _git(repo, env, "push", "-q", "origin", RC)
    env, log = _gh_release_stub(tmp_path, env)
    result = _create_release_of(repo, env, RC)
    assert result.returncode == 0, result.stdout + result.stderr
    create = _gh_calls(log, "release create")
    edit = _gh_calls(log, "release edit")
    for call in (create, edit):
        assert "--prerelease" in call
        assert "--latest=false" in call
    assert "--draft=false" in edit


def test_refreshing_a_published_rc_keeps_it_a_prerelease(rc_repo, tmp_path: Path) -> None:
    repo, _, env = rc_repo
    _commit_rc(repo, env)
    _git(repo, env, "tag", "-a", RC, "-m", "x")
    _git(repo, env, "push", "-q", "origin", RC)
    env, log = _gh_release_stub(tmp_path, env)
    stub = Path(env["PATH"].split(os.pathsep)[0]) / "gh"
    stub.write_text(stub.read_text().replace('[ -f "$state" ] || exit 1; echo true', "echo false"))
    result = _create_release_of(repo, env, RC)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--prerelease --latest=false" in _gh_calls(log, "release edit")


def test_a_stable_release_keeps_the_default_flags(release_repo, tmp_path: Path) -> None:
    repo, _, env = release_repo
    _git(repo, env, "tag", "-a", VERSION, "-m", "x")
    _git(repo, env, "push", "-q", "origin", VERSION)
    env, log = _gh_release_stub(tmp_path, env)
    result = _create_release_of(repo, env, VERSION)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = _gh_calls(log, "release create") + _gh_calls(log, "release edit")
    assert "--draft=false" in calls
    assert "--prerelease" not in calls
    assert "--latest" not in calls


def test_stable_tags_still_run_the_whole_chain() -> None:
    assert _job("release", "linux-repo")["with"]["version"] == (
        "${{ needs.version.outputs.version }}"
    )
    dispatch = _step(_job("release", "dispatch-armhf"), "Start armhf workflow")["run"]
    assert "nfpm-armhf.yml" in dispatch
    assert "docs-site.yml" in _job("release", "docs-site")["uses"]
    assert "[0-9]+.[0-9]+.[0-9]+" in _on("release")["push"]["tags"]
    assert "[0-9]+.[0-9]+.[0-9]+" in _on("docker")["push"]["tags"]
