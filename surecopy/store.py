"""Durable job records in SQLite. Everything needed to resume lives here."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass

from .models import Destination, ExistingPolicy, FileStatus, JobSpec, JobStatus, SourceFile, VerifyLevel

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created REAL NOT NULL,
    finished REAL,
    status TEXT NOT NULL,
    position INTEGER NOT NULL,
    spec TEXT NOT NULL,
    warnings TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    relative TEXT NOT NULL,
    abs_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    mode INTEGER NOT NULL,
    source_hash TEXT
);
CREATE INDEX IF NOT EXISTS files_job ON files(job_id);
CREATE TABLE IF NOT EXISTS dirs (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    relative TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dests (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    root TEXT NOT NULL,
    verify TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'ready',
    message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS copies (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    dest_id INTEGER NOT NULL REFERENCES dests(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    committed INTEGER NOT NULL DEFAULT 0,
    verified_level TEXT,
    dest_hash TEXT,
    message TEXT NOT NULL DEFAULT '',
    dest_path TEXT,
    stale TEXT,          -- "size:mtime_ns" of an older version SureCopy itself wrote in this job
    PRIMARY KEY (file_id, dest_id)
);
CREATE TABLE IF NOT EXISTS removals (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    dest_id INTEGER NOT NULL REFERENCES dests(id) ON DELETE CASCADE,
    relative TEXT NOT NULL,
    is_dir INTEGER NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    state TEXT NOT NULL,          -- planned, removed, kept, failed
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS saved (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    spec TEXT NOT NULL,
    created REAL NOT NULL,
    last_run REAL
);
CREATE TABLE IF NOT EXISTS perf (
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    dest_id INTEGER NOT NULL REFERENCES dests(id) ON DELETE CASCADE,
    bytes INTEGER NOT NULL DEFAULT 0,
    files INTEGER NOT NULL DEFAULT 0,
    seconds REAL NOT NULL DEFAULT 0,
    src_key TEXT,          -- drive the copy read from
    dst_key TEXT,          -- drive this destination is on
    full INTEGER NOT NULL DEFAULT 0,   -- Full verify (readback) was on
    PRIMARY KEY (job_id, dest_id)
);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    level TEXT NOT NULL,
    text TEXT NOT NULL
);
"""


def spec_to_json(spec: JobSpec) -> str:
    return json.dumps({
        "name": spec.name, "sources": spec.sources, "excludes": spec.excludes,
        "chunk_mib": spec.chunk_mib, "on_existing": spec.on_existing.value,
        "mirror": spec.mirror, "mirror_delete": spec.mirror_delete,
        "destinations": [{"root": d.root, "verify": d.verify.value} for d in spec.destinations],
    })


def spec_from_json(text: str) -> JobSpec:
    raw = json.loads(text)
    return JobSpec(raw["name"], raw["sources"],
                   [Destination(d["root"], VerifyLevel(d["verify"])) for d in raw["destinations"]],
                   raw["excludes"], raw["chunk_mib"],
                   ExistingPolicy(raw.get("on_existing", ExistingPolicy.UPDATE.value)),
                   raw.get("mirror", False), raw.get("mirror_delete", False))


def task_key(spec: JobSpec) -> str:
    """Runs with the same sources going to the same destinations belong to one copy's history.
    Name, verify levels and other settings can change between runs."""
    norm = lambda p: os.path.normpath(p).rstrip("/") or "/"
    return json.dumps([sorted(norm(s) for s in spec.sources), sorted(norm(d.root) for d in spec.destinations)])


def default_db_path() -> str:
    base = os.path.expanduser("~/Library/Application Support/SureCopy")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "surecopy.db")


@dataclass
class FileRow:
    id: int
    relative: str
    abs_path: str
    size: int
    mtime_ns: int
    mode: int
    source_hash: str | None


@dataclass
class DestRow:
    id: int
    root: str
    verify: VerifyLevel
    state: str
    message: str


@dataclass
class CopyRow:
    file_id: int
    dest_id: int
    status: FileStatus
    committed: int
    verified_level: str | None
    dest_hash: str | None
    message: str
    dest_path: str | None = None   # where the copy actually landed (differs for "keep both")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(copies)")}
        if "dest_path" not in cols:
            self.db.execute("ALTER TABLE copies ADD COLUMN dest_path TEXT")
        if "stale" not in cols:
            self.db.execute("ALTER TABLE copies ADD COLUMN stale TEXT")
        pcols = {r[1] for r in self.db.execute("PRAGMA table_info(perf)")}
        for col, ddl in (("src_key", "TEXT"), ("dst_key", "TEXT"), ("full", "INTEGER NOT NULL DEFAULT 0")):
            if col not in pcols:
                self.db.execute(f"ALTER TABLE perf ADD COLUMN {col} {ddl}")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "mirror_approved" not in cols:
            self.db.execute("ALTER TABLE jobs ADD COLUMN mirror_approved INTEGER NOT NULL DEFAULT 0")
        if "task_key" not in cols:
            self.db.execute("ALTER TABLE jobs ADD COLUMN task_key TEXT")
            self.db.execute("ALTER TABLE jobs ADD COLUMN started REAL")
            for job_id, spec in self.db.execute("SELECT id, spec FROM jobs").fetchall():
                self.db.execute("UPDATE jobs SET task_key=? WHERE id=?", (task_key(spec_from_json(spec)), job_id))

    def close(self) -> None:
        with self._lock:
            self.db.close()

    @contextmanager
    def tx(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    # ---- jobs -------------------------------------------------------------

    def create_job(self, spec: JobSpec, files: list[SourceFile], dirs: list[str], warnings: list[str]) -> int:
        spec_json = spec_to_json(spec)
        with self.tx() as db:
            pos = db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM jobs").fetchone()[0]
            job_id = db.execute(
                "INSERT INTO jobs(name, created, status, position, spec, warnings, task_key) VALUES (?,?,?,?,?,?,?)",
                (spec.name, time.time(), JobStatus.QUEUED.value, pos, spec_json, json.dumps(warnings),
                 task_key(spec)),
            ).lastrowid
            db.executemany(
                "INSERT INTO files(job_id, relative, abs_path, size, mtime_ns, mode) VALUES (?,?,?,?,?,?)",
                [(job_id, f.relative, f.abs_path, f.size, f.mtime_ns, f.mode) for f in files])
            db.executemany("INSERT INTO dirs(job_id, relative) VALUES (?,?)", [(job_id, d) for d in dirs])
            for d in spec.destinations:
                dest_id = db.execute("INSERT INTO dests(job_id, root, verify) VALUES (?,?,?)",
                                     (job_id, d.root, d.verify.value)).lastrowid
                db.execute(
                    "INSERT INTO copies(file_id, dest_id, status) SELECT id, ?, ? FROM files WHERE job_id = ?",
                    (dest_id, FileStatus.PENDING.value, job_id))
        return job_id

    def spec(self, job_id: int) -> JobSpec:
        return spec_from_json(self.q("SELECT spec FROM jobs WHERE id=?", (job_id,))[0][0])

    # ---- saved copies (favorites) ------------------------------------------

    def save_copy(self, spec: JobSpec) -> int:
        with self._lock:
            return self.db.execute("INSERT INTO saved(name, spec, created) VALUES (?,?,?)",
                                   (spec.name, spec_to_json(spec), time.time())).lastrowid

    def saved_copies(self) -> list[tuple[int, str, JobSpec, float | None]]:
        return [(i, n, spec_from_json(s), lr) for i, n, s, lr in
                self.q("SELECT id, name, spec, last_run FROM saved ORDER BY name COLLATE NOCASE")]

    def saved_spec(self, saved_id: int) -> JobSpec:
        return spec_from_json(self.q("SELECT spec FROM saved WHERE id=?", (saved_id,))[0][0])

    def touch_saved(self, saved_id: int) -> None:
        with self._lock:
            self.db.execute("UPDATE saved SET last_run=? WHERE id=?", (time.time(), saved_id))

    def delete_saved(self, saved_id: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM saved WHERE id=?", (saved_id,))

    def recent_destinations(self, limit: int = 8) -> list[str]:
        rows = self.q("SELECT root, MAX(job_id) m FROM dests GROUP BY root ORDER BY m DESC LIMIT ?", (limit,))
        return [r[0] for r in rows]

    def delete_job(self, job_id: int) -> None:
        """Remove a job's record from the list. Never touches files on any drive."""
        with self._lock:
            self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    # ---- history: every run of the same copy ---------------------------------

    def task_of(self, job_id: int) -> str:
        return self.q("SELECT task_key FROM jobs WHERE id=?", (job_id,))[0][0]

    def runs(self, key: str) -> list[tuple[int, str, JobStatus, float, float | None, float | None]]:
        """Every run of one copy, newest first: (job_id, name, status, created, started, finished)."""
        return [(i, n, JobStatus(s), c, st, f) for i, n, s, c, st, f in self.q(
            "SELECT id, name, status, created, started, finished FROM jobs WHERE task_key=? "
            "ORDER BY created DESC, id DESC", (key,))]

    def tasks(self) -> list[tuple[str, int, int]]:
        """One row per copy that has at least one run: (task_key, latest job_id, run count), newest first."""
        return self.q(
            "SELECT task_key, MAX(id), COUNT(*) FROM jobs GROUP BY task_key ORDER BY MAX(created) DESC")

    def run_summary(self, job_id: int) -> dict[str, int]:
        """File counts for one run, per destination (the largest count across destinations, so a
        47-file copy to two drives reads "47 already there", not 94), plus mirror removals."""
        out: dict[str, int] = {}
        for status, n in self.q(
                "SELECT c.status, COUNT(*) FROM copies c JOIN files f ON f.id=c.file_id WHERE f.job_id=? "
                "GROUP BY c.dest_id, c.status", (job_id,)):
            out[status] = max(out.get(status, 0), n)
        out["removed"] = self.q(
            "SELECT COALESCE(MAX(n), 0) FROM (SELECT COUNT(*) n FROM removals WHERE job_id=? AND state='removed' "
            "AND is_dir=0 GROUP BY dest_id)", (job_id,))[0][0]
        return out

    def job_times(self, job_id: int) -> tuple[float, float | None, float | None]:
        return self.q("SELECT created, started, finished FROM jobs WHERE id=?", (job_id,))[0]

    def job_info(self, job_id: int) -> tuple[str, JobStatus, float, float | None]:
        n, s, c, f = self.q("SELECT name, status, created, finished FROM jobs WHERE id=?", (job_id,))[0]
        return n, JobStatus(s), c, f

    # ---- fast summaries for the GUI -----------------------------------------

    def totals(self, job_id: int) -> tuple[int, int]:
        """(file count, total bytes)"""
        return self.q("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM files WHERE job_id=?", (job_id,))[0]

    def dest_counts(self, job_id: int) -> dict[int, dict[str, int]]:
        out: dict[int, dict[str, int]] = {}
        for dest_id, status, n in self.q(
                "SELECT c.dest_id, c.status, COUNT(*) FROM copies c JOIN files f ON f.id=c.file_id "
                "WHERE f.job_id=? GROUP BY c.dest_id, c.status", (job_id,)):
            out.setdefault(dest_id, {})[status] = n
        return out

    def dest_bytes(self, job_id: int) -> dict[int, int]:
        """Bytes finished or safely checkpointed per destination."""
        return dict(self.q(
            "SELECT c.dest_id, SUM(CASE WHEN c.status IN ('copied','updated','skipped','kept_newer') "
            "THEN f.size ELSE c.committed END) FROM copies c JOIN files f ON f.id=c.file_id "
            "WHERE f.job_id=? GROUP BY c.dest_id", (job_id,)))

    def files_finished(self, job_id: int) -> int:
        """Files that are finished (done, skipped or flagged) on every destination."""
        return self.q(
            "SELECT COUNT(*) FROM files f WHERE f.job_id=? AND NOT EXISTS (SELECT 1 FROM copies c "
            "WHERE c.file_id=f.id AND c.status IN ('pending','partial'))", (job_id,))[0][0]

    def issues(self, job_id: int, limit: int = 500) -> list[tuple[str, int, str, str]]:
        """(relative, dest_id, status, message) for failed and conflicting copies."""
        return self.q(
            "SELECT f.relative, c.dest_id, c.status, c.message FROM copies c JOIN files f ON f.id=c.file_id "
            "WHERE f.job_id=? AND (c.status IN ('failed','conflict') OR c.message LIKE '%MISMATCH%') "
            "ORDER BY f.id LIMIT ?", (job_id, limit))

    def issue_count(self, job_id: int) -> int:
        return self.q(
            "SELECT COUNT(*) FROM copies c JOIN files f ON f.id=c.file_id "
            "WHERE f.job_id=? AND (c.status IN ('failed','conflict') OR c.message LIKE '%MISMATCH%')",
            (job_id,))[0][0]

    def job_status(self, job_id: int) -> JobStatus:
        return JobStatus(self.q("SELECT status FROM jobs WHERE id=?", (job_id,))[0][0])

    def set_job_status(self, job_id: int, status: JobStatus) -> None:
        ends = (JobStatus.DONE, JobStatus.DONE_WITH_ISSUES, JobStatus.CANCELLED, JobStatus.CONFIRM_MIRROR)
        finished = time.time() if status in ends else None
        with self._lock:
            self.db.execute("UPDATE jobs SET status=?, finished=? WHERE id=?", (status.value, finished, job_id))
            if status is JobStatus.RUNNING:
                self.db.execute("UPDATE jobs SET started=COALESCE(started, ?) WHERE id=?", (time.time(), job_id))

    def jobs(self) -> list[tuple[int, str, JobStatus, int]]:
        return [(i, n, JobStatus(s), p) for i, n, s, p in
                self.q("SELECT id, name, status, position FROM jobs ORDER BY position")]

    def next_queued(self) -> int | None:
        rows = self.q("SELECT id FROM jobs WHERE status=? ORDER BY position LIMIT 1",
                      (JobStatus.QUEUED.value,))
        return rows[0][0] if rows else None

    def move_job(self, job_id: int, new_position: int) -> None:
        with self.tx() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY position")]
            ids.remove(job_id)
            ids.insert(max(0, min(new_position, len(ids))), job_id)
            db.executemany("UPDATE jobs SET position=? WHERE id=?", [(i + 1, j) for i, j in enumerate(ids)])

    def set_copy_check(self, file_id: int, dest_id: int, verified_level: str, dest_hash: str | None,
                       message: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE copies SET verified_level=?, dest_hash=?, message=? WHERE file_id=? AND dest_id=?",
                            (verified_level, dest_hash, message, file_id, dest_id))

    def warnings(self, job_id: int) -> list[str]:
        return json.loads(self.q("SELECT warnings FROM jobs WHERE id=?", (job_id,))[0][0])

    # ---- files, destinations, copies ---------------------------------------

    def files(self, job_id: int) -> list[FileRow]:
        return [FileRow(*r) for r in self.q(
            "SELECT id, relative, abs_path, size, mtime_ns, mode, source_hash FROM files "
            "WHERE job_id=? ORDER BY id", (job_id,))]

    def dirs(self, job_id: int) -> list[str]:
        return [r[0] for r in self.q("SELECT relative FROM dirs WHERE job_id=? ORDER BY rowid", (job_id,))]

    def dests(self, job_id: int) -> list[DestRow]:
        return [DestRow(i, r, VerifyLevel(v), s, m) for i, r, v, s, m in self.q(
            "SELECT id, root, verify, state, message FROM dests WHERE job_id=? ORDER BY id", (job_id,))]

    def set_dest_state(self, dest_id: int, state: str, message: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE dests SET state=?, message=? WHERE id=?", (state, message, dest_id))

    def copies(self, file_id: int) -> dict[int, CopyRow]:
        return {r[1]: CopyRow(r[0], r[1], FileStatus(r[2]), *r[3:]) for r in self.q(
            "SELECT file_id, dest_id, status, committed, verified_level, dest_hash, message, dest_path "
            "FROM copies WHERE file_id=?", (file_id,))}

    def all_copies(self, job_id: int) -> list[CopyRow]:
        return [CopyRow(r[0], r[1], FileStatus(r[2]), *r[3:]) for r in self.q(
            "SELECT c.file_id, c.dest_id, c.status, c.committed, c.verified_level, c.dest_hash, c.message, c.dest_path "
            "FROM copies c JOIN files f ON f.id = c.file_id WHERE f.job_id=?", (job_id,))]

    def checkpoint(self, file_id: int, dest_id: int, committed: int) -> None:
        with self._lock:
            self.db.execute("UPDATE copies SET status=?, committed=? WHERE file_id=? AND dest_id=?",
                            (FileStatus.PARTIAL.value, committed, file_id, dest_id))

    def finish_copy(self, file_id: int, dest_id: int, status: FileStatus, *, committed: int | None = None,
                    verified_level: str | None = None, dest_hash: str | None = None, message: str = "",
                    dest_path: str | None = None) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE copies SET status=?, committed=COALESCE(?, committed), verified_level=?, dest_hash=?, "
                "message=?, dest_path=COALESCE(?, dest_path) WHERE file_id=? AND dest_id=?",
                (status.value, committed, verified_level, dest_hash, message, dest_path, file_id, dest_id))

    def reset_file(self, file_id: int, size: int, mtime_ns: int) -> None:
        """The source changed since planning: forget hashes and partial progress for it."""
        with self.tx() as db:
            old_size, old_mtime = db.execute("SELECT size, mtime_ns FROM files WHERE id=?", (file_id,)).fetchone()
            # Remember which destination files are SureCopy's own copies of the old version, so they
            # can be replaced even when the dates are too close to tell which is newer.
            db.execute("UPDATE copies SET stale=? WHERE file_id=? AND status IN ('copied', 'updated')",
                       (f"{old_size}:{old_mtime}", file_id))
            db.execute("UPDATE files SET size=?, mtime_ns=?, source_hash=NULL WHERE id=?", (size, mtime_ns, file_id))
            # Every destination needs the new version, including ones that already got the old one.
            db.execute("UPDATE copies SET status=?, committed=0, dest_hash=NULL, verified_level=NULL, message='', "
                       "dest_path=NULL WHERE file_id=?", (FileStatus.PENDING.value, file_id))

    def stale_sig(self, file_id: int, dest_id: int) -> tuple[int, int] | None:
        rows = self.q("SELECT stale FROM copies WHERE file_id=? AND dest_id=?", (file_id, dest_id))
        if rows and rows[0][0]:
            size, mtime = rows[0][0].split(":")
            return int(size), int(mtime)
        return None

    def set_source_hash(self, file_id: int, digest: str) -> None:
        with self._lock:
            self.db.execute("UPDATE files SET source_hash=? WHERE id=?", (digest, file_id))

    def retry_failed(self, job_id: int) -> None:
        """Let failed copies run again on the next pass (keeps partial progress)."""
        with self._lock:
            self.db.execute(
                "UPDATE copies SET status=CASE WHEN committed > 0 THEN ? ELSE ? END, message='' "
                "WHERE status=? AND file_id IN (SELECT id FROM files WHERE job_id=?)",
                (FileStatus.PARTIAL.value, FileStatus.PENDING.value, FileStatus.FAILED.value, job_id))
            self.db.execute("UPDATE dests SET state='ready', message='' WHERE job_id=?", (job_id,))

    # ---- mirror removals ----------------------------------------------------

    def mirror_approved(self, job_id: int) -> bool:
        return bool(self.q("SELECT mirror_approved FROM jobs WHERE id=?", (job_id,))[0][0])

    def set_mirror_approved(self, job_id: int, approved: bool) -> None:
        with self._lock:
            self.db.execute("UPDATE jobs SET mirror_approved=? WHERE id=?", (int(approved), job_id))

    def replace_removal_plan(self, job_id: int, dest_id: int, rows: list[tuple[str, bool, int, int]]) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM removals WHERE job_id=? AND dest_id=? AND state='planned'", (job_id, dest_id))
            db.executemany(
                "INSERT INTO removals(job_id, dest_id, relative, is_dir, size, mtime_ns, state) "
                "VALUES (?,?,?,?,?,?,'planned')",
                [(job_id, dest_id, rel, int(is_dir), size, mtime) for rel, is_dir, size, mtime in rows])

    def removals(self, job_id: int, state: str | None = None) -> list[tuple]:
        """(id, dest_id, relative, is_dir, size, mtime_ns, state, note)"""
        sql = "SELECT id, dest_id, relative, is_dir, size, mtime_ns, state, note FROM removals WHERE job_id=?"
        args: tuple = (job_id,)
        if state:
            sql += " AND state=?"
            args += (state,)
        return self.q(sql + " ORDER BY id", args)

    def set_removal(self, removal_id: int, state: str, note: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE removals SET state=?, note=? WHERE id=?", (state, note, removal_id))

    # ---- transfer performance per run --------------------------------------------

    def add_perf(self, job_id: int, dest_id: int, nbytes: int, files: int, seconds: float,
                 src_key: str | None = None, dst_key: str | None = None, full: bool = False) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO perf(job_id, dest_id, bytes, files, seconds, src_key, dst_key, full) "
                "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(job_id, dest_id) DO UPDATE SET bytes=bytes+excluded.bytes, "
                "files=files+excluded.files, seconds=seconds+excluded.seconds, "
                "src_key=COALESCE(excluded.src_key, perf.src_key), dst_key=COALESCE(excluded.dst_key, perf.dst_key)",
                (job_id, dest_id, nbytes, files, seconds, src_key, dst_key, int(full)))

    def speed_samples(self, src_key: str, dst_key: str, full: bool, *, exclude_job: int | None = None,
                      before: float | None = None, min_seconds: float = 2.0,
                      limit: int = 30) -> list[tuple[int, int, float]]:
        """(bytes, files, seconds) from past copies between two drives, newest first."""
        sql = ("SELECT p.bytes, p.files, p.seconds FROM perf p JOIN jobs j ON j.id = p.job_id "
               "WHERE p.src_key=? AND p.dst_key=? AND p.full=? AND p.seconds >= ? "
               "AND (p.bytes >= ? OR p.files >= ?)")
        args: list = [src_key, dst_key, int(full), min_seconds, 64 * 1024 * 1024, 100]
        if exclude_job is not None:
            sql += " AND p.job_id != ?"
            args.append(exclude_job)
        if before is not None:
            sql += " AND j.created < ?"
            args.append(before)
        sql += " ORDER BY j.created DESC, j.id DESC LIMIT ?"
        args.append(limit)
        return self.q(sql, tuple(args))

    def perf_detail(self, job_id: int) -> dict[int, tuple[int, int, float, str | None, str | None, bool]]:
        """{dest_id: (bytes, files, seconds, src_key, dst_key, full)}"""
        return {d: (b, f, s, sk, dk, bool(fu)) for d, b, f, s, sk, dk, fu in self.q(
            "SELECT dest_id, bytes, files, seconds, src_key, dst_key, full FROM perf WHERE job_id=?", (job_id,))}

    def perf(self, job_id: int) -> dict[int, tuple[int, int, float]]:
        return {d: (b, f, s) for d, b, f, s in
                self.q("SELECT dest_id, bytes, files, seconds FROM perf WHERE job_id=?", (job_id,))}

    # ---- log ----------------------------------------------------------------

    def log(self, job_id: int, level: str, text: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO log(job_id, ts, level, text) VALUES (?,?,?,?)",
                            (job_id, time.time(), level, text))

    def log_lines(self, job_id: int) -> list[tuple[float, str, str]]:
        return self.q("SELECT ts, level, text FROM log WHERE job_id=? ORDER BY id", (job_id,))
