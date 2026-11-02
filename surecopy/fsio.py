"""Small filesystem helpers with the macOS specifics in one place."""

from __future__ import annotations

import errno
import fcntl
import os
import sys
import time

F_NOCACHE = 48      # macOS: bypass the unified buffer cache for this descriptor
F_FULLFSYNC = 51    # macOS: ask the device to flush its write cache

IS_MAC = sys.platform == "darwin"

# Characters SMB shares and Avid Nexis reject or mangle.
UNSAFE_CHARS = set('\\:*?"<>|')
MAX_NAME_BYTES = 255
MAX_PATH_BYTES = 1024


def no_cache(fd: int) -> None:
    """Make reads come from the disk or share, not memory. macOS only; harmless elsewhere."""
    if IS_MAC:
        try:
            fcntl.fcntl(fd, F_NOCACHE, 1)
        except OSError:
            pass


def flush(fd: int, full: bool = False) -> None:
    os.fsync(fd)
    if full and IS_MAC:
        try:
            fcntl.fcntl(fd, F_FULLFSYNC)
        except OSError:
            pass  # some network filesystems refuse it; fsync already ran


def flush_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def same_mtime(a_ns: int, b_ns: int, window_ns: int = 2_000_000_000) -> bool:
    """Drives and shares round modified times (FAT32 to 2 seconds), so allow a 2 second window,
    like rsync's --modify-window for FAT drives."""
    return abs(a_ns - b_ns) <= window_ns


def publish(partial: str, final: str, retries: int = 5) -> None:
    """Rename a finished .partial into place without ever replacing an existing file.

    Hard links give an atomic no-overwrite on most local filesystems. SMB and Nexis
    often refuse links, so fall back to an existence check plus rename, retrying
    briefly because network shares sometimes fail a rename right after a write.
    """
    delay = 0.2
    for attempt in range(retries):
        try:
            try:
                os.link(partial, final)
                os.unlink(partial)
                return
            except FileExistsError:
                raise
            except OSError as exc:
                if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK, errno.EACCES):
                    raise
            if os.path.lexists(final):
                raise FileExistsError(errno.EEXIST, "Destination file already exists", final)
            os.rename(partial, final)
            return
        except FileExistsError:
            raise
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def replace_into(partial: str, final: str, expected: tuple[int, int], retries: int = 5) -> None:
    """Swap a finished .partial in for an older file, but only if that file is still the
    one we decided to replace (same size and modified time). Otherwise raise FileExistsError.

    rename() over an existing file is atomic on APFS. Some SMB servers refuse it, so fall
    back to moving the old file aside, renaming the new one in, then removing the old one.
    If the second step fails the old file is put back.
    """
    delay = 0.2
    for attempt in range(retries):
        try:
            st = os.stat(final)
            if (st.st_size, st.st_mtime_ns) != expected:
                raise FileExistsError(errno.EEXIST, "Destination file changed during the copy", final)
            try:
                os.replace(partial, final)
                return
            except OSError as exc:
                if exc.errno not in (errno.EEXIST, errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP):
                    raise
            aside = temp_name(final, ".surecopy-old")
            os.rename(final, aside)
            try:
                os.rename(partial, final)
            except OSError:
                os.rename(aside, final)
                raise
            try:
                os.unlink(aside)
            except OSError:
                pass
            return
        except FileExistsError:
            raise
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def temp_name(final: str, suffix: str) -> str:
    """Hidden working name next to `final`. Long names are shortened with a hash so the temp
    name never exceeds the 255-byte filename limit (a 244-character name is still valid)."""
    import hashlib
    head, tail = os.path.split(final)
    name = f".{tail}{suffix}"
    if len(name.encode()) > MAX_NAME_BYTES:
        digest = hashlib.sha1(tail.encode()).hexdigest()[:16]
        name = f".{tail.encode()[:120].decode(errors='ignore')}~{digest}{suffix}"
    return os.path.join(head, name)


def volume_present(root: str) -> bool:
    return os.path.isdir(root)


def name_problems(relative: str) -> list[str]:
    """Problems that would break or mangle this path on SMB or Nexis."""
    problems = []
    parts = relative.split("/")
    for part in parts:
        bad = sorted(set(part) & UNSAFE_CHARS)
        if bad:
            problems.append(f"'{part}' contains {' '.join(bad)}")
        if part.endswith((" ", ".")) and part not in (".", ".."):
            problems.append(f"'{part}' ends with a space or period")
        if len(part.encode()) > MAX_NAME_BYTES:
            problems.append(f"'{part[:40]}...' is longer than {MAX_NAME_BYTES} bytes")
    if len(relative.encode()) > MAX_PATH_BYTES:
        problems.append(f"path is longer than {MAX_PATH_BYTES} bytes")
    return problems
