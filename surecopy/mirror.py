"""Mirror mode: remove destination files that are no longer in the source.

Strict by design:
- Nothing is removed until the user has seen the exact list and approved it. Every run
  makes a fresh preview; approval covers only the files on that preview.
- Only runs on a destination whose copy finished with no failed or unfinished files.
- Only looks inside the folders this job copies (Destination/<source folder>), never the
  rest of the drive. Single-file sources are never mirrored.
- Excluded files (caches, .DS_Store, etc.) are protected, like rsync without --delete-excluded.
- By default files are moved into "_SureCopy Removed/<date> <job>" on the same drive,
  not deleted. Permanent delete is a separate, explicit setting.
- Before each removal the file is checked again; if it changed since the preview, it stays.
- Danger warnings: the source folder is empty, or the removal is a large share of the backup.
"""

from __future__ import annotations

import os
import time
import unicodedata
from dataclasses import dataclass, field

from . import fsio
from .models import FileStatus, JobSpec
from .scan import DEFAULT_EXCLUDES, excluded
from .store import DestRow, FileRow, Store

REMOVED_FOLDER = "_SureCopy Removed"
LARGE_SHARE = 0.25          # warn when removing more than a quarter of a backup's files...
LARGE_MIN_FILES = 20        # ...and at least this many


def key(rel: str) -> str:
    """Compare names the way Mac and SMB drives do: Unicode-normalized, case-insensitive."""
    return unicodedata.normalize("NFC", rel).casefold()


@dataclass
class DestPreview:
    dest_id: int
    root: str
    files: list[tuple[str, int]] = field(default_factory=list)   # (relative, size)
    folders: list[str] = field(default_factory=list)
    existing_files: int = 0
    dangers: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(s for _, s in self.files)


def mirror_roots(spec: JobSpec) -> list[str]:
    return [os.path.basename(os.path.abspath(s.rstrip("/"))) for s in spec.sources if os.path.isdir(s)]


def plan(store: Store, job_id: int, spec: JobSpec, d: DestRow, files: list[FileRow]) -> DestPreview:
    patterns = DEFAULT_EXCLUDES + [f"{REMOVED_FOLDER}"] + list(spec.excludes)
    keep = {key(f.relative) for f in files}
    for c in store.all_copies(job_id):
        if c.dest_id == d.id and c.dest_path:   # "keep both" copies like "name (2).ext"
            keep.add(key(os.path.relpath(c.dest_path, d.root).replace(os.sep, "/")))
    keep_dirs = {key(x) for x in store.dirs(job_id)}
    preview = DestPreview(d.id, d.root)
    rows: list[tuple[str, bool, int, int]] = []

    for top in mirror_roots(spec):
        base = os.path.join(d.root, top)
        if not os.path.isdir(base):
            continue
        source_count = sum(1 for f in files if f.relative.startswith(top + "/"))
        dest_count = 0
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, d.root).replace(os.sep, "/")
            kept_dirs = []
            for name in sorted(dirnames):
                rel = f"{rel_dir}/{name}"
                if excluded(name, rel, patterns):
                    continue
                if os.path.islink(os.path.join(dirpath, name)):
                    filenames.append(name)   # treat a linked folder as one item, never follow it
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs
            for name in sorted(filenames):
                rel = f"{rel_dir}/{name}"
                if excluded(name, rel, patterns):
                    continue
                dest_count += 1
                if key(rel) in keep:
                    continue
                st = os.lstat(os.path.join(dirpath, name))
                rows.append((rel, False, st.st_size, st.st_mtime_ns))
                preview.files.append((rel, st.st_size))
            if rel_dir != top and key(rel_dir) not in keep_dirs:
                st = os.lstat(dirpath)
                rows.append((rel_dir, True, 0, st.st_mtime_ns))
                preview.folders.append(rel_dir)
        preview.existing_files += dest_count
        if source_count == 0 and dest_count:
            preview.dangers.append(
                f"The source folder '{top}' is empty, so mirroring would remove all {dest_count} files "
                f"in {base}. Check that the right source drive is connected.")
    n = len(preview.files)
    if n >= LARGE_MIN_FILES and preview.existing_files and n / preview.existing_files > LARGE_SHARE:
        preview.dangers.append(f"This removes {n} of {preview.existing_files} files "
                               f"({100 * n // preview.existing_files}%) from this backup.")
    store.replace_removal_plan(job_id, d.id, rows)
    return preview


def ready_for_mirror(store: Store, job_id: int, d: DestRow) -> bool:
    if d.state in ("disconnected", "failed"):
        return False
    return not any(c.dest_id == d.id and c.status in (FileStatus.PENDING, FileStatus.PARTIAL, FileStatus.FAILED)
                   for c in store.all_copies(job_id))


def execute(store: Store, job_id: int, spec: JobSpec, d: DestRow, files: list[FileRow]) -> tuple[int, int]:
    """Carry out the approved plan for one destination. Returns (removed, kept)."""
    keep = {key(f.relative) for f in files}
    stamp = time.strftime("%Y-%m-%d %H.%M.%S")
    bin_root = os.path.join(d.root, REMOVED_FOLDER, f"{stamp} {spec.name}".replace("/", "-"))
    removed = kept = 0
    rows = [r for r in store.removals(job_id, "planned") if r[1] == d.id]
    for rid, _, rel, is_dir, size, mtime, _, _ in [r for r in rows if not r[3]]:
        path = os.path.join(d.root, rel)
        try:
            if key(rel) in keep:
                store.set_removal(rid, "kept", "Now in the source")
                kept += 1
                continue
            if not os.path.lexists(path):
                store.set_removal(rid, "kept", "Already gone")
                kept += 1
                continue
            st = os.lstat(path)
            if st.st_size != size or st.st_mtime_ns != mtime:
                store.set_removal(rid, "kept", "Changed since the preview; left in place")
                kept += 1
                continue
            if spec.mirror_delete:
                os.unlink(path)
                store.set_removal(rid, "removed", "Deleted permanently")
            else:
                target = os.path.join(bin_root, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                os.rename(path, target)
                store.set_removal(rid, "removed", f"Moved to {os.path.relpath(target, d.root)}")
            removed += 1
        except OSError as exc:
            store.set_removal(rid, "failed", exc.strerror or str(exc))
    # Folders last, deepest first, and only if they ended up empty.
    for rid, _, rel, *_ in sorted([r for r in rows if r[3]], key=lambda r: -r[2].count("/")):
        path = os.path.join(d.root, rel)
        try:
            os.rmdir(path)
            store.set_removal(rid, "removed", "Empty folder removed")
        except OSError:
            store.set_removal(rid, "kept", "Folder not empty or already gone")
    fsio.flush_dir(d.root)
    store.log(job_id, "info", f"{d.root}: mirror removed {removed} files, kept {kept}")
    return removed, kept


def preview_text(previews: list[DestPreview], spec: JobSpec) -> str:
    """The strict warning shown before approval."""
    how = ("PERMANENTLY DELETED" if spec.mirror_delete else
           f"moved to '{REMOVED_FOLDER}' on each drive (delete that folder later to free space)")
    out = ["MIRROR MODE WARNING",
           "These files are on the destination but no longer in the source.",
           f"If you approve, they will be {how}.", ""]
    for p in previews:
        out.append(f"{p.root}: {len(p.files)} files, {len(p.folders)} folders")
        for danger in p.dangers:
            out.append(f"  DANGER: {danger}")
        for rel, size in p.files[:200]:
            out.append(f"  - {rel}  ({size:,} bytes)")
        if len(p.files) > 200:
            out.append(f"  ...and {len(p.files) - 200} more")
        out.append("")
    return "\n".join(out)
