"""Which drive is a folder on? Speed history is kept per drive, keyed by the drive's identity
rather than its name: the volume UUID for local drives and the server/share address for network
storage, so renaming a drive keeps its history and two drives both called "Backup" don't share one.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class Volume:
    key: str          # stable identity
    label: str        # what to show people
    kind: str         # "local", "network" or "unknown"
    mount: str


def mount_point(path: str) -> str:
    path = os.path.realpath(path)
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def _mount_table() -> dict[str, tuple[str, str]]:
    """{mount point: (device or share, filesystem type)}"""
    table = {}
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():   # //user@server/share on /Volumes/x (smbfs, nodev, ...)
                if " on " in line and " (" in line:
                    dev, rest = line.split(" on ", 1)
                    mnt, opts = rest.rsplit(" (", 1)
                    table[mnt] = (dev, opts.split(",")[0])
        else:
            with open("/proc/mounts") as fh:
                for line in fh:
                    dev, mnt, fs = line.split()[:3]
                    table[mnt.replace("\\040", " ")] = (dev, fs)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return table


NETWORK_FS = {"smbfs", "nfs", "afpfs", "webdav", "cifs", "smb3", "nfs4", "avidfs", "avid", "isis", "fuse.sshfs"}


def identify(path: str) -> Volume:
    mnt = mount_point(path)
    dev, fs = _mount_table().get(mnt, ("", ""))
    label = os.path.basename(mnt) or ("Startup disk" if mnt == "/" else mnt)
    if fs.lower() in NETWORK_FS or dev.startswith("//"):
        share = dev.split("@", 1)[-1] if dev.startswith("//") else dev   # drop the user name
        return Volume(f"net:{fs}:{share.lower()}", label, "network", mnt)
    if sys.platform == "darwin":
        try:
            raw = subprocess.run(["/usr/sbin/diskutil", "info", "-plist", mnt],
                                 capture_output=True, timeout=10).stdout
            info = plistlib.loads(raw) if raw else {}
            if info.get("VolumeUUID"):
                return Volume(f"uuid:{info['VolumeUUID']}", info.get("VolumeName") or label, "local", mnt)
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
            pass
    try:
        st = os.stat(mnt)
        return Volume(f"dev:{dev or st.st_dev}:{mnt}", label, "local" if dev.startswith("/dev") else "unknown", mnt)
    except OSError:
        return Volume(f"path:{mnt}", label, "unknown", mnt)


_cache: dict[str, tuple[float, Volume | None]] = {}


def volume_for(path: str, max_age: float = 60.0) -> Volume | None:
    """identify(), cached briefly (identifying a drive can run diskutil). None if the path is gone."""
    now = time.monotonic()
    hit = _cache.get(path)
    if hit and now - hit[0] < max_age:
        return hit[1]
    try:
        vol = identify(path) if os.path.exists(path) else None
    except OSError:
        vol = None
    if vol is not None:          # never remember "not found": a drive plugged back in must be seen at once
        _cache[path] = (now, vol)
    else:
        _cache.pop(path, None)
    return vol


def sources_key(sources: list[str]) -> str | None:
    """The drive a copy reads from. None if the sources span several drives or can't be found."""
    keys = {v.key for v in (volume_for(s) for s in sources) if v}
    return keys.pop() if len(keys) == 1 else None
