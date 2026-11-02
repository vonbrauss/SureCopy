"""The copy engine: read each source chunk once, write it to every destination.

Safety rules this module keeps:
- Never overwrite or delete a user's file. Work happens in hidden .partial files,
  which are renamed into place only after the file passes its verify level.
- Progress is checkpointed (fsync, then a SQLite row) so a stopped or crashed
  job resumes where it left off.
- A slow, failed or disconnected destination never stops the others.
"""

from __future__ import annotations

import errno
import os
import queue
import shutil
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import xxhash

from . import fsio, mirror
from .models import ExistingPolicy, FileStatus, JobStatus, Progress, VerifyLevel
from .scan import PARTIAL_SUFFIX
from .store import DestRow, FileRow, Store

HASH_NAME = "xxh128"
CHECKPOINT_BYTES = 64 * 1024 * 1024
QUEUE_DEPTH = 8                  # chunks buffered per destination (8 x 8 MiB = 64 MiB)
PROGRESS_INTERVAL = 0.2
# Every file is fsynced before it's marked done. Files this big also get F_FULLFSYNC (flush the
# drive's own cache). Doing that for every small document makes big folders crawl on spinning disks.
FULL_SYNC_BYTES = 64 * 1024 * 1024

DONE_STATES = (FileStatus.COPIED, FileStatus.UPDATED, FileStatus.SKIPPED, FileStatus.KEPT_NEWER,
               FileStatus.CONFLICT)
COMPLETE_STATES = (FileStatus.COPIED, FileStatus.UPDATED, FileStatus.SKIPPED, FileStatus.KEPT_NEWER)


def new_hasher():
    return xxhash.xxh3_128()


def partial_path(final: str) -> str:
    return fsio.temp_name(final, PARTIAL_SUFFIX)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n} B"


class Controls:
    """Shared with the GUI. pause() keeps partial files; cancel() cleans them up."""

    def __init__(self):
        self._pause = threading.Event()
        self._cancel = threading.Event()

    def pause(self):
        self._pause.set()

    def cancel(self):
        self._cancel.set()

    def reset(self):
        self._pause.clear()
        self._cancel.clear()

    @property
    def stopping(self) -> bool:
        return self._pause.is_set() or self._cancel.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


# A test hook: called before every destination write. May raise OSError to simulate failure.
FaultHook = Callable[[str, str, int], None]


class DestinationGone(Exception):
    pass


@dataclass
class Target:
    dest: DestRow
    final: str
    partial: str
    offset: int                  # bytes already trusted from an earlier pass
    replace: tuple[int, int] | None = None   # (size, mtime_ns) of the older file being updated
    status: FileStatus = FileStatus.PARTIAL
    message: str = ""
    written: int = 0             # high-water mark in this pass
    failed: bool = False
    closed: bool = False         # writer thread has exited; stop sending to it
    started: float = 0.0         # when this destination's writer began (for speed history)
    start_offset: int = 0
    gone: bool = False           # whole destination unusable for the rest of the job
    dest_hash: str | None = None
    inbox: queue.Queue = field(default_factory=lambda: queue.Queue(QUEUE_DEPTH))


class Engine:
    def __init__(self, store: Store, controls: Controls | None = None,
                 on_progress: Callable[[Progress], None] | None = None,
                 fault: FaultHook | None = None, checkpoint_bytes: int = CHECKPOINT_BYTES):
        self.store = store
        self.controls = controls or Controls()
        self.on_progress = on_progress
        self.fault = fault
        self.checkpoint_bytes = checkpoint_bytes
        self._last_emit = 0.0

    # ---- public -----------------------------------------------------------

    def run(self, job_id: int) -> JobStatus:
        store = self.store
        store.set_job_status(job_id, JobStatus.RUNNING)
        spec = store.spec(job_id)
        files = store.files(job_id)
        chunk = max(1, spec.chunk_mib) * 1024 * 1024
        store.log(job_id, "info", f"Started: {len(files)} files to {len(spec.destinations)} destinations")
        self.policy = spec.on_existing
        self.mirror_waiting = False

        active = self._preflight(job_id, spec.sources, files)
        self._job_id = job_id
        self._files = files
        self._done_files = 0
        self._total_bytes = sum(x.size for x in files)
        self._perf: dict[int, list] = {}          # dest_id -> [bytes, files, seconds] this pass
        self._live: dict[int, Target] = {}
        self._perf_lock = threading.Lock()

        for f in files:
            if self.controls.stopping or not active:
                break
            self._copy_file(job_id, f, active, chunk)
            active = [d for d in active if d.state not in ("disconnected", "failed")]
            self._done_files += 1

        if spec.mirror and not self.controls.stopping:
            self._mirror(job_id, spec, files)
        if any(b or n for b, n, _ in self._perf.values()):
            # Tag the record with the two drives involved, so future copies between them can be estimated.
            from .drives import sources_key, volume_for
            src = sources_key(spec.sources)
            for d in store.dests(job_id):
                nbytes, nfiles, secs = self._perf.get(d.id, (0, 0, 0.0))
                if nbytes or nfiles:
                    vol = volume_for(d.root)
                    store.add_perf(job_id, d.id, nbytes, nfiles, secs, src, vol.key if vol else None,
                                   d.verify is VerifyLevel.FULL)
        status = self._finish(job_id, files)
        store.log(job_id, "info", f"Finished pass: {status.value}")
        return status

    # ---- before copying ------------------------------------------------------

    def _preflight(self, job_id: int, sources: list[str], files: list[FileRow]) -> list[DestRow]:
        store = self.store
        active = []
        real_sources = [os.path.realpath(s) for s in sources]
        for d in store.dests(job_id):
            if not fsio.volume_present(d.root):
                self._dest_problem(job_id, d, "disconnected", f"Not connected: {d.root}")
                continue
            root = os.path.realpath(d.root)
            if any(root == s or root.startswith(s + os.sep) for s in real_sources):
                self._dest_problem(job_id, d, "failed", "Destination is inside a source folder")
                continue
            if any(os.path.join(root, os.path.basename(s)) == s for s in real_sources):
                self._dest_problem(job_id, d, "failed", "A source already lives at this destination")
                continue
            needed = 0
            copies = {c.file_id: c for c in store.all_copies(job_id) if c.dest_id == d.id}
            for f in files:
                c = copies[f.id]
                if c.status in (FileStatus.PENDING, FileStatus.PARTIAL, FileStatus.FAILED):
                    if c.committed == 0 and self._already_there(d.root, f):
                        continue   # will be skipped, so it needs no space
                    needed += max(0, f.size - c.committed)
            free = shutil.disk_usage(d.root).free
            if needed > free:
                self._dest_problem(job_id, d, "failed",
                                   f"Not enough space: needs {human(needed)}, {human(free)} free")
                continue
            try:
                for rel in store.dirs(job_id):
                    os.makedirs(os.path.join(d.root, rel), exist_ok=True)
            except OSError as exc:
                self._dest_problem(job_id, d, "failed", f"Can't create folders: {exc.strerror}")
                continue
            store.set_dest_state(d.id, "copying")
            d.state = "copying"
            active.append(d)
        return active

    @staticmethod
    def _clear_temps(final: str) -> None:
        for path in (partial_path(final), fsio.temp_name(final, ".surecopy-old")):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                pass   # unreadable share etc.; harmless, it's only our own temp file

    @staticmethod
    def _already_there(root: str, f: FileRow) -> bool:
        try:
            st = os.stat(os.path.join(root, f.relative))
        except OSError:
            return False
        return st.st_size == f.size and fsio.same_mtime(st.st_mtime_ns, f.mtime_ns)

    def _dest_problem(self, job_id: int, d: DestRow, state: str, message: str) -> None:
        d.state, d.message = state, message
        self.store.set_dest_state(d.id, state, message)
        self.store.log(job_id, "error", f"{d.root}: {message}")

    # ---- one file -------------------------------------------------------------

    def _copy_file(self, job_id: int, f: FileRow, active: list[DestRow], chunk: int) -> None:
        store = self.store
        try:
            st = os.stat(f.abs_path)
        except OSError as exc:
            for d in active:
                store.finish_copy(f.id, d.id, FileStatus.FAILED, message=f"Source unreadable: {exc.strerror}")
            store.log(job_id, "error", f"{f.relative}: source unreadable ({exc.strerror})")
            return
        if st.st_size != f.size or st.st_mtime_ns != f.mtime_ns:
            store.log(job_id, "warn", f"{f.relative}: source changed since the job was planned; starting it over")
            store.reset_file(f.id, st.st_size, st.st_mtime_ns)
            f.size, f.mtime_ns, f.source_hash = st.st_size, st.st_mtime_ns, None

        copies = store.copies(f.id)
        targets: list[Target] = []
        for d in active:
            c = copies[d.id]
            if c.status in DONE_STATES:
                continue
            final = os.path.join(d.root, f.relative)
            part = partial_path(final)
            try:
                action, final, replace, message = self._decide(f, final, store.stale_sig(f.id, d.id))
                if action != "copy":
                    # Nothing to copy. Clear SureCopy's own leftovers from an interrupted earlier pass
                    # (for example a crash right after a file got its final name).
                    self._clear_temps(os.path.join(d.root, f.relative))
                    if final != os.path.join(d.root, f.relative):
                        self._clear_temps(final)
                    store.finish_copy(f.id, d.id, action, committed=f.size if action is FileStatus.SKIPPED else None,
                                      message=message, dest_path=final)
                    if action is FileStatus.CONFLICT:
                        store.log(job_id, "warn", f"{d.root}: {f.relative}: {message}")
                    continue
                part = partial_path(final)
                offset = 0
                if c.status == FileStatus.PARTIAL and os.path.exists(part):
                    offset = min(c.committed, os.path.getsize(part), f.size)
                targets.append(Target(d, final, part, offset, replace=replace))
            except OSError as exc:
                self._target_error(job_id, f, Target(d, final, part, 0), exc)

        if not targets:
            return
        self._transfer(job_id, f, targets, chunk)

    def _decide(self, f: FileRow, final: str, stale: tuple[int, int] | None = None):
        """Returns (action, final_path, replace_signature, message). action is "copy" or a FileStatus."""
        if not os.path.lexists(final):
            return "copy", final, None, ""
        dst = os.stat(final)
        if dst.st_size == f.size and fsio.same_mtime(dst.st_mtime_ns, f.mtime_ns):
            return FileStatus.SKIPPED, final, None, "Already at destination (same size and date)"
        if stale and (dst.st_size, dst.st_mtime_ns) == stale:
            # It's the older version this job copied a moment ago; the source changed since. Replace it.
            return "copy", final, (dst.st_size, dst.st_mtime_ns), ""
        if self.policy is ExistingPolicy.NEVER:
            return FileStatus.CONFLICT, final, None, "A different file with this name is already there; not overwritten"
        if self.policy is ExistingPolicy.UPDATE:
            if f.mtime_ns > dst.st_mtime_ns and not fsio.same_mtime(f.mtime_ns, dst.st_mtime_ns):
                return "copy", final, (dst.st_size, dst.st_mtime_ns), ""
            if dst.st_mtime_ns > f.mtime_ns and not fsio.same_mtime(f.mtime_ns, dst.st_mtime_ns):
                return FileStatus.KEPT_NEWER, final, None, "The destination's copy is newer; left as is"
            return FileStatus.CONFLICT, final, None, "Same date but a different size; not replaced"
        # KEEP_BOTH: use the first free "name (n).ext", or skip if a matching copy is already there.
        stem, ext = os.path.splitext(final)
        for n in range(2, 10_000):
            candidate = f"{stem} ({n}){ext}"
            if not os.path.lexists(candidate):
                return "copy", candidate, None, ""
            st = os.stat(candidate)
            if st.st_size == f.size and fsio.same_mtime(st.st_mtime_ns, f.mtime_ns):
                return FileStatus.SKIPPED, candidate, None, f"Already kept as {os.path.basename(candidate)}"
        return FileStatus.CONFLICT, final, None, "Too many kept versions"

    def _transfer(self, job_id: int, f: FileRow, targets: list[Target], chunk: int) -> None:
        store = self.store
        need_hash = any(t.dest.verify.hashes_source for t in targets)
        start = 0 if need_hash else min(t.offset for t in targets)
        hasher = new_hasher() if need_hash else None

        threads = [threading.Thread(target=self._writer, args=(job_id, f, t), daemon=True) for t in targets]
        for th in threads:
            th.start()

        outcome = "done"
        try:
            fd = os.open(f.abs_path, os.O_RDONLY)
        except OSError as exc:
            outcome = f"Source unreadable: {exc.strerror}"
            fd = None
        if fd is not None:
            fsio.no_cache(fd)
            try:
                pos = start
                while pos < f.size:
                    if self.controls.stopping:
                        outcome = "stop"
                        break
                    data = os.pread(fd, min(chunk, f.size - pos), pos)
                    if not data:
                        outcome = "Source ended early (file shrank during copy)"
                        break
                    if hasher:
                        hasher.update(data)
                    for t in targets:
                        self._send(t, ("chunk", pos, data))
                    pos += len(data)
                    self._emit(job_id, f, targets)
                    if all(t.failed for t in targets):
                        outcome = "Every destination failed for this file"
                        break
                st = os.fstat(fd)
                if outcome == "done" and (st.st_size != f.size or st.st_mtime_ns != f.mtime_ns):
                    outcome = "Source changed during the copy"
            except OSError as exc:
                outcome = f"Source read error: {exc.strerror}"
            finally:
                os.close(fd)

        digest = hasher.hexdigest() if hasher and outcome == "done" else None
        if digest:
            store.set_source_hash(f.id, digest)
            f.source_hash = digest
        msg = ("done", digest, None) if outcome == "done" else ("stop", outcome, None)
        for t in targets:
            self._send(t, msg)
        for th in threads:
            th.join()
        if outcome not in ("done", "stop"):
            store.log(job_id, "error", f"{f.relative}: {outcome}")
        self._emit(job_id, f, targets, force=True)

    @staticmethod
    def _send(t: Target, msg) -> None:
        while not (t.failed or t.closed):
            try:
                t.inbox.put(msg, timeout=0.25)
                return
            except queue.Full:
                continue

    # ---- one destination of one file (runs in its own thread) -------------------

    def _writer(self, job_id: int, f: FileRow, t: Target) -> None:
        store = self.store
        full = t.dest.verify is VerifyLevel.FULL
        fd = rfd = None
        t.started = time.monotonic()
        t.start_offset = t.offset
        with self._perf_lock:
            self._live[t.dest.id] = t
        try:
            fd = os.open(t.partial, os.O_RDWR | os.O_CREAT, 0o644)
            if os.fstat(fd).st_size != t.offset:
                os.ftruncate(fd, t.offset)
            if full and t.offset:
                rfd = os.open(t.partial, os.O_RDONLY)
                fsio.no_cache(rfd)
            committed = t.offset
            t.written = t.offset
            last_ckpt = committed
            while True:
                kind, a, data = t.inbox.get()
                if kind == "chunk" and self.controls.stopping:
                    kind, a = "stop", "stop"
                if kind == "chunk":
                    pos = a
                    end = pos + len(data)
                    if pos < t.offset:
                        # Already copied in an earlier pass. Full verify re-checks those bytes.
                        have = min(end, t.offset) - pos
                        if rfd is not None and os.pread(rfd, have, pos) != data[:have]:
                            store.log(job_id, "warn", f"{t.dest.root}: {f.relative} earlier partial data "
                                      f"didn't match near {human(pos)}; rewriting from there")
                            os.ftruncate(fd, pos)
                            t.offset = committed = last_ckpt = pos
                        elif end <= t.offset:
                            t.written = end
                            continue
                    skip = max(0, t.offset - pos)
                    if self.fault:
                        self.fault(t.dest.root, f.relative, pos)
                    view = memoryview(data)[skip:]
                    off = pos + skip
                    while view:
                        n = os.pwrite(fd, view, off)
                        view, off = view[n:], off + n
                    t.written = committed = end
                    if committed - last_ckpt >= self.checkpoint_bytes:
                        fsio.flush(fd)
                        store.checkpoint(f.id, t.dest.id, committed)
                        last_ckpt = committed
                elif kind == "stop":
                    fsio.flush(fd)
                    if a == "stop":
                        store.checkpoint(f.id, t.dest.id, committed)
                    else:
                        store.finish_copy(f.id, t.dest.id, FileStatus.FAILED, committed=0, message=a)
                        os.ftruncate(fd, 0)
                    return
                else:  # done
                    self._complete(job_id, f, t, fd, a)
                    return
        except OSError as exc:
            self._target_error(job_id, f, t, exc)
        finally:
            t.closed = True
            # Only time spent moving this file's data counts toward the speed comparison.
            with self._perf_lock:
                rec = self._perf.setdefault(t.dest.id, [0, 0, 0.0])
                rec[0] += max(0, t.written - t.start_offset)
                rec[2] += time.monotonic() - t.started
                if self._live.get(t.dest.id) is t:
                    del self._live[t.dest.id]
            for x in (fd, rfd):
                if x is not None:
                    try:
                        os.close(x)
                    except OSError:
                        pass

    def _complete(self, job_id: int, f: FileRow, t: Target, fd: int, source_hash: str | None) -> None:
        store = self.store
        fsio.flush(fd, full=f.size >= FULL_SYNC_BYTES)
        size = os.fstat(fd).st_size
        if size != f.size:
            raise OSError(errno.EIO, f"Size mismatch after copy: {size} vs {f.size} bytes")
        level = t.dest.verify
        dest_hash = None
        if level is VerifyLevel.FULL:
            dest_hash = self._readback(t.partial)
            if dest_hash != source_hash:
                os.ftruncate(fd, 0)
                store.finish_copy(f.id, t.dest.id, FileStatus.FAILED, committed=0,
                                  message="Readback didn't match the source; will copy again on retry")
                store.log(job_id, "error", f"{t.dest.root}: {f.relative} readback mismatch")
                t.failed = True
                return
        os.utime(t.partial, ns=(f.mtime_ns, f.mtime_ns))
        try:
            os.chmod(t.partial, f.mode & 0o777)
        except OSError:
            pass  # many network shares ignore permissions
        try:
            if t.replace:
                fsio.replace_into(t.partial, t.final, t.replace)
            else:
                fsio.publish(t.partial, t.final)
        except FileExistsError:
            os.unlink(t.partial)
            store.finish_copy(f.id, t.dest.id, FileStatus.CONFLICT,
                              message="The destination file changed during the copy; left as is")
            return
        fsio.flush_dir(os.path.dirname(t.final))
        self._clear_temps(t.final)   # the .surecopy-old aside from an interrupted replace, if any
        with self._perf_lock:
            self._perf.setdefault(t.dest.id, [0, 0, 0.0])[1] += 1
        store.finish_copy(f.id, t.dest.id, FileStatus.UPDATED if t.replace else FileStatus.COPIED,
                          committed=f.size, verified_level=level.value, dest_hash=dest_hash, dest_path=t.final,
                          message="Replaced an older version" if t.replace else "")

    @staticmethod
    def _readback(path: str) -> str:
        h = new_hasher()
        fd = os.open(path, os.O_RDONLY)
        try:
            fsio.no_cache(fd)
            while data := os.read(fd, 8 * 1024 * 1024):
                h.update(data)
        finally:
            os.close(fd)
        return h.hexdigest()

    def _target_error(self, job_id: int, f: FileRow, t: Target, exc: OSError) -> None:
        store = self.store
        t.failed = True
        reason = exc.strerror or str(exc)
        if not fsio.volume_present(t.dest.root):
            self._dest_problem(job_id, t.dest, "disconnected", f"Disconnected during {f.relative}")
            # Keep the checkpoint so the next pass resumes here.
            return
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            self._dest_problem(job_id, t.dest, "failed", "Destination is full")
            return
        store.finish_copy(f.id, t.dest.id, FileStatus.FAILED, message=reason)
        store.log(job_id, "error", f"{t.dest.root}: {f.relative} failed ({reason})")

    # ---- progress and wrap-up -----------------------------------------------------

    def _emit(self, job_id: int, f: FileRow, targets: list[Target], force: bool = False) -> None:
        if not self.on_progress:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < PROGRESS_INTERVAL:
            return
        self._last_emit = now
        per_dest = self.store.dest_bytes(job_id)
        current = self.store.copies(f.id)
        for t in targets:   # swap the saved checkpoint for live progress on the file in flight
            c = current.get(t.dest.id)
            if c and c.status not in COMPLETE_STATES:
                per_dest[t.dest.id] = per_dest.get(t.dest.id, 0) - c.committed + t.written
        roots = {d.id: d.root for d in self.store.dests(job_id)}
        now = time.monotonic()
        perf = {}
        with self._perf_lock:
            for dest_id, root in roots.items():
                b, n, s = self._perf.get(dest_id, [0, 0, 0.0])
                live = self._live.get(dest_id)
                if live is not None and not live.closed:
                    b += max(0, live.written - live.start_offset)
                    s += now - live.started
                perf[root] = (b, n, s)
        self.on_progress(Progress(job_id, f.relative, None, {roots[k]: v for k, v in per_dest.items()},
                                  self._total_bytes, self._done_files, len(self._files), perf=perf))

    def _finish(self, job_id: int, files: list[FileRow]) -> JobStatus:
        store = self.store
        copies = store.all_copies(job_id)
        if self.controls.cancelled:
            self._cleanup_partials(job_id, files, copies)
            status = JobStatus.CANCELLED
        elif self.controls.stopping:
            status = JobStatus.PAUSED
        elif any(c.status in (FileStatus.PENDING, FileStatus.PARTIAL) for c in copies):
            status = JobStatus.NEEDS_RESUME
        elif self.mirror_waiting:
            status = JobStatus.CONFIRM_MIRROR
        elif any(c.status in (FileStatus.FAILED, FileStatus.CONFLICT) for c in copies):
            status = JobStatus.DONE_WITH_ISSUES
        else:
            status = JobStatus.DONE
        for d in store.dests(job_id):
            if d.state == "copying":
                store.set_dest_state(d.id, "done" if status in (JobStatus.DONE, JobStatus.DONE_WITH_ISSUES) else "ready")
        store.set_job_status(job_id, status)
        return status

    def _mirror(self, job_id: int, spec, files: list[FileRow]) -> None:
        """Approved: carry out the approved list. Not approved: make a preview and wait."""
        store = self.store
        approved = store.mirror_approved(job_id)
        for d in store.dests(job_id):
            if not mirror.ready_for_mirror(store, job_id, d):
                store.log(job_id, "warn", f"{d.root}: mirror skipped because the copy isn't complete")
                continue
            if approved:
                mirror.execute(store, job_id, spec, d, files)
            else:
                p = mirror.plan(store, job_id, spec, d, files)
                if p.files or p.folders:
                    self.mirror_waiting = True
        store.set_mirror_approved(job_id, False)

    def _cleanup_partials(self, job_id: int, files: list[FileRow], copies) -> None:
        """Cancel removes SureCopy's own .partial files. Finished files are never touched."""
        by_id = {f.id: f for f in files}
        roots = {d.id: d.root for d in self.store.dests(job_id)}
        for c in copies:
            if c.status in (FileStatus.PARTIAL, FileStatus.PENDING, FileStatus.FAILED):
                part = partial_path(os.path.join(roots[c.dest_id], by_id[c.file_id].relative))
                try:
                    os.unlink(part)
                except OSError:
                    pass
                self.store.finish_copy(c.file_id, c.dest_id, FileStatus.PENDING, committed=0)
