"""High-level actions the CLI and GUI call: plan, queue, resume, verify later."""

from __future__ import annotations

import os
import threading
from typing import Callable

from . import fsio, mirror
from .engine import Controls, Engine, new_hasher
from .models import FileStatus, JobSpec, JobStatus, Progress, VerifyLevel
from .scan import ScanResult, scan
from .store import Store


def check_job(spec: JobSpec) -> ScanResult:
    """Scan the sources and find anything that blocks the job. Safe to run on a worker thread."""
    result = scan(spec.sources, spec.excludes)
    if not spec.destinations:
        result.problems.append("Add at least one destination")
    roots = [os.path.realpath(d.root).casefold() for d in spec.destinations]
    if len(set(roots)) != len(roots):
        result.problems.append("The same destination is listed twice")
    return result


def plan_job(store: Store, spec: JobSpec, result: ScanResult | None = None) -> tuple[int | None, ScanResult]:
    """Scan (unless already scanned) and queue the job. Returns (None, result) if something blocks it."""
    result = result or check_job(spec)
    if result.problems:
        return None, result
    job_id = store.create_job(spec, result.files, result.directories, result.warnings)
    return job_id, result


def resume_job(store: Store, job_id: int) -> None:
    """Put a paused, interrupted or partly failed job back in the queue."""
    store.retry_failed(job_id)
    store.set_job_status(job_id, JobStatus.QUEUED)


def mirror_preview(store: Store, job_id: int) -> list[mirror.DestPreview]:
    """Fresh list of what mirror mode would remove. This exact list is what approval covers."""
    spec = store.spec(job_id)
    files = store.files(job_id)
    previews = []
    for d in store.dests(job_id):
        if fsio.volume_present(d.root) and mirror.ready_for_mirror(store, job_id, d):
            p = mirror.plan(store, job_id, spec, d, files)
            if p.files or p.folders:
                previews.append(p)
    return previews


def approve_mirror(store: Store, job_id: int) -> None:
    """The user saw the preview and said yes. The next run removes only those files."""
    store.set_mirror_approved(job_id, True)
    store.set_job_status(job_id, JobStatus.QUEUED)
    store.log(job_id, "info", "Mirror removals approved by the user")


def decline_mirror(store: Store, job_id: int) -> None:
    for rid, *_ in store.removals(job_id, "planned"):
        store.set_removal(rid, "kept", "Declined by the user")
    store.set_mirror_approved(job_id, False)
    store.set_job_status(job_id, JobStatus.DONE)
    store.log(job_id, "info", "Mirror removals declined; nothing removed")


class QueueRunner:
    """Runs queued jobs one after another on a background thread.

    Starting, pausing and moving to the next job all happen under one lock, so a Pause aimed at one
    job can never land on the next one, and a Start pressed just as the queue empties is never lost.
    """

    def __init__(self, store: Store, on_progress: Callable[[Progress], None] | None = None,
                 on_job_done: Callable[[int, JobStatus], None] | None = None):
        self.store = store
        self.on_progress = on_progress
        self.on_job_done = on_job_done
        self.controls = Controls()
        self.current: int | None = None
        self._thread: threading.Thread | None = None
        self._halt = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._halt.clear()
            if self._thread is not None:
                return          # the loop checks the queue again before it stops
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while True:
            with self._lock:
                job_id = None if self._halt.is_set() else self.store.next_queued()
                if job_id is None:
                    self._thread = None
                    return
                self.current = job_id
                self.controls.reset()
            status = Engine(self.store, self.controls, self.on_progress).run(job_id)
            with self._lock:
                self.current = None
            if self.on_job_done:
                self.on_job_done(job_id, status)

    def pause_current(self, job_id: int | None = None) -> bool:
        return self.stop_all(job_id)

    def cancel_current(self, job_id: int | None = None) -> bool:
        with self._lock:
            if self.current is None or (job_id is not None and self.current != job_id):
                return False
            self.controls.cancel()
            return True

    def stop_all(self, job_id: int | None = None) -> bool:
        """Pause the running job (only if it's `job_id`, when given) and don't start the next one."""
        with self._lock:
            if job_id is not None and self.current != job_id:
                return False
            self._halt.set()
            self.controls.pause()
            return True

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def wait(self, timeout: float | None = None) -> None:
        t = self._thread
        if t:
            t.join(timeout)


def verify_later(store: Store, job_id: int, on_file: Callable[[str], None] | None = None) -> dict[str, int]:
    """Read back every finished copy and compare it to the source hash.

    Never changes the user's files. A mismatch is recorded on the copy and reported.
    """
    counts = {"verified": 0, "mismatch": 0, "missing": 0, "source_changed": 0}
    dests = {d.id: d for d in store.dests(job_id)}
    for f in store.files(job_id):
        copies = [c for c in store.copies(f.id).values() if c.status in (FileStatus.COPIED, FileStatus.UPDATED, FileStatus.SKIPPED)]
        if not copies:
            continue
        if on_file:
            on_file(f.relative)
        try:
            st = os.stat(f.abs_path)
        except OSError:
            st = None
        if st is None or st.st_size != f.size or not fsio.same_mtime(st.st_mtime_ns, f.mtime_ns):
            counts["source_changed"] += len(copies)
            for c in copies:
                store.set_copy_check(f.id, c.dest_id, c.verified_level or "fast", c.dest_hash,
                                     "Verify later: source changed or missing, can't compare")
            continue
        source_hash = f.source_hash or hash_file(f.abs_path)
        if not f.source_hash:
            store.set_source_hash(f.id, source_hash)
        for c in copies:
            final = c.dest_path or os.path.join(dests[c.dest_id].root, f.relative)
            try:
                dest_hash = hash_file(final)
            except OSError:
                counts["missing"] += 1
                store.set_copy_check(f.id, c.dest_id, c.verified_level or "fast", None,
                                     "Verify later: destination file missing or unreadable")
                continue
            if dest_hash == source_hash:
                counts["verified"] += 1
                store.set_copy_check(f.id, c.dest_id, VerifyLevel.FULL.value, dest_hash, "Verified later")
            else:
                counts["mismatch"] += 1
                store.set_copy_check(f.id, c.dest_id, c.verified_level or "fast", dest_hash,
                                     "Verify later: MISMATCH, destination differs from source")
                store.log(job_id, "error", f"{final}: verify later mismatch")
    return counts


def hash_file(path: str) -> str:
    h = new_hasher()
    fd = os.open(path, os.O_RDONLY)
    try:
        fsio.no_cache(fd)
        while data := os.read(fd, 8 * 1024 * 1024):
            h.update(data)
    finally:
        os.close(fd)
    return h.hexdigest()
