"""Plugin archive (`plugin.zip`) validation, before anything is extracted.

Everything is decided from the central directory and bounded reads: entry names,
modes, declared sizes and counts first, contents (manifest, README, nested zips,
magic bytes) only within hard caps. Layout and limits: docs/dev/plugins.md.
"""

import io
import json
import re
import shutil
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from app.plugins.config_schema import check_config_schema
from app.plugins.errors import PluginIssue, PluginValidationError
from app.plugins.manifest import (
    MANIFEST_MAX_BYTES,
    Manifest,
    Origin,
    contains_forbidden,
    load_manifest_json,
)

MAX_COMPRESSED_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_README_BYTES = 200 * 1024
MAX_CONFIG_SCHEMA_BYTES = 64 * 1024
MAX_I18N_BYTES = 256 * 1024
MAX_NAME_LENGTH = 300
MAX_SEGMENT_LENGTH = 100
MAX_NESTING_DEPTH = 3

NATIVE_SUFFIX_RE = re.compile(r"\.(so|pyd|dll|dylib)(\.\d+)*$", re.IGNORECASE)
BYTECODE_SUFFIXES = (".pyc", ".pyo")
NESTED_ARCHIVE_SUFFIXES = (".zip", ".whl", ".egg", ".jar", ".pyz", ".apk")
_ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_EOCD = b"PK\x05\x06"
_MACHO_MAGICS = {
    bytes.fromhex(h)
    for h in ("feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe", "bebafeca")
}


@dataclass(frozen=True)
class ValidatedPackage:
    manifest: Manifest
    files: tuple[str, ...]
    uncompressed_size: int
    secret_fields: frozenset[str]


def _fail(code: str, path: str, message: str) -> PluginValidationError:
    return PluginValidationError([PluginIssue(code, path, message)])


def _check_name(info: zipfile.ZipInfo) -> str:
    """Return the safe normalised name of an entry (no trailing slash) or raise."""
    name = info.filename
    shown = name[:80]
    if not name or len(name) > MAX_NAME_LENGTH:
        raise _fail("package_unsafe_path", shown, "entry name is empty or too long")
    if "\\" in name or contains_forbidden(name):
        raise _fail(
            "package_unsafe_path",
            shown,
            "entry name has control or invisible characters or a backslash",
        )
    if name.startswith("/") or _WINDOWS_DRIVE.match(name):
        raise _fail("package_unsafe_path", shown, "absolute paths are refused")
    segments = name.rstrip("/").split("/")
    if any(s in ("", ".", "..") or len(s) > MAX_SEGMENT_LENGTH for s in segments):
        raise _fail("package_unsafe_path", shown, "entry name escapes the package or is malformed")
    return "/".join(segments)


def _check_entry_kind(info: zipfile.ZipInfo, name: str) -> bool:
    """Refuse links, special files, executables and encryption. Returns is_dir."""
    mode = info.external_attr >> 16
    is_dir = info.is_dir()
    if stat.S_ISLNK(mode):
        raise _fail("package_symlink", name, "symbolic links are refused")
    if mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise _fail("package_special_file", name, "only regular files and directories are allowed")
    if is_dir and stat.S_IFMT(mode) == stat.S_IFREG:
        raise _fail("package_special_file", name, "inconsistent entry type")
    if not is_dir and mode & 0o111:
        raise _fail("package_executable", name, "executable files are refused")
    if info.flag_bits & 0x1:
        raise _fail("package_encrypted", name, "encrypted entries are refused")
    if info.compress_type not in _ALLOWED_COMPRESSION:
        raise _fail("package_compression", name, "only stored and deflated entries are allowed")
    return is_dir


def _native_magic(data: bytes) -> bool:
    """ELF, Mach-O, or a real PE: 'MZ', an e_lfanew inside the file and 'PE\\0\\0' there."""
    head = data[:4]
    if head == b"\x7fELF" or head in _MACHO_MAGICS:
        return True
    if data[:2] == b"MZ" and len(data) >= 0x40:
        off = int.from_bytes(data[0x3C:0x40], "little")
        return off + 4 <= len(data) and data[off : off + 4] == b"PE\x00\x00"
    return False


def _foreign_archive(head: bytes) -> bool:
    """gzip, bzip2, xz and tar: not scanned, so not accepted."""
    return (
        head[:2] == b"\x1f\x8b"
        or head[:3] == b"BZh"
        or head[:6] == b"\xfd7zXZ\x00"
        or head[257:262] == b"ustar"
    )


def _looks_like_zip(data: bytes) -> bool:
    """A zip starts with a local header, or has an end-of-central-directory record near
    its tail (zipfile opens archives with junk prepended)."""
    return data[:4] in _ZIP_MAGIC or _EOCD in data[-(0xFFFF + 22) :]


def _fold(name: str) -> str:
    """Collision key: names that a case- or normalisation-insensitive filesystem merges."""
    return unicodedata.normalize("NFC", name).casefold()


def _check_name_content(name: str) -> None:
    lower = name.lower()
    if NATIVE_SUFFIX_RE.search(lower):
        raise _fail("package_native_extension", name, "native extensions are refused anywhere")
    if lower.endswith(BYTECODE_SUFFIXES) or "/__pycache__/" in "/" + lower:
        raise _fail("package_bytecode", name, "compiled bytecode is refused, ship the source")


@dataclass
class _Budget:
    entries: int = 0
    size: int = 0

    def add(self, count: int, size: int) -> None:
        self.entries += count
        self.size += size
        if self.entries > MAX_ENTRIES:
            raise _fail("package_too_many_entries", "", f"more than {MAX_ENTRIES} entries")
        if self.size > MAX_UNCOMPRESSED_BYTES:
            raise _fail("package_uncompressed_too_large", "", "uncompressed size is over 20 MiB")


def _scan_content(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, name: str, budget: _Budget, depth: int
) -> None:
    """Content checks on one file (best effort): native and foreign archive signatures,
    nested zips (their entries share the budget). Reading it all also checks its CRC."""
    if info.file_size == 0:
        return
    with zf.open(info) as fh:
        data = fh.read()  # bounded: declared sizes were added to the 20 MiB budget
    head = data[:512]
    if _native_magic(data):
        raise _fail("package_native_extension", name, "native binaries are refused anywhere")
    if _foreign_archive(head):
        raise _fail("package_nested_archive", name, "tar, gzip, bzip2 and xz are refused")
    if not _looks_like_zip(data):
        if name.lower().endswith(NESTED_ARCHIVE_SUFFIXES):
            raise _fail("package_bad_zip", name, "nested archive is not a readable zip")
        return
    if depth >= MAX_NESTING_DEPTH:
        raise _fail("package_nested_too_deep", name, "zip files are nested too deeply")
    try:
        inner = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _fail("package_bad_zip", name, "nested archive is not a readable zip") from None
    _walk(inner, budget, depth + 1, f"{name}!")


def _walk(
    zf: zipfile.ZipFile, budget: _Budget, depth: int, prefix: str
) -> dict[str, zipfile.ZipInfo]:
    infos = zf.infolist()
    budget.add(len(infos), 0)
    seen: dict[str, zipfile.ZipInfo] = {}
    folded: set[str] = set()
    for info in infos:
        name = _check_name(info)
        shown = prefix + name
        is_dir = _check_entry_kind(info, shown)
        if _fold(name) in folded:
            raise _fail("package_duplicate_entry", shown, "duplicate entry (case or Unicode form)")
        folded.add(_fold(name))
        if is_dir:
            continue
        _check_name_content(prefix + name)
        budget.add(0, info.file_size)
        seen[name] = info
    file_keys = {_fold(n) for n in seen}
    for name in seen:
        parts = name.split("/")
        if any(_fold("/".join(parts[:i])) in file_keys for i in range(1, len(parts))):
            raise _fail(
                "package_duplicate_entry", prefix + name, "a file is also used as a directory"
            )
    for name, info in seen.items():
        _scan_content(zf, info, prefix + name, budget, depth)
    return seen


def _read_capped(zf: zipfile.ZipFile, name: str, limit: int, code: str) -> bytes:
    info = zf.getinfo(name)
    if info.file_size > limit:
        raise _fail(code, name, f"{name} is over {limit // 1024} KiB")
    try:
        with zf.open(info) as fh:
            data = fh.read(limit + 1)
    except (zipfile.BadZipFile, EOFError, OSError):
        raise _fail("package_bad_zip", name, "entry cannot be read") from None
    if len(data) > limit:
        raise _fail(code, name, f"{name} is over {limit // 1024} KiB")
    return data


def _open(source: Path | bytes | BinaryIO) -> zipfile.ZipFile:
    if isinstance(source, Path):
        size = source.stat().st_size
        handle: BinaryIO | Path = source
    elif isinstance(source, bytes):
        size = len(source)
        handle = io.BytesIO(source)
    else:
        source.seek(0, io.SEEK_END)
        size = source.tell()
        source.seek(0)
        handle = source
    if size > MAX_COMPRESSED_BYTES:
        raise _fail("package_too_large", "", "archive is over 5 MiB")
    try:
        return zipfile.ZipFile(handle)
    except zipfile.BadZipFile:
        raise _fail("package_bad_zip", "", "not a readable zip archive") from None


_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)


def validate_package(
    source: Path | bytes | BinaryIO, *, origin: Origin = "third_party"
) -> ValidatedPackage:
    """Validate an archive without extracting it.

    Structural and safety failures raise at the first problem (nothing else in the
    archive is trusted); manifest and config-schema problems list every issue.
    Every file is read once (CRC included); a corrupt archive is `package_bad_zip`.
    """
    try:
        return _validate_package(source, origin)
    except _ZIP_ERRORS:
        raise _fail("package_bad_zip", "", "the archive is corrupt or unreadable") from None


def _validate_package(source: Path | bytes | BinaryIO, origin: Origin) -> ValidatedPackage:
    with _open(source) as zf:
        files = _walk(zf, _Budget(), 0, "")
        for required in ("manifest.json", "README.md", "LICENSE"):
            if required not in files:
                raise _fail("package_missing_file", required, f"{required} is required")
        manifest = load_manifest_json(
            _read_capped(zf, "manifest.json", MANIFEST_MAX_BYTES, "manifest_too_large"),
            origin=origin,
        )
        issues: list[PluginIssue] = []

        def need(path: str, what: str) -> bool:
            if path not in files:
                issues.append(
                    PluginIssue("package_missing_file", path, f"{what} {path} is missing")
                )
                return False
            return True

        entry = manifest.entry_path
        if entry.endswith(".py"):
            need(entry, "entry module")
        elif f"{entry}/__init__.py" not in files:
            issues.append(
                PluginIssue(
                    "package_missing_file", entry, f"entry package {entry}/__init__.py is missing"
                )
            )
        for path in {"README.md", *manifest.readme.values()}:
            if need(path, "readme"):
                _read_capped(zf, path, MAX_README_BYTES, "package_readme_too_large")
        for path in manifest.i18n.values():
            if need(path, "i18n file"):
                data = _read_capped(zf, path, MAX_I18N_BYTES, "package_file_too_large")
                try:
                    if not isinstance(json.loads(data), dict):
                        raise ValueError
                except ValueError:
                    issues.append(
                        PluginIssue("package_i18n_invalid", path, "i18n file must be a JSON object")
                    )
        secrets: frozenset[str] = frozenset()
        if manifest.config_schema and need(manifest.config_schema, "config schema"):
            data = _read_capped(
                zf, manifest.config_schema, MAX_CONFIG_SCHEMA_BYTES, "package_file_too_large"
            )
            try:
                secrets = check_config_schema(json.loads(data))
            except ValueError as exc:
                if isinstance(exc, PluginValidationError):
                    issues.extend(
                        PluginIssue(i.code, f"{manifest.config_schema}:{i.path}", i.message)
                        for i in exc.issues
                    )
                else:
                    issues.append(
                        PluginIssue(
                            "config_schema_invalid", manifest.config_schema, "not valid JSON"
                        )
                    )
        if issues:
            raise PluginValidationError(issues)
        total = sum(i.file_size for i in files.values())
        return ValidatedPackage(manifest, tuple(sorted(files)), total, secrets)


def extract_package(
    source: Path | bytes | BinaryIO, dest: Path, *, origin: Origin = "third_party"
) -> ValidatedPackage:
    """Validate, then extract into the empty directory `dest` (files 0644, dirs 0755).

    Entry names were validated, and every target is re-checked to stay under `dest`.
    """
    result = validate_package(source, origin=origin)
    if isinstance(source, Path):
        zf = zipfile.ZipFile(source)
    elif isinstance(source, bytes):
        zf = zipfile.ZipFile(io.BytesIO(source))
    else:
        source.seek(0)
        zf = zipfile.ZipFile(source)
    root = dest.resolve()
    if root.exists() and any(root.iterdir()):
        raise _fail("package_destination_not_empty", str(dest), "destination must be empty")
    existed = root.exists()
    created_top = root
    while not created_top.parent.exists():
        created_top = created_top.parent
    root.mkdir(parents=True, exist_ok=True)
    try:
        with zf:
            for name in result.files:
                target = (root / PurePosixPath(name)).resolve()
                if not target.is_relative_to(root):
                    raise _fail("package_unsafe_path", name, "entry escapes the destination")
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with zf.open(name) as src, open(target, "xb") as out:
                    while chunk := src.read(64 * 1024):
                        written += len(chunk)
                        if written > MAX_UNCOMPRESSED_BYTES:
                            raise _fail(
                                "package_uncompressed_too_large", name, "entry is too large"
                            )
                        out.write(chunk)
                target.chmod(0o644)
        for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
            if directory != root or not existed:
                directory.chmod(0o755)
    except BaseException as exc:
        # No partial tree, whatever went wrong.
        if existed:
            for child in root.iterdir():
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        else:
            shutil.rmtree(created_top, ignore_errors=True)
        if isinstance(exc, _ZIP_ERRORS + (OSError,)):
            raise _fail("package_extract_failed", "", "the archive could not be extracted") from exc
        raise
    return result
