"""`meshloom_plugin/` ships wherever `app/` ships (MSHL-176).

The plugin child runs `meshloom_plugin.runner` from the app root
(`app/plugins/launch.py`), so every artefact that copies `app/` must copy the
package next to it. These tests read the real build files.
"""

import re
import subprocess
import textwrap
from pathlib import Path

import pytest

from app.plugins import launch

ROOT = Path(__file__).resolve().parents[1]


def _final_stage(dockerfile: str) -> list[str]:
    lines = [line.strip() for line in dockerfile.splitlines()]
    last_from = max(i for i, line in enumerate(lines) if line.upper().startswith("FROM "))
    return lines[last_from:]


def test_dockerfile_copies_the_package_next_to_app() -> None:
    stage = _final_stage((ROOT / "Dockerfile").read_text())
    assert "COPY app/ ./app/" in stage
    assert "COPY meshloom_plugin/ ./meshloom_plugin/" in stage


def test_dockerignore_keeps_the_package() -> None:
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    assert not any(
        p.strip().strip("/") in ("meshloom_plugin", "meshloom_plugin/**") for p in patterns
    )


def _shell_function(script: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", script, re.S | re.M)
    assert match, f"{name}() not found"
    return match.group(1)


def test_nfpm_build_copies_the_package_next_to_app() -> None:
    script = (ROOT / "scripts/build/build_nfpm_packages.sh").read_text()
    body = _shell_function(script, "copy_project_files")
    assert 'cp -a "$REPO_ROOT/app" "$OPT/app"' in body
    assert 'cp -a "$REPO_ROOT/meshloom_plugin" "$OPT/meshloom_plugin"' in body
    template = (ROOT / "pkg/nfpm/nfpm.yaml.tmpl").read_text()
    # The package ships the whole staged /opt/meshloom tree, so the copy above is enough.
    assert "src: __STAGING__/opt/meshloom\n    dst: /opt/meshloom" in template


def test_armhf_build_image_copies_the_package_next_to_app() -> None:
    stage = (ROOT / "Dockerfile.nfpm-armhf").read_text().split("FROM deps AS tree", 1)[1]
    assert "COPY app/ app/" in stage
    assert "COPY meshloom_plugin/ meshloom_plugin/" in stage
    assert "meshloom_plugin" not in (ROOT / "Dockerfile.nfpm-armhf.dockerignore").read_text()
    workflow = (ROOT / ".github/workflows/nfpm.yml").read_text()
    assert '"meshloom_plugin/**"' in workflow


def _overlay_script() -> str:
    workflow = (ROOT / ".github/workflows/nfpm-armhf.yml").read_text()
    match = re.search(
        r"- name: Overlay tagged sources\n\s+run: \|\n((?:          .*\n|\n)+)", workflow
    )
    assert match, "overlay step not found"
    return textwrap.dedent(match.group(1))


@pytest.mark.parametrize("tag_has_package", [True, False], ids=["5.x-tag", "pre-5.0-tag"])
def test_armhf_overlay_runs_for_old_and_new_tags(tmp_path: Path, tag_has_package: bool) -> None:
    # The recipe comes from the default branch and rebuilds any tag: a tag older
    # than 5.0 has no meshloom_plugin/, and the overlay must still leave one for
    # Dockerfile.nfpm-armhf's COPY and copy_project_files.
    tag = tmp_path / "tag-src"
    for name in ("app", "frontend"):
        (tag / name).mkdir(parents=True)
        (tag / name / "marker").write_text(name)
    if tag_has_package:
        (tag / "meshloom_plugin").mkdir()
        (tag / "meshloom_plugin" / "runner.py").write_text("# tag runner\n")
    for name in ("pyproject.toml", "uv.lock", "LICENSE.md"):
        (tag / name).write_text(name)
    (tmp_path / "meshloom_plugin").mkdir()
    (tmp_path / "meshloom_plugin" / "runner.py").write_text("# default-branch runner\n")
    subprocess.run(["bash", "-e", "-c", _overlay_script()], cwd=tmp_path, check=True)
    assert not tag.exists()
    assert (tmp_path / "meshloom_plugin").is_dir()
    runner = tmp_path / "meshloom_plugin" / "runner.py"
    if tag_has_package:
        assert runner.read_text() == "# tag runner\n"
    else:
        # Never the default branch's runner inside an old package: an empty dir.
        assert list((tmp_path / "meshloom_plugin").iterdir()) == []


def test_upgrade_matrix_image_carries_the_package() -> None:
    dockerfile = (ROOT / "scripts/test/upgrade_matrix/compose/Dockerfile.appN").read_text()
    assert "COPY meshloom_plugin/ /app/meshloom_plugin/" in dockerfile


def test_launch_resolves_the_package_from_the_app_root() -> None:
    assert launch.APP_ROOT == ROOT
    assert (launch.APP_ROOT / "meshloom_plugin" / "runner.py").is_file()


def test_runner_package_uses_only_the_standard_library() -> None:
    # The child never sees site-packages: an import of a third-party module
    # would only fail at plugin start-up, on a user's machine.
    import sys

    allowed = set(sys.stdlib_module_names) | {"meshloom_plugin"}
    for path in (ROOT / "meshloom_plugin").rglob("*.py"):
        if path.name == "dev.py":  # author tooling in the server's interpreter, not child code
            continue
        for match in re.finditer(
            r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", path.read_text(), re.M
        ):
            top = (match.group(1) or match.group(2)).split(".")[0]
            assert top in allowed, f"{path.name} imports {top}"


# --- OS isolation (MSHL-180) -------------------------------------------------------


def test_image_creates_the_plugin_account() -> None:
    from app.plugins.isolation.setuid import ACCOUNT

    stage = " ".join(_final_stage((ROOT / "Dockerfile").read_text())).replace("\\ ", "")
    match = re.search(rf"RUN useradd ([^\n]*?) {ACCOUNT}\b", stage)
    assert match, "the image must create the dedicated plugin account"
    options = match.group(1)
    assert "--system" in options and "/usr/sbin/nologin" in options
    assert "--create-home" not in options.replace("--no-create-home", "")


def test_packages_recommend_bubblewrap() -> None:
    template = (ROOT / "pkg/nfpm/nfpm.yaml.tmpl").read_text()
    overrides = template[template.index("\noverrides:") :]
    for target in ("deb", "rpm"):
        block = overrides[overrides.index(f"  {target}:") :]
        recommends = block[block.index("recommends:") :].splitlines()[1]
        assert recommends.strip() == "- bubblewrap", target


@pytest.mark.parametrize("script", ["build_rpi_image.sh", "build_rpi_kiosk_image.sh"])
def test_rpi_images_install_bubblewrap(script: str) -> None:
    # dpkg -i does not pull Recommends: the image installs it next to the .deb.
    text = (ROOT / "scripts/build" / script).read_text()
    installs = [line for line in text.splitlines() if line.startswith("apt-get install")]
    assert any("bubblewrap" in line.split() for line in installs), script
