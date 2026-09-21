"""Docker mapping helpers in scripts/setup/install.sh."""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "setup" / "install.sh"

_HELPERS = (
    "docker_allows_usb",
    "detect_dbus_socket",
    "find_serial_devices",
    "set_serial_mapping",
    "prepare_docker_mappings",
)


def _extract_fn(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    marker = f"{name}() {{"
    start = text.index(marker)
    brace_at = start + len(marker) - 1
    depth = 0
    for index, char in enumerate(text[brace_at:], start=brace_at):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unclosed function {name}")


def _bash(call: str, extra: str = "") -> subprocess.CompletedProcess[str]:
    source = "\n\n".join(_extract_fn(name) for name in _HELPERS)
    script = f"""
set -euo pipefail
DOCKER_KIND=""
TRANSPORT=""
SERIAL_PORT=""
SERIAL_COMPOSE_HOST_PATH=""
DBUS_SOCKET=""
STEP_TOTAL=3
INSTALL_STEP=3
SERIAL_FOUND_HOST_PATHS=()
SERIAL_FOUND_LABELS=()
SERIAL_FOUND_DISPLAYS=()
ui_warn() {{ :; }}
{source}
{extra}
{call}
"""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def _ok(call: str, extra: str = "") -> str:
    result = _bash(call, extra)
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def test_detect_dbus_socket_accepts_socket_only(tmp_path: Path) -> None:
    socket_path = tmp_path / "system_bus_socket"
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(socket_path))
    try:
        assert _ok(f'detect_dbus_socket "{socket_path}"; printf %s "$DBUS_SOCKET"') == str(
            socket_path
        )
    finally:
        sock.close()


def test_detect_dbus_socket_ignores_directory_and_missing(tmp_path: Path) -> None:
    directory = tmp_path / "dbus"
    directory.mkdir()
    missing = tmp_path / "nope"
    out = _ok(f'detect_dbus_socket "{directory}" "{missing}" || true; printf %s "$DBUS_SOCKET"')
    assert out == ""


def test_set_serial_mapping_plain_path() -> None:
    out = _ok('set_serial_mapping /dev/ttyACM0; printf %s "$TRANSPORT|$SERIAL_COMPOSE_HOST_PATH"')
    assert out == "serial|/dev/ttyACM0"


def test_set_serial_mapping_resolves_colon_symlink(tmp_path: Path) -> None:
    target = tmp_path / "ttyACM0"
    target.write_text("", encoding="utf-8")
    by_id = tmp_path / "usb-Device_VID:PID_abc"
    by_id.symlink_to(target)
    resolved = str(by_id.resolve())
    out = _ok(f'set_serial_mapping "{by_id}"; printf %s "$TRANSPORT|$SERIAL_COMPOSE_HOST_PATH"')
    assert out == f"serial|{resolved}"


def test_set_serial_mapping_rejects_unresolved_colon() -> None:
    out = _ok(
        """
if set_serial_mapping "/no/such/usb-VID:PID"; then echo ok; else
  printf %s "$TRANSPORT|$SERIAL_COMPOSE_HOST_PATH|fail"
fi
"""
    )
    assert out == "ui||fail"


def test_prepare_maps_single_serial_without_prompt() -> None:
    extra = """
docker_allows_usb() { return 0; }
detect_dbus_socket() { DBUS_SOCKET=""; return 1; }
find_serial_devices() {
    SERIAL_FOUND_HOST_PATHS=(/dev/ttyACM0)
    SERIAL_FOUND_LABELS=(ttyACM0)
    SERIAL_FOUND_DISPLAYS=(/dev/ttyACM0)
}
choose_serial_among_found() { echo PROMPT; }
"""
    out = _ok(
        'prepare_docker_mappings; printf %s "$TRANSPORT|$SERIAL_COMPOSE_HOST_PATH|$STEP_TOTAL"',
        extra,
    )
    assert out == "serial|/dev/ttyACM0|2"


def test_prepare_skips_usb_when_no_serial_device() -> None:
    extra = """
docker_allows_usb() { return 0; }
detect_dbus_socket() { DBUS_SOCKET="/run/dbus/system_bus_socket"; return 0; }
find_serial_devices() {
    SERIAL_FOUND_HOST_PATHS=()
    SERIAL_FOUND_LABELS=()
    SERIAL_FOUND_DISPLAYS=()
}
choose_serial_among_found() { echo PROMPT; }
"""
    out = _ok(
        'prepare_docker_mappings; printf %s "$TRANSPORT|$SERIAL_COMPOSE_HOST_PATH|$DBUS_SOCKET|$STEP_TOTAL"',
        extra,
    )
    assert out == "ui||/run/dbus/system_bus_socket|2"


def test_prepare_asks_when_several_serial_devices() -> None:
    extra = """
docker_allows_usb() { return 0; }
detect_dbus_socket() { DBUS_SOCKET=""; return 1; }
find_serial_devices() {
    SERIAL_FOUND_HOST_PATHS=(/dev/ttyACM0 /dev/ttyUSB0)
    SERIAL_FOUND_LABELS=(ttyACM0 ttyUSB0)
    SERIAL_FOUND_DISPLAYS=(/dev/ttyACM0 /dev/ttyUSB0)
}
choose_serial_among_found() { echo PROMPT; TRANSPORT=ui; }
"""
    out = _ok('prepare_docker_mappings; printf %s "$STEP_TOTAL"', extra)
    assert out.splitlines()[-1] == "3"
    assert "PROMPT" in out


def test_prepare_skips_everything_when_docker_cannot_pass_devices() -> None:
    extra = """
DOCKER_KIND=linux-rootless
detect_dbus_socket() { DBUS_SOCKET="/run/dbus/system_bus_socket"; return 0; }
find_serial_devices() {
    SERIAL_FOUND_HOST_PATHS=(/dev/ttyACM0)
    SERIAL_FOUND_LABELS=(ttyACM0)
    SERIAL_FOUND_DISPLAYS=(/dev/ttyACM0)
}
"""
    out = _ok(
        'prepare_docker_mappings; printf %s "$TRANSPORT|$SERIAL_COMPOSE_HOST_PATH|$DBUS_SOCKET|$STEP_TOTAL"',
        extra,
    )
    assert out == "ui|||2"


def test_compose_writer_uses_detected_paths_only() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("write_docker_compose()", 1)[1].split("\ninstall_docker_stack()", 1)[0]
    assert "system_bus_socket:ro" in body
    assert '[ -n "$DBUS_SOCKET" ]' in body
    assert '[ -n "$SERIAL_COMPOSE_HOST_PATH" ]' in body
    assert "ttyACM0" not in body
    assert "serial-auto" not in body
