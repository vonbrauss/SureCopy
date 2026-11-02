"""Walk the sources and build the list of files a job will copy."""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass, field

from .fsio import name_problems
from .models import SourceFile

PARTIAL_SUFFIX = ".surecopy-partial"

DEFAULT_EXCLUDES = [
    ".DS_Store", "._*", ".Spotlight-V100", ".Trashes", ".fseventsd",
    ".TemporaryItems", ".DocumentRevisions-V100", "*" + PARTIAL_SUFFIX, "*.surecopy-old",
]


# One-click exclude presets. Folder or file names, matched anywhere in the tree.
EXCLUDE_PRESETS = {
    "caches": ("Caches & render files", [
        "Media Cache Files", "Media Cache", "Peak Files", "*.pek", "*.cfa", "*.ims",
        "Adobe Premiere Pro Video Previews", "Adobe Premiere Pro Audio Previews",
        "Adobe After Effects Disk Cache", "CacheClip", "Render Files", "Shared Render Files",
        ".fcpcache", "Thumbnail Media",
    ]),
    "system": ("System junk (Trash, temp files)", [
        ".Trash", "Thumbs.db", "desktop.ini", "*.tmp", "~$*",
    ]),
}


@dataclass
class ScanResult:
    files: list[SourceFile] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)   # relative, including empty ones
    warnings: list[str] = field(default_factory=list)      # shown, but do not block
    problems: list[str] = field(default_factory=list)      # block the job until fixed

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


def excluded(name: str, relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) or fnmatch.fnmatchcase(relative, p) for p in patterns)


def scan(sources: list[str], excludes: list[str] | None = None, use_defaults: bool = True) -> ScanResult:
    patterns = (DEFAULT_EXCLUDES if use_defaults else []) + list(excludes or [])
    result = ScanResult()
    roots_seen: dict[str, str] = {}

    for source in sources:
        source = os.path.abspath(source.rstrip("/") or "/")
        top = os.path.basename(source)
        if not os.path.lexists(source):
            result.problems.append(f"Source not found: {source}")
            continue
        key = top.casefold()
        if key in roots_seen:
            result.problems.append(
                f"Two sources share the name '{top}' ({roots_seen[key]} and {source}); "
                "they would land in the same folder")
            continue
        roots_seen[key] = source

        st = os.lstat(source)
        if stat.S_ISLNK(st.st_mode):
            result.warnings.append(f"Skipped symbolic link: {source}")
            continue
        if stat.S_ISREG(st.st_mode):
            result.files.append(SourceFile(source, top, source, st.st_size, st.st_mtime_ns, st.st_mode))
            continue

        result.directories.append(top)
        for dirpath, dirnames, filenames in os.walk(source, onerror=lambda e: result.problems.append(
                f"Can't read {e.filename}: {e.strerror}")):
            rel_dir = os.path.relpath(dirpath, os.path.dirname(source)).replace(os.sep, "/")
            keep = []
            for d in sorted(dirnames):
                rel = f"{rel_dir}/{d}"
                full = os.path.join(dirpath, d)
                if excluded(d, rel, patterns):
                    continue
                if os.path.islink(full):
                    result.warnings.append(f"Skipped symbolic link: {full}")
                    continue
                keep.append(d)
                result.directories.append(rel)
            dirnames[:] = keep
            for name in sorted(filenames):
                rel = f"{rel_dir}/{name}"
                full = os.path.join(dirpath, name)
                if excluded(name, rel, patterns):
                    continue
                try:
                    st = os.lstat(full)
                except OSError as exc:
                    result.problems.append(f"Can't read {full}: {exc.strerror}")
                    continue
                if stat.S_ISLNK(st.st_mode):
                    result.warnings.append(f"Skipped symbolic link: {full}")
                    continue
                if not stat.S_ISREG(st.st_mode):
                    result.warnings.append(f"Skipped special file: {full}")
                    continue
                result.files.append(SourceFile(source, rel, full, st.st_size, st.st_mtime_ns, st.st_mode))

    check_names(result)
    return result


def check_names(result: ScanResult) -> None:
    """Flag names that break on SMB/Nexis, and names that differ only by case."""
    seen: dict[str, str] = {}
    for rel in [*result.directories, *(f.relative for f in result.files)]:
        for p in name_problems(rel):
            result.warnings.append(f"{rel}: {p} (may not copy to SMB or Avid Nexis)")
        key = rel.casefold()
        if key in seen and seen[key] != rel:
            result.problems.append(
                f"'{seen[key]}' and '{rel}' differ only by upper/lowercase; "
                "most Mac and network drives can't hold both")
        seen.setdefault(key, rel)
