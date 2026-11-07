"""Archive validation: every rule has a fixture archive built here."""

import copy
import io
import json
import stat
import zipfile
from pathlib import Path

import pytest

from app.plugins import package as pkg
from app.plugins.errors import PluginValidationError
from app.plugins.package import extract_package, validate_package

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "docs/dev/plugins/vectors/manifest.json").read_text("utf-8"))
SAMPLE = next(v["manifest"] for v in VECTORS["vectors"] if v["name"] == "sample")

CONFIG_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "api_key": {"type": "string", "writeOnly": True}},
}


def good_files(manifest=None):
    return {
        "manifest.json": json.dumps(manifest or SAMPLE).encode(),
        "plugin.py": b"class Weather: ...\n",
        "README.md": b"# Weather\n",
        "README.fr.md": b"# Meteo\n",
        "LICENSE": b"MIT\n",
        "config.schema.json": json.dumps(CONFIG_SCHEMA).encode(),
        "i18n/en.json": b"{}",
        "i18n/fr.json": b"{}",
    }


def build(files=None, *, infos=None, compression=zipfile.ZIP_DEFLATED) -> bytes:
    """Build an archive; `infos` maps name -> ZipInfo overrides (mode, raw names, ...)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, data in (files or good_files()).items():
            info = (infos or {}).get(name)
            if info is None:
                info = zipfile.ZipInfo(name)
                info.external_attr = 0o644 << 16
                info.compress_type = compression
            zf.writestr(info, data)
    return buf.getvalue()


def link(name):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


def with_extra(**extra):
    return {**good_files(), **extra}


def refused(data) -> list[str]:
    with pytest.raises(PluginValidationError) as exc:
        validate_package(data)
    return exc.value.codes


def test_good_package_validates():
    result = validate_package(build())
    assert result.manifest.id == "acme.weather"
    assert result.secret_fields == {"api_key"}
    assert "plugin.py" in result.files


def test_good_package_from_path_and_file(tmp_path):
    path = tmp_path / "plugin.zip"
    path.write_bytes(build())
    assert validate_package(path).manifest.id == "acme.weather"
    with open(path, "rb") as fh:
        assert validate_package(fh).manifest.id == "acme.weather"


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "a/../../evil.py",
        "/etc/passwd",
        "C:/Windows/evil.py",
        "a\\..\\evil.py",
        "a//b.py",
        "./plugin2.py",
    ],
)
def test_zip_slip_and_unsafe_names(name):
    assert refused(build(with_extra(**{name: b"x"}))) == ["package_unsafe_path"]


def test_symlink_is_refused():
    files = with_extra(**{"vendor/lnk": b"/etc/passwd"})
    assert refused(build(files, infos={"vendor/lnk": link("vendor/lnk")})) == ["package_symlink"]


def test_special_file_and_executable_bit():
    info = zipfile.ZipInfo("dev")
    info.external_attr = (stat.S_IFCHR | 0o644) << 16
    assert refused(build(with_extra(dev=b""), infos={"dev": info})) == ["package_special_file"]
    info = zipfile.ZipInfo("run.sh")
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    assert refused(build(with_extra(**{"run.sh": b"#!/bin/sh"}), infos={"run.sh": info})) == [
        "package_executable"
    ]


@pytest.mark.parametrize(
    "name",
    [
        "vendor/_speedups.so",
        "vendor/_speedups.cpython-311-x86_64-linux-gnu.so",
        "vendor/lib/libfoo.so.1.2",
        "vendor/ext.pyd",
        "vendor/x.DLL",
        "vendor/x.dylib",
        "vendor/pkg-1.0.dist-info/native.so",
    ],
)
def test_native_extensions_are_refused_anywhere(name):
    assert refused(build(with_extra(**{name: b"x"}))) == ["package_native_extension"]


def test_native_binary_under_a_harmless_name():
    elf = b"\x7fELF" + b"\x00" * 60
    assert refused(build(with_extra(**{"data.bin": elf}))) == ["package_native_extension"]
    pe = bytearray(b"MZ" + b"\x00" * 62) + b"PE\x00\x00"
    pe[0x3C:0x40] = (64).to_bytes(4, "little")
    assert refused(build(with_extra(**{"x.txt": bytes(pe)}))) == ["package_native_extension"]
    assert validate_package(build(with_extra(**{"note.txt": b"MZ is a postal code"})))


def test_nested_zip_with_native_extension_is_refused():
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("lib/_ext.so", b"x")
    assert refused(build(with_extra(**{"vendor/deps.zip": inner.getvalue()}))) == [
        "package_native_extension"
    ]


def test_nested_zip_two_levels_deep_and_too_deep():
    def wrap(inner: bytes, name: str) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(name, inner)
        return buf.getvalue()

    so = wrap(b"x", "deep/_ext.pyd")
    deep = wrap(wrap(so, "b.zip"), "a.zip")
    assert refused(build(with_extra(**{"vendor/x.zip": deep}))) == ["package_native_extension"]
    chain = b"data"
    for i in range(pkg.MAX_NESTING_DEPTH + 2):
        chain = wrap(chain, f"n{i}.zip")
    assert refused(build(with_extra(**{"vendor/x.zip": chain}))) == ["package_nested_too_deep"]


def test_innocent_nested_zip_is_allowed_and_unreadable_one_is_not():
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("data.txt", b"hello")
    assert validate_package(build(with_extra(**{"vendor/data.zip": inner.getvalue()})))
    assert refused(build(with_extra(**{"vendor/bad.zip": b"PK\x03\x04garbage"}))) == [
        "package_bad_zip"
    ]


def test_bytecode_is_refused():
    assert refused(build(with_extra(**{"vendor/m.cpython-311.pyc": b"x"}))) == ["package_bytecode"]
    assert refused(build(with_extra(**{"__pycache__/m.txt": b"x"}))) == ["package_bytecode"]


def test_caps(monkeypatch):
    monkeypatch.setattr(pkg, "MAX_ENTRIES", 10)
    many = with_extra(**{f"vendor/f{i}.py": b"" for i in range(10)})
    assert refused(build(many)) == ["package_too_many_entries"]
    monkeypatch.setattr(pkg, "MAX_ENTRIES", 2000)
    monkeypatch.setattr(pkg, "MAX_UNCOMPRESSED_BYTES", 1000)
    assert refused(build(with_extra(**{"vendor/big.txt": b"a" * 2000}))) == [
        "package_uncompressed_too_large"
    ]
    monkeypatch.setattr(pkg, "MAX_UNCOMPRESSED_BYTES", 20 * 1024 * 1024)
    monkeypatch.setattr(pkg, "MAX_COMPRESSED_BYTES", 100)
    assert refused(build()) == ["package_too_large"]


def test_compressed_cap_uses_the_real_limit():
    assert pkg.MAX_COMPRESSED_BYTES == 5 * 1024 * 1024
    assert pkg.MAX_UNCOMPRESSED_BYTES == 20 * 1024 * 1024
    assert pkg.MAX_ENTRIES == 2000
    data = build(
        with_extra(**{"vendor/blob.bin": bytes(range(256)) * 25000}), compression=zipfile.ZIP_STORED
    )
    assert len(data) > pkg.MAX_COMPRESSED_BYTES
    assert refused(data) == ["package_too_large"]


def test_decompression_bomb_is_refused_by_declared_size():
    # 25 MiB of zeroes deflates to a few KiB: the uncompressed cap catches it.
    bomb = with_extra(**{"vendor/zeros.bin": bytes(25 * 1024 * 1024)})
    data = build(bomb)
    assert len(data) < pkg.MAX_COMPRESSED_BYTES
    assert refused(data) == ["package_uncompressed_too_large"]


def test_duplicate_and_case_colliding_entries():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in good_files().items():
            zf.writestr(name, data)
        zf.writestr("plugin.PY", b"x")
    assert refused(buf.getvalue()) == ["package_duplicate_entry"]
    clash = with_extra(**{"vendor": b"x", "vendor/m.py": b""})
    assert refused(build(clash)) == ["package_duplicate_entry"]


def test_encrypted_and_exotic_compression():
    data = bytearray(build())
    # Set the encryption bit in every central-directory header.
    pos = 0
    while (pos := data.find(b"PK\x01\x02", pos)) != -1:
        data[pos + 8] |= 1
        pos += 4
    assert refused(bytes(data)) == ["package_encrypted"]
    files = good_files()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_BZIP2) as zf:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_BZIP2
            zf.writestr(info, content)
    assert refused(buf.getvalue()) == ["package_compression"]


def test_not_a_zip():
    assert refused(b"not a zip at all") == ["package_bad_zip"]


@pytest.mark.parametrize("missing", ["manifest.json", "README.md", "LICENSE"])
def test_required_files(missing):
    files = good_files()
    del files[missing]
    assert refused(build(files)) == ["package_missing_file"]


def test_files_referenced_by_the_manifest_must_exist():
    files = good_files()
    for gone in ("plugin.py", "README.fr.md", "i18n/fr.json", "config.schema.json"):
        del files[gone]
    with pytest.raises(PluginValidationError) as exc:
        validate_package(build(files))
    assert {i.path for i in exc.value.issues} == {
        "plugin.py",
        "README.fr.md",
        "i18n/fr.json",
        "config.schema.json",
    }


def test_entry_package_form():
    m = copy.deepcopy(SAMPLE)
    m["entry"] = "weather:Weather"
    files = good_files(m)
    del files["plugin.py"]
    assert refused(build(files)) == ["package_missing_file"]
    files["weather/__init__.py"] = b""
    assert validate_package(build(files))


def test_readme_and_manifest_size_caps():
    assert refused(build(with_extra(**{"README.md": b"a" * (200 * 1024 + 1)}))) == [
        "package_readme_too_large"
    ]
    big = with_extra(**{"manifest.json": b" " * (64 * 1024 + 1)})
    assert refused(build(big)) == ["manifest_too_large"]


def test_manifest_problems_surface_with_their_codes():
    m = copy.deepcopy(SAMPLE)
    m["license"] = "WTFPL"
    m["permissions"]["ui.panel"] = {}
    codes = refused(build(good_files(m)))
    assert "license_not_accepted" in codes and "permission_reserved" in codes


def test_config_schema_is_validated_with_a_path():
    files = good_files()
    files["config.schema.json"] = json.dumps(
        {"type": "object", "properties": {"x": {"type": "object"}}}
    ).encode()
    with pytest.raises(PluginValidationError) as exc:
        validate_package(build(files))
    assert exc.value.issues[0].path.startswith("config.schema.json:")
    files["config.schema.json"] = b"{nope"
    assert refused(build(files)) == ["config_schema_invalid"]


def test_extraction_writes_plain_files_after_validation(tmp_path):
    dest = tmp_path / "out"
    result = extract_package(build(), dest)
    assert (dest / "plugin.py").read_bytes() == b"class Weather: ...\n"
    assert (dest / "i18n" / "en.json").is_file()
    assert stat.S_IMODE((dest / "plugin.py").stat().st_mode) == 0o644
    assert set(result.files) == {
        p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()
    }


def test_extraction_refuses_a_hostile_archive_without_writing(tmp_path):
    dest = tmp_path / "out"
    with pytest.raises(PluginValidationError):
        extract_package(build(with_extra(**{"../evil.py": b"x"})), dest)
    assert not dest.exists()
    assert not (tmp_path / "evil.py").exists()


def test_extraction_needs_an_empty_destination(tmp_path):
    (tmp_path / "keep").write_text("x")
    with pytest.raises(PluginValidationError) as exc:
        extract_package(build(), tmp_path)
    assert exc.value.codes == ["package_destination_not_empty"]


def test_unicode_and_case_collisions_are_refused_before_extraction(tmp_path):
    nfc, nfd = "caf\u00e9.txt", "cafe\u0301.txt"
    assert refused(build(with_extra(**{nfc: b"a", nfd: b"b"}))) == ["package_duplicate_entry"]
    assert refused(build(with_extra(**{"Foo": b"a", "foo/bar": b"b"}))) == [
        "package_duplicate_entry"
    ]
    assert refused(
        build(with_extra(**{"vendor/caf\u00e9": b"a", "vendor/cafe\u0301/x.py": b""}))
    ) == ["package_duplicate_entry"]
    dest = tmp_path / "out"
    with pytest.raises(PluginValidationError):
        extract_package(build(with_extra(**{nfc: b"a", nfd: b"b"})), dest)
    assert not dest.exists()


@pytest.mark.parametrize("name", ["vendor/\u202eevil.py", "vendor/a\u200bb.py"])
def test_invisible_characters_in_entry_names(name):
    assert refused(build(with_extra(**{name: b"x"}))) == ["package_unsafe_path"]


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    return buf.getvalue()


def test_bad_crc_is_a_typed_error():
    data = bytearray(
        build(
            with_extra(**{"vendor/data.txt": b"hello world" * 10}), compression=zipfile.ZIP_STORED
        )
    )
    i = data.index(b"hello world")
    data[i] ^= 0xFF
    assert refused(bytes(data)) == ["package_bad_zip"]


def test_nested_zip_with_prepended_junk_is_still_scanned():
    inner = b"JUNKJUNK" + _zip_bytes({"lib/evil.so": b"x"})
    assert refused(build(with_extra(**{"vendor/data.bin": inner}))) == ["package_native_extension"]


def test_pe_with_far_header_and_foreign_archives_are_refused():
    pe = bytearray(b"MZ" + b"\x00" * 1000)
    pe[0x3C:0x40] = (600).to_bytes(4, "little")
    pe[600:604] = b"PE\x00\x00"
    assert refused(build(with_extra(**{"vendor/a.bin": bytes(pe)}))) == ["package_native_extension"]
    import gzip

    assert refused(build(with_extra(**{"vendor/a.bin": gzip.compress(b"x")}))) == [
        "package_nested_archive"
    ]
    tar = bytearray(512)
    tar[257:262] = b"ustar"
    assert refused(build(with_extra(**{"vendor/a.bin": bytes(tar)}))) == ["package_nested_archive"]


def test_extraction_cleans_up_on_failure(tmp_path, monkeypatch):
    real = zipfile.ZipFile.open
    calls = {"n": 0}

    def flaky(self, name, *a, **k):
        # Validation reads each file once; fail on the extraction pass only.
        if isinstance(name, str):
            calls["n"] += 1
            if name == "plugin.py":
                raise zipfile.BadZipFile("boom")
        return real(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "open", flaky)
    dest = tmp_path / "out"
    with pytest.raises(PluginValidationError) as exc:
        extract_package(build(), dest)
    assert exc.value.codes == ["package_extract_failed"]
    assert not dest.exists()
    existing = tmp_path / "empty"
    existing.mkdir()
    with pytest.raises(PluginValidationError):
        extract_package(build(), existing)
    assert existing.is_dir() and list(existing.iterdir()) == []


def test_extracted_directories_are_0755(tmp_path):
    dest = tmp_path / "out"
    old = __import__("os").umask(0o002)
    try:
        extract_package(build(), dest)
    finally:
        __import__("os").umask(old)
    assert stat.S_IMODE((dest / "i18n").stat().st_mode) == 0o755
    assert stat.S_IMODE(dest.stat().st_mode) == 0o755


def test_entry_names_allow_zwnj_but_not_bidi():
    assert validate_package(build(with_extra(**{"vendor/\u067e\u200c\u06cc.txt": b"x"})))
    assert refused(build(with_extra(**{"vendor/a\u200eb.txt": b"x"}))) == ["package_unsafe_path"]


def test_failed_extraction_removes_the_parents_it_created(tmp_path, monkeypatch):
    real = zipfile.ZipFile.open

    def flaky(self, name, *a, **k):
        if name == "plugin.py":
            raise zipfile.BadZipFile("boom")
        return real(self, name, *a, **k)

    monkeypatch.setattr(zipfile.ZipFile, "open", flaky)
    with pytest.raises(PluginValidationError):
        extract_package(build(), tmp_path / "a" / "b" / "out")
    assert list(tmp_path.iterdir()) == []
