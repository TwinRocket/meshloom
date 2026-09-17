"""GitHub release publish stays draft until every asset is up."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts" / "build" / "create_github_release.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")


def test_create_is_draft_without_assets() -> None:
    """Stats and Meshloom clients must not see an empty published release."""
    assert 'gh release create "$VERSION" --draft' in SCRIPT
    assert 'gh release create "$VERSION" "${ASSETS[@]}"' not in SCRIPT
    assert 'gh release create "$VERSION" "${ASSETS[@]}" --title' not in SCRIPT


def test_uploads_one_asset_at_a_time_with_logs_and_retries() -> None:
    assert "release_upload_one" in SCRIPT
    assert 'for asset in "${ASSETS[@]}"' in SCRIPT
    assert 'gh release upload "$VERSION" "$asset" --clobber' in SCRIPT
    assert "Still uploading" in SCRIPT
    assert "max_attempts=5" in SCRIPT
    assert "Uploaded ${name}" in SCRIPT


def test_publishes_only_after_uploads() -> None:
    upload_at = SCRIPT.index('release_upload_one "$asset"')
    publish_at = SCRIPT.index("--draft=false")
    assert upload_at < publish_at
    assert "isDraft" in SCRIPT


def test_publish_job_waits_longer_than_one_slow_package() -> None:
    start = WORKFLOW.index("  publish:")
    assert "timeout-minutes: 30" in WORKFLOW[start : start + 400]
