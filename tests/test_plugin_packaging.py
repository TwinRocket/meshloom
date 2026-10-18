"""`meshloom_plugin/` ships wherever `app/` ships (MSHL-176).

The plugin child runs `meshloom_plugin.runner` from the app root
(`app/plugins/launch.py`), so every artefact that copies `app/` must copy the
package next to it. These tests read the real build files.
"""

import re
from pathlib import Path

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


def test_armhf_overlay_takes_the_package_from_the_tag() -> None:
    workflow = (ROOT / ".github/workflows/nfpm-armhf.yml").read_text()
    assert "rm -rf app frontend meshloom_plugin" in workflow
    assert "cp -a tag-src/app tag-src/frontend tag-src/meshloom_plugin ." in workflow


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
        for match in re.finditer(
            r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", path.read_text(), re.M
        ):
            top = (match.group(1) or match.group(2)).split(".")[0]
            assert top in allowed, f"{path.name} imports {top}"
