"""Kernel-side guards of the plugin child (Linux): a seccomp filter and no capabilities.

Both are applied by the runner (`_apply_limits`) after `PR_SET_NO_NEW_PRIVS` and
before any plugin code, and both survive everything the plugin can do (a seccomp
filter cannot be removed, dropped capabilities do not come back under
`no_new_privs`).

**The filter** (`RUNNER_DENIED`) answers `EPERM` to every syscall that creates a
task or runs a program: `clone`, `clone3`, `fork`, `vfork`, `execve`,
`execveat`, plus `unshare` and `setns` (no new namespace, a user namespace
included, and no joining one: Python 3.12+ exposes `os.unshare` and `os.setns`,
which raise no audit event; MSHL-212). It is the
guard that holds where `RLIMIT_NPROC` does not: the kernel exempts root from
that limit, so a child running as root (levels `none` and `bwrap` started by a
root server) could otherwise rebuild `_posixsubprocess` from its spec, or start
a thread (3.11 emitted no thread-start audit event), past the audit hook (MSHL-323). Threads are refused too: the
runner never starts one (its event loop is single-threaded, its self-pipe an
`os.pipe`) and `RLIMIT_NPROC` 0 already refuses them at every non-root level.
Allowing `clone(CLONE_THREAD)` only would also be fragile: `clone3` carries its
flags behind a user pointer seccomp cannot read. It also refuses every socket
(`socket`, `socketpair`, and `socketcall` on i386, MSHL-415): the child talks to
the server over pipes and reaches the network only through the server's `http`
capability, so no IP socket (loopback and the radio proxy included, at the
`setuid` level where no network namespace exists) nor Unix socket (abstract
ones have no permission check) is ever needed; `_socket.socketpair` raises no
audit event. And the paths to another process that raise no audit event either
(MSHL-417): `pidfd_open`, `pidfd_send_signal`, `pidfd_getfd` (`os.kill` is
audited, `signal.pidfd_send_signal` is not), `ptrace`, `process_vm_readv` and
`process_vm_writev` (what `sys.remote_exec`, 3.14, rests on).

A syscall of another ABI than the interpreter's (x32 on x86_64, `int 0x80`
i386 calls) is refused whatever its number. Unknown architectures get no
filter: the caller decides whether that is fatal (it is when the child runs as
root, where nothing else holds, and at the `setuid` level, where the socket
refusal is the only loopback guard).

**The bwrap filter** (`BWRAP_DENIED`, MSHL-413) is a second, independent
program the `bwrap` level hands to bubblewrap (`--seccomp FD`,
`app/plugins/isolation/bwrap.py`): bwrap installs it right before it executes
the interpreter, so it binds the interpreter's start-up too, and it stays when
the runner's filter is stacked on top. It denies the runner's list but `execve`
(bwrap's own exec of the interpreter needs it), plus kernel attack surface no
plugin needs: `io_uring_*`, `userfaultfd`, `bpf`, `perf_event_open`, the key
syscalls (`keyctl`, `add_key`, `request_key`) and the mount API (`mount`,
`umount2`, `pivot_root`, `open_tree`, `move_mount`, `fsopen`, `fsconfig`,
`fsmount`, `fspick`, `mount_setattr`). A bypass of one filter still meets the
other.

**Capabilities.** A server that is not root but keeps `CAP_SETUID` and
`CAP_SETGID` as ambient capabilities (the Docker image with
`MESHLOOM_RUN_AS_USER`, docker-entrypoint.sh) hands them down through the uid
drop and the exec: without this step the plugin could switch to the server's
uid. The runner clears the ambient set and empties the effective, permitted and
inheritable sets, then checks `/proc/self/status`.
"""

import ctypes
import os
import struct
import sys
from typing import Any

PR_SET_SECCOMP = 22
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4
SECCOMP_MODE_FILTER = 2
EPERM = 1
EINVAL = 22

SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000
# offsetof(struct seccomp_data, nr) and offsetof(struct seccomp_data, arch)
_OFFSET_NR = 0
_OFFSET_ARCH = 4
_BPF_LD_W_ABS = 0x20
_BPF_JEQ_K = 0x15
_BPF_JGE_K = 0x35
_BPF_RET_K = 0x06
_X32_SYSCALL_BIT = 0x40000000
_LINUX_CAPABILITY_VERSION_3 = 0x20080522

# What the runner's filter refuses, and what the bwrap filter refuses (see above).
RUNNER_DENIED = frozenset(
    {
        "clone",
        "clone3",
        "fork",
        "vfork",
        "execve",
        "execveat",
        "unshare",
        "setns",
        "socket",
        "socketpair",
        "socketcall",
        "pidfd_open",
        "pidfd_send_signal",
        "pidfd_getfd",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
    }
)
KERNEL_SURFACE = frozenset(
    {
        "io_uring_setup",
        "io_uring_enter",
        "io_uring_register",
        "userfaultfd",
        "bpf",
        "perf_event_open",
        "keyctl",
        "add_key",
        "request_key",
        "mount",
        "umount2",
        "pivot_root",
        "open_tree",
        "move_mount",
        "fsopen",
        "fsconfig",
        "fsmount",
        "fspick",
        "mount_setattr",
    }
)
BWRAP_DENIED = (RUNNER_DENIED - {"execve"}) | KERNEL_SURFACE

# Syscalls numbered the same on every ABI below (allocated after the tables were
# unified, Linux 5.1+).
_UNIFIED = {
    "pidfd_send_signal": 424,
    "io_uring_setup": 425,
    "io_uring_enter": 426,
    "io_uring_register": 427,
    "open_tree": 428,
    "move_mount": 429,
    "fsopen": 430,
    "fsconfig": 431,
    "fsmount": 432,
    "fspick": 433,
    "pidfd_open": 434,
    "clone3": 435,
    "pidfd_getfd": 438,
    "mount_setattr": 442,
}
# The generic table (asm-generic, scripts/syscall.tbl): aarch64 and riscv64. It has
# no fork, no vfork and no socketcall.
_GENERIC = {
    **_UNIFIED,
    "umount2": 39,
    "mount": 40,
    "pivot_root": 41,
    "unshare": 97,
    "ptrace": 117,
    "socket": 198,
    "socketpair": 199,
    "add_key": 217,
    "request_key": 218,
    "keyctl": 219,
    "clone": 220,
    "execve": 221,
    "perf_event_open": 241,
    "setns": 268,
    "process_vm_readv": 270,
    "process_vm_writev": 271,
    "bpf": 280,
    "execveat": 281,
    "userfaultfd": 282,
}
# i386 and arm (EABI) share the low numbers they both inherited.
_I386_ARM_LOW = {"fork": 2, "execve": 11, "mount": 21, "ptrace": 26, "umount2": 52}

# AUDIT_ARCH_* value and the syscall numbers this module may deny, per ABI (checked
# against the kernel's syscall tables, v6.12: arch/x86/entry/syscalls/syscall_64.tbl
# and syscall_32.tbl, arch/arm/tools/syscall.tbl, scripts/syscall.tbl). A name an
# ABI lacks (fork on aarch64, socketcall outside i386) is simply not there.
TABLES: dict[str, tuple[int, dict[str, int]]] = {
    "x86_64": (
        0xC000003E,
        {
            **_UNIFIED,
            "socket": 41,
            "socketpair": 53,
            "clone": 56,
            "fork": 57,
            "vfork": 58,
            "execve": 59,
            "ptrace": 101,
            "pivot_root": 155,
            "mount": 165,
            "umount2": 166,
            "add_key": 248,
            "request_key": 249,
            "keyctl": 250,
            "unshare": 272,
            "perf_event_open": 298,
            "setns": 308,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "bpf": 321,
            "execveat": 322,
            "userfaultfd": 323,
        },
    ),
    "i386": (
        0x40000003,
        {
            **_UNIFIED,
            **_I386_ARM_LOW,
            "socketcall": 102,
            "clone": 120,
            "vfork": 190,
            "pivot_root": 217,
            "add_key": 286,
            "request_key": 287,
            "keyctl": 288,
            "unshare": 310,
            "perf_event_open": 336,
            "setns": 346,
            "process_vm_readv": 347,
            "process_vm_writev": 348,
            "bpf": 357,
            "execveat": 358,
            "socket": 359,
            "socketpair": 360,
            "userfaultfd": 374,
        },
    ),
    "aarch64": (0xC00000B7, dict(_GENERIC)),
    "arm": (
        0x40000028,
        {
            **_UNIFIED,
            **_I386_ARM_LOW,
            "clone": 120,
            "vfork": 190,
            "pivot_root": 218,
            "socket": 281,
            "socketpair": 288,
            "add_key": 309,
            "request_key": 310,
            "keyctl": 311,
            "unshare": 337,
            "perf_event_open": 364,
            "setns": 375,
            "process_vm_readv": 376,
            "process_vm_writev": 377,
            "bpf": 386,
            "execveat": 387,
            "userfaultfd": 388,
        },
    ),
    "riscv64": (0xC00000F3, dict(_GENERIC)),
}


class SeccompError(Exception):
    pass


def native_abi(machine: str | None = None, pointer_size: int | None = None) -> str | None:
    """The interpreter's ABI, not the kernel's: a 32-bit userland on a 64-bit kernel
    (armhf on an arm64 Raspberry Pi) makes arm syscalls although uname says aarch64."""
    machine = (machine or os.uname().machine).lower()
    wide = (pointer_size or struct.calcsize("P")) == 8
    if machine in ("x86_64", "amd64"):
        return "x86_64" if wide else "i386"
    if machine in ("i386", "i486", "i586", "i686"):
        return "i386"
    if machine in ("aarch64", "arm64", "armv8l", "armv8b"):
        return "aarch64" if wide else "arm"
    if machine.startswith("arm"):
        return "arm"
    if machine == "riscv64" and wide:
        return "riscv64"
    return None


def denied_numbers(abi: str, names: frozenset[str] = RUNNER_DENIED) -> list[int]:
    """The numbers of `names` on `abi`, sorted (a name the ABI lacks is skipped)."""
    _arch, numbers = TABLES[abi]
    return sorted({numbers[name] for name in names if name in numbers})


def build_filter(
    abi: str, names: frozenset[str] = RUNNER_DENIED
) -> list[tuple[int, int, int, int]]:
    """The BPF program for `abi`: another arch, or a syscall in `names`, gets EPERM."""
    arch, _numbers = TABLES[abi]
    deny = SECCOMP_RET_ERRNO | EPERM
    denied = denied_numbers(abi, names)
    prog = [
        (_BPF_LD_W_ABS, 0, 0, _OFFSET_ARCH),
        # Same arch: skip the refusal that follows.
        (_BPF_JEQ_K, 1, 0, arch),
        (_BPF_RET_K, 0, 0, deny),
        (_BPF_LD_W_ABS, 0, 0, _OFFSET_NR),
    ]
    if abi == "x86_64":
        # x32 syscalls share the arch value and carry this bit: refuse them all.
        prog.append((_BPF_JGE_K, len(denied) + 1, 0, _X32_SYSCALL_BIT))
    for index, number in enumerate(denied):
        # A match jumps over the remaining checks and the allow, onto the refusal.
        prog.append((_BPF_JEQ_K, len(denied) - index, 0, number))
    prog.append((_BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    prog.append((_BPF_RET_K, 0, 0, deny))
    return prog


def program_bytes(prog: list[tuple[int, int, int, int]]) -> bytes:
    """`prog` as the kernel's `struct sock_filter` array, in native byte order: what
    bubblewrap reads from its `--seccomp` descriptor."""
    return b"".join(struct.pack("=HBBI", *insn) for insn in prog)


class _SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter))]


def install_filter(prctl: Any, abi: str) -> None:
    """Install the filter (`PR_SET_NO_NEW_PRIVS` must be set). Raises SeccompError."""
    prog = build_filter(abi)
    array = (_SockFilter * len(prog))(*(_SockFilter(*insn) for insn in prog))
    fprog = _SockFprog(len(prog), ctypes.cast(array, ctypes.POINTER(_SockFilter)))
    # The runner's prctl declares its trailing arguments `unsigned long`: pass the address.
    if prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.addressof(fprog), 0, 0) != 0:
        raise SeccompError(f"PR_SET_SECCOMP: errno {ctypes.get_errno()}")


def libc_prctl() -> Any:
    """libc's prctl, its trailing arguments declared `unsigned long`."""
    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
    prctl.restype = ctypes.c_int
    return prctl


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def capability_sets(status_path: str = "/proc/self/status") -> dict[str, int]:
    """CapInh, CapPrm, CapEff and CapAmb of this process, as integers."""
    sets: dict[str, int] = {}
    with open(status_path, encoding="ascii") as fh:
        for line in fh:
            key, _, value = line.partition(":")
            if key in ("CapInh", "CapPrm", "CapEff", "CapAmb"):
                sets[key] = int(value.strip(), 16)
    return sets


def drop_capabilities(prctl: Any) -> None:
    """A non-root child: clear the ambient set, empty the other three, check. Raises
    SeccompError. Root keeps its sets (dropping them would not make it less root:
    the uid owns the files); the filter and the OS level are what hold there."""
    if os.geteuid() == 0:
        return
    # EINVAL before Linux 4.3 (no ambient set): nothing to clear there.
    if (
        prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0
        and ctypes.get_errno() != EINVAL
    ):
        raise SeccompError(f"PR_CAP_AMBIENT_CLEAR_ALL: errno {ctypes.get_errno()}")
    libc = ctypes.CDLL(None, use_errno=True)
    header = _CapHeader(_LINUX_CAPABILITY_VERSION_3, 0)
    data = (_CapData * 2)()
    if libc.capset(ctypes.byref(header), data) != 0:
        raise SeccompError(f"capset: errno {ctypes.get_errno()}")
    left = sorted(k for k, v in capability_sets().items() if v)
    if left:
        raise SeccompError(f"capabilities left after the drop: {', '.join(left)}")


def apply(prctl: Any, *, required: bool) -> str | None:
    """Drop the capabilities, then install the filter. Returns the filtered ABI, or
    None off Linux and, when `required` is false, on an unknown architecture."""
    if not sys.platform.startswith("linux"):
        return None
    drop_capabilities(prctl)
    abi = native_abi()
    if abi is None:
        if required:
            raise SeccompError(f"no seccomp table for {os.uname().machine}")
        return None
    install_filter(prctl, abi)
    return abi
