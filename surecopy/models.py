"""Plain data types shared by the engine, the job store and (later) the GUI."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class VerifyLevel(str, enum.Enum):
    """How hard SureCopy checks each copy. FAST is the default."""

    FAST = "fast"            # size check after copy
    SOURCE_HASH = "source"   # size check + xxHash of the source, recorded for later
    FULL = "full"            # read every destination file back and compare hashes

    @property
    def hashes_source(self) -> bool:
        return self is not VerifyLevel.FAST

    @property
    def label(self) -> str:
        return {
            VerifyLevel.FAST: "Fast (size check)",
            VerifyLevel.SOURCE_HASH: "Source hash (no readback)",
            VerifyLevel.FULL: "Full verify (readback)",
        }[self]


class ExistingPolicy(str, enum.Enum):
    """What to do when a different file with the same name is already at the destination."""

    UPDATE = "update"          # replace it if the source is newer (rsync --update). Default.
    NEVER = "never"            # never overwrite; flag it as a conflict
    KEEP_BOTH = "keep_both"    # copy the source alongside as "name (2).ext"

    @property
    def label(self) -> str:
        return {
            ExistingPolicy.UPDATE: "Update if the source is newer",
            ExistingPolicy.NEVER: "Never overwrite",
            ExistingPolicy.KEEP_BOTH: "Keep both",
        }[self]


class FileStatus(str, enum.Enum):
    PENDING = "pending"
    PARTIAL = "partial"        # some bytes written, checkpoint saved
    COPIED = "copied"          # new file published and passed its verify level
    UPDATED = "updated"        # replaced an older version at the destination
    SKIPPED = "skipped"        # identical file already at the destination
    KEPT_NEWER = "kept_newer"  # the destination's copy is newer; left as is
    CONFLICT = "conflict"      # different file there and the policy says don't replace
    FAILED = "failed"


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    NEEDS_RESUME = "needs_resume"  # finished a pass but something is incomplete
    DONE = "done"                  # every file on every destination copied or skipped
    DONE_WITH_ISSUES = "done_with_issues"  # conflicts or failures remain
    CONFIRM_MIRROR = "confirm_mirror"      # copy finished; mirror removals wait for the user's OK
    CANCELLED = "cancelled"


class DestState(str, enum.Enum):
    READY = "ready"
    COPYING = "copying"
    DISCONNECTED = "disconnected"
    FAILED = "failed"
    DONE = "done"


@dataclass(frozen=True)
class SourceFile:
    source_root: str   # the folder or file the user added
    relative: str      # path inside the job's destination layout, "/" separated
    abs_path: str
    size: int
    mtime_ns: int
    mode: int


@dataclass
class Destination:
    root: str
    verify: VerifyLevel = VerifyLevel.FAST


@dataclass
class JobSpec:
    name: str
    sources: list[str]
    destinations: list[Destination]
    excludes: list[str] = field(default_factory=list)
    chunk_mib: int = 8
    on_existing: ExistingPolicy = ExistingPolicy.UPDATE
    mirror: bool = False            # remove destination files that are no longer in the source
    mirror_delete: bool = False     # False: move them to "_SureCopy Removed". True: delete permanently


@dataclass(frozen=True)
class Progress:
    """A snapshot the GUI can draw. Emitted often; keep it cheap."""

    job_id: int
    file: str | None
    dest_root: str | None
    dest_bytes: dict[str, int]       # dest root -> bytes done for the job
    total_bytes: int
    files_done: int
    files_total: int
    message: str = ""
    # dest root -> (bytes moved, files finished, seconds spent moving them) in this pass, live
    perf: dict | None = None
