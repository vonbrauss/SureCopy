import errno
import os
import time
import shutil
from unittest import mock

from surecopy import engine as engine_mod
from surecopy.engine import Controls, Engine, partial_path
from surecopy.jobs import QueueRunner, plan_job, resume_job, verify_later
from surecopy.models import ExistingPolicy, FileStatus, JobStatus, VerifyLevel
from surecopy.models import SourceFile
from surecopy.report import report_text
from surecopy.scan import ScanResult, check_names

from .helpers import Sandbox, read, tree

KB = 1024
OLD = 1_500_000_000_000_000_000      # 2017
NEWER = 2_000_000_000_000_000_000    # 2033, newer than any fixture


class Base(Sandbox):
    def plan(self, *args, **kw):
        kw.setdefault("chunk_mib", 1)
        job_id, result = plan_job(self.store, self.spec(*args, **kw))
        self.assertIsNotNone(job_id, result.problems)
        return job_id

    def existing(self, root, rel, data, mtime_ns):
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def statuses(self, job_id):
        return [c.status for c in self.store.all_copies(job_id)]

    def _pause_at(self, controls, at):
        def fault(root, rel, pos):
            if pos >= at:
                controls.pause()
        return fault


class EngineTests(Base):
    def test_copies_to_two_destinations(self):
        src = self.make({"a.mov": 3 * 1024 * KB, "sub/b.wav": 10 * KB, "sub/deep/empty.txt": b""})
        os.makedirs(os.path.join(src, "emptydir"))
        os.utime(os.path.join(src, "a.mov"), ns=(1_600_000_000_000_000_000,) * 2)
        a, b = self.dest("A"), self.dest("B")
        job = self.plan([src], [a, b])
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        for d in (a, b):
            self.assertEqual(tree(os.path.join(d, "Show")), tree(src))
            self.assertTrue(os.path.isdir(os.path.join(d, "Show", "emptydir")))
            self.assertEqual(os.stat(os.path.join(d, "Show", "a.mov")).st_mtime_ns, 1_600_000_000_000_000_000)
        self.assertTrue(all(s == FileStatus.COPIED for s in self.statuses(job)))
        self.assertFalse(any(n.endswith(".surecopy-partial") for d in (a, b) for _, _, fs in os.walk(d) for n in fs))

    def test_single_file_source_and_default_excludes(self):
        src = self.make({"clip.mxf": 5 * KB, ".DS_Store": b"x", "._clip.mxf": b"x", "skip.tmp": b"x"})
        a = self.dest("A")
        job = self.plan([src, os.path.join(src, "clip.mxf")], [a], excludes=["*.tmp"])
        Engine(self.store).run(job)
        self.assertEqual(sorted(tree(a)), ["Show/clip.mxf", "clip.mxf"])

    def test_longest_valid_names_copy_and_update(self):
        name = "n" * 251 + ".mov"                      # 255 bytes, the filesystem maximum
        src = self.make({name: 3 * 1024 * KB})
        a = self.dest("A")
        job = self.plan([src], [a])
        controls = Controls()
        Engine(self.store, controls, fault=self._pause_at(controls, 1024 * KB), checkpoint_bytes=1).run(job)
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(os.path.join(a, "Show", name)), read(os.path.join(src, name)))
        os.utime(os.path.join(src, name), ns=(NEWER, NEWER))   # now update it in place
        self.assertEqual(Engine(self.store).run(self.plan([src], [a])), JobStatus.DONE)
        self.assertEqual(os.listdir(os.path.join(a, "Show")), [name])

    def test_crash_right_after_publish_leaves_no_temp_file(self):
        src = self.make({"a.mov": 50 * KB})
        a = self.dest("A")
        Engine(self.store).run(self.plan([src], [a]))
        final = os.path.join(a, "Show", "a.mov")
        os.link(final, partial_path(final))            # state after link(), before unlink()
        aside = engine_mod.fsio.temp_name(final, ".surecopy-old")
        with open(aside, "wb") as fh:                   # and an aside from an interrupted replace
            fh.write(b"old")
        self.assertEqual(Engine(self.store).run(self.plan([src], [a])), JobStatus.DONE)
        self.assertEqual(sorted(os.listdir(os.path.join(a, "Show"))), ["a.mov"])

    def test_skips_identical_files(self):
        src = self.make({"a.mov": 50 * KB})
        a = self.dest("A")
        Engine(self.store).run(self.plan([src], [a]))
        job2 = self.plan([src], [a])
        self.assertEqual(Engine(self.store).run(job2), JobStatus.DONE)
        self.assertEqual(self.statuses(job2), [FileStatus.SKIPPED])

    def test_never_policy_does_not_overwrite(self):
        src = self.make({"a.mov": 50 * KB})
        a = self.dest("A")
        old = self.existing(a, "Show/a.mov", b"precious", OLD)
        job = self.plan([src], [a], on_existing=ExistingPolicy.NEVER)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE_WITH_ISSUES)
        self.assertEqual(read(old), b"precious")
        self.assertEqual(self.statuses(job), [FileStatus.CONFLICT])
        self.assertIn("conflict", report_text(self.store, job))

    def test_pause_then_resume_continues_from_checkpoint(self):
        src = self.make({"big.mov": 4 * 1024 * KB, "next.mov": 10 * KB})
        a, b = self.dest("A"), self.dest("B")
        job = self.plan([src], [a, b])
        controls = Controls()
        eng = Engine(self.store, controls, fault=self._pause_at(controls, 2 * 1024 * KB), checkpoint_bytes=1)
        self.assertEqual(eng.run(job), JobStatus.PAUSED)
        big = next(f for f in self.store.files(job) if f.relative.endswith("big.mov"))
        committed = {c.dest_id: c.committed for c in self.store.copies(big.id).values()}
        self.assertTrue(all(v >= 2 * 1024 * KB for v in committed.values()), committed)
        self.assertTrue(os.path.exists(partial_path(os.path.join(a, "Show", "big.mov"))))
        self.assertFalse(os.path.exists(os.path.join(a, "Show", "big.mov")))

        writes = []
        resume_job(self.store, job)
        status = Engine(self.store, fault=lambda r, rel, pos: writes.append((rel, pos))).run(job)
        self.assertEqual(status, JobStatus.DONE)
        first = min(pos for rel, pos in writes if rel.endswith("big.mov"))
        self.assertGreaterEqual(first, 2 * 1024 * KB, "resume should not rewrite finished chunks")
        for d in (a, b):
            self.assertEqual(tree(os.path.join(d, "Show")), tree(src))

    def _paused_partial(self, verify):
        src = self.make({"big.mov": 4 * 1024 * KB})
        a = self.dest("A")
        job = self.plan([src], [a], verify=verify)
        controls = Controls()
        Engine(self.store, controls, fault=self._pause_at(controls, 2 * 1024 * KB), checkpoint_bytes=1).run(job)
        part = partial_path(os.path.join(a, "Show", "big.mov"))
        with open(part, "r+b") as fh:   # damage data already copied in the first pass
            fh.write(b"\0" * 100)
        resume_job(self.store, job)
        return job, src, a, Engine(self.store).run(job)

    def test_full_verify_resume_rechecks_earlier_partial_data(self):
        job, src, a, status = self._paused_partial(VerifyLevel.FULL)
        self.assertEqual(status, JobStatus.DONE)
        self.assertEqual(read(os.path.join(a, "Show", "big.mov")), read(os.path.join(src, "big.mov")))

    def test_fast_resume_trusts_checkpoint(self):
        # Documents the trade-off: Fast doesn't re-read earlier partial data.
        job, src, a, status = self._paused_partial(VerifyLevel.FAST)
        self.assertEqual(status, JobStatus.DONE)
        self.assertNotEqual(read(os.path.join(a, "Show", "big.mov")), read(os.path.join(src, "big.mov")))
        # ...and Verify later catches it.
        self.assertEqual(verify_later(self.store, job)["mismatch"], 1)
        self.assertIn("MISMATCH", report_text(self.store, job))

    def test_one_failing_destination_does_not_stop_the_others(self):
        src = self.make({"a.mov": 30 * KB, "b.mov": 30 * KB, "c.mov": 30 * KB})
        a, b = self.dest("A"), self.dest("B")
        job = self.plan([src], [a, b])

        def fault(root, rel, pos):
            if root == b and rel.endswith("b.mov"):
                raise OSError(errno.EIO, "Input/output error")
        self.assertEqual(Engine(self.store, fault=fault).run(job), JobStatus.DONE_WITH_ISSUES)
        self.assertEqual(tree(os.path.join(a, "Show")), tree(src))
        self.assertEqual(sorted(tree(os.path.join(b, "Show"))), ["a.mov", "c.mov"])
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(tree(os.path.join(b, "Show")), tree(src))

    def test_disconnected_destination_resumes_after_reconnect(self):
        src = self.make({"a.mov": 2 * 1024 * KB, "b.mov": 30 * KB})
        a, b = self.dest("A"), self.dest("B")
        job = self.plan([src], [a, b])
        unplugged = b + "-unplugged"

        def fault(root, rel, pos):
            if root == b and rel.endswith("a.mov") and pos >= 1024 * KB:
                os.rename(b, unplugged)
                raise OSError(errno.ENXIO, "Device not configured")
        self.assertEqual(Engine(self.store, fault=fault, checkpoint_bytes=1).run(job), JobStatus.NEEDS_RESUME)
        self.assertEqual(tree(os.path.join(a, "Show")), tree(src))
        dest_b = next(d for d in self.store.dests(job) if d.root == b)
        self.assertEqual(dest_b.state, "disconnected")

        os.rename(unplugged, b)
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(tree(os.path.join(b, "Show")), tree(src))

    def test_full_verify_mismatch_is_never_published(self):
        src = self.make({"a.mov": 30 * KB})
        a, b = self.dest("A"), self.dest("B")
        job = self.plan([src], [a, b], verify=[VerifyLevel.FULL, VerifyLevel.FAST])
        with mock.patch.object(Engine, "_readback", staticmethod(lambda p: "bad")):
            self.assertEqual(Engine(self.store).run(job), JobStatus.DONE_WITH_ISSUES)
        self.assertFalse(os.path.exists(os.path.join(a, "Show", "a.mov")))
        self.assertTrue(os.path.exists(os.path.join(b, "Show", "a.mov")))
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        levels = {c.verified_level for c in self.store.all_copies(job)}
        self.assertEqual(levels, {"full", "fast"})

    def test_source_hash_level_records_hash(self):
        src = self.make({"a.mov": 30 * KB})
        job = self.plan([src], [self.dest("A")], verify=VerifyLevel.SOURCE_HASH)
        Engine(self.store).run(job)
        self.assertEqual(len(self.store.files(job)[0].source_hash), 32)

    def test_fast_level_skips_hashing(self):
        src = self.make({"a.mov": 30 * KB})
        job = self.plan([src], [self.dest("A")])
        Engine(self.store).run(job)
        self.assertIsNone(self.store.files(job)[0].source_hash)

    def test_source_changing_mid_copy_fails_then_recopies(self):
        src = self.make({"a.mov": 3 * 1024 * KB})
        a = self.dest("A")
        job = self.plan([src], [a])
        path = os.path.join(src, "a.mov")
        real_pread = os.pread

        def pread(fd, n, pos):   # someone edits the source while it is being read
            data = real_pread(fd, n, pos)
            if pos >= 1024 * KB:
                os.utime(path, ns=(1, 1))
            return data
        with mock.patch.object(engine_mod.os, "pread", pread):
            self.assertEqual(Engine(self.store).run(job), JobStatus.DONE_WITH_ISSUES)
        self.assertFalse(os.path.exists(os.path.join(a, "Show", "a.mov")))
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(os.path.join(a, "Show", "a.mov")), read(path))

    def test_source_edited_after_copy_is_recopied_on_resume(self):
        src = self.make({"a.txt": 10 * KB, "big.mov": 4 * 1024 * KB})
        a = self.dest("A")
        job = self.plan([src], [a])
        controls = Controls()

        def fault(root, rel, pos):
            if rel.endswith("big.mov") and pos >= 1024 * KB:   # a.txt is already copied by now
                with open(os.path.join(src, "a.txt"), "ab") as fh:
                    fh.write(b"edited")
                controls.pause()
        Engine(self.store, controls, fault=fault, checkpoint_bytes=1).run(job)
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(os.path.join(a, "Show", "a.txt")), read(os.path.join(src, "a.txt")))

    def test_cancel_removes_only_partials(self):
        src = self.make({"a.mov": 10 * KB, "big.mov": 4 * 1024 * KB})
        a = self.dest("A")
        job = self.plan([src], [a])
        controls = Controls()

        def fault(root, rel, pos):
            if rel.endswith("big.mov") and pos >= 1024 * KB:
                controls.cancel()
        self.assertEqual(Engine(self.store, controls, fault=fault, checkpoint_bytes=1).run(job), JobStatus.CANCELLED)
        self.assertEqual(sorted(tree(a)), ["Show/a.mov"])

    def test_refuses_destination_inside_source(self):
        src = self.make({"a.mov": 10 * KB})
        inside = os.path.join(src, "backup")
        os.makedirs(inside)
        job = self.plan([src], [inside])
        self.assertEqual(Engine(self.store).run(job), JobStatus.NEEDS_RESUME)
        self.assertEqual(self.store.dests(job)[0].state, "failed")
        self.assertEqual(self.store.dests(job)[0].message, "Destination is inside a source folder")

    def test_not_enough_space_fails_early(self):
        src = self.make({"a.mov": 100 * KB})
        a = self.dest("A")
        job = self.plan([src], [a])
        fake = shutil._ntuple_diskusage(1000, 1000, 10) if hasattr(shutil, "_ntuple_diskusage") else None
        with mock.patch.object(engine_mod.shutil, "disk_usage", return_value=fake):
            Engine(self.store).run(job)
        self.assertIn("Not enough space", self.store.dests(job)[0].message)
        self.assertEqual(tree(a), {})

    def test_space_check_ignores_files_already_there(self):
        src = self.make({"a.mov": 100 * KB, "b.mov": 10 * KB})
        a = self.dest("A")
        Engine(self.store).run(self.plan([src], [a]))
        with open(os.path.join(src, "c.mov"), "wb") as fh:
            fh.write(os.urandom(5 * KB))
        job = self.plan([src], [a])
        fake = shutil._ntuple_diskusage(10**9, 10**9, 50 * KB)   # room for c.mov, not for everything
        with mock.patch.object(engine_mod.shutil, "disk_usage", return_value=fake):
            self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)

    def test_planning_warns_on_names_nexis_rejects(self):
        src = self.make({"bad:name.mov": b"3", "ok.mov": b"1"})
        job, result = plan_job(self.store, self.spec([src], [self.dest("A")]))
        self.assertIsNotNone(job)
        self.assertTrue(any("bad:name.mov" in w for w in result.warnings))

    def test_names_differing_only_by_case_block_the_job(self):
        # Built directly: a Mac's case-insensitive disk can't hold both files as fixtures.
        result = ScanResult(files=[SourceFile("/s", f"Show/{n}", f"/s/{n}", 1, 0, 0o644)
                                   for n in ("Clip.mov", "clip.mov")], directories=["Show"])
        check_names(result)
        self.assertTrue(any("upper/lowercase" in p for p in result.problems))

    def test_queue_runs_jobs_in_order(self):
        s1 = self.make({"a.mov": 10 * KB}, os.path.join(self.src, "One"))
        s2 = self.make({"b.mov": 10 * KB}, os.path.join(self.src, "Two"))
        a = self.dest("A")
        j1, j2 = self.plan([s1], [a]), self.plan([s2], [a])
        self.store.move_job(j2, 0)
        order = []
        runner = QueueRunner(self.store, on_job_done=lambda j, s: order.append((j, s)))
        runner.start()
        runner.wait(30)
        self.assertEqual(order, [(j2, JobStatus.DONE), (j1, JobStatus.DONE)])

    def test_queue_never_loses_a_start_or_misdirects_a_pause(self):
        a = self.dest("A")
        jobs = []
        for i in range(12):
            s = self.make({"x.bin": 2000}, os.path.join(self.src, f"S{i}"))
            jobs.append(self.plan([s], [a]))
        runner = QueueRunner(self.store)
        for j in jobs:                               # hammer start() while jobs finish
            runner.start()
            self.assertFalse(runner.stop_all(-1))    # a pause for a job that isn't running does nothing
        deadline = time.time() + 30
        while time.time() < deadline and any(self.store.job_status(j) != JobStatus.DONE for j in jobs):
            runner.start()
            time.sleep(0.01)
        self.assertTrue(all(self.store.job_status(j) == JobStatus.DONE for j in jobs))

    def test_progress_reports_bytes_per_destination(self):
        src = self.make({"a.mov": 2 * 1024 * KB})
        a, b = self.dest("A"), self.dest("B")
        seen = []
        Engine(self.store, on_progress=seen.append).run(self.plan([src], [a, b]))
        last = seen[-1]
        self.assertEqual(last.dest_bytes, {a: 2 * 1024 * KB, b: 2 * 1024 * KB})
        self.assertEqual(last.total_bytes, 2 * 1024 * KB)


class SyncTests(Base):
    """Re-running a backup: update older files, keep newer ones, never lose data."""

    def test_updates_older_file_at_destination(self):
        src = self.make({"edit.prproj": 40 * KB, "same.wav": 10 * KB})
        a, b = self.dest("A"), self.dest("B")
        old = self.existing(a, "Show/edit.prproj", b"last week's version", OLD)
        job = self.plan([src], [a, b], verify=[VerifyLevel.FULL, VerifyLevel.FAST])
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(old), read(os.path.join(src, "edit.prproj")))
        self.assertEqual(os.stat(old).st_mtime_ns, os.stat(os.path.join(src, "edit.prproj")).st_mtime_ns)
        statuses = sorted(c.status.value for c in self.store.all_copies(job))
        self.assertEqual(statuses, ["copied", "copied", "copied", "updated"])
        self.assertFalse([n for n in os.listdir(os.path.join(a, "Show")) if n.startswith(".")])
        self.assertIn("1 updated", report_text(self.store, job))
        self.assertEqual(verify_later(self.store, job)["verified"], 4)

    def test_keeps_newer_file_at_destination(self):
        src = self.make({"notes.docx": 10 * KB})
        a = self.dest("A")
        newer = self.existing(a, "Show/notes.docx", b"edited on the backup", NEWER)
        job = self.plan([src], [a])
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(newer), b"edited on the backup")
        self.assertEqual(self.statuses(job), [FileStatus.KEPT_NEWER])

    def test_rerun_only_copies_what_changed(self):
        src = self.make({"a.mov": 20 * KB, "b.mov": 20 * KB, "c.pdf": 5 * KB})
        a = self.dest("A")
        Engine(self.store).run(self.plan([src], [a]))
        with open(os.path.join(src, "c.pdf"), "ab") as fh:
            fh.write(b"revised")
        os.utime(os.path.join(src, "c.pdf"), ns=(NEWER, NEWER))
        self.make({"new.mov": 5 * KB})
        job = self.plan([src], [a])
        Engine(self.store).run(job)
        by_name = {f.relative: self.store.copies(f.id) for f in self.store.files(job)}
        got = {rel: next(iter(c.values())).status.value for rel, c in by_name.items()}
        self.assertEqual(got, {"Show/a.mov": "skipped", "Show/b.mov": "skipped",
                               "Show/c.pdf": "updated", "Show/new.mov": "copied"})
        self.assertEqual(tree(os.path.join(a, "Show")), tree(src))

    def test_keep_both_saves_alongside_and_rerun_skips(self):
        src = self.make({"logo.psd": 10 * KB})
        a = self.dest("A")
        old = self.existing(a, "Show/logo.psd", b"original", OLD)
        job = self.plan([src], [a], on_existing=ExistingPolicy.KEEP_BOTH)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(old), b"original")
        kept = os.path.join(a, "Show", "logo (2).psd")
        self.assertEqual(read(kept), read(os.path.join(src, "logo.psd")))
        job2 = self.plan([src], [a], on_existing=ExistingPolicy.KEEP_BOTH)
        Engine(self.store).run(job2)
        self.assertEqual(self.statuses(job2), [FileStatus.SKIPPED])
        self.assertFalse(os.path.exists(os.path.join(a, "Show", "logo (3).psd")))
        self.assertEqual(verify_later(self.store, job)["verified"], 1)

    def test_old_file_untouched_until_update_finishes(self):
        src = self.make({"big.mov": 4 * 1024 * KB})
        a = self.dest("A")
        old = self.existing(a, "Show/big.mov", b"old cut", OLD)
        job = self.plan([src], [a])
        controls = Controls()
        Engine(self.store, controls, fault=self._pause_at(controls, 2 * 1024 * KB), checkpoint_bytes=1).run(job)
        self.assertEqual(read(old), b"old cut")
        resume_job(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertEqual(read(old), read(os.path.join(src, "big.mov")))

    def test_destination_edited_during_update_is_not_replaced(self):
        src = self.make({"doc.txt": 2 * 1024 * KB})
        a = self.dest("A")
        old = self.existing(a, "Show/doc.txt", b"v1", OLD)

        def fault(root, rel, pos):
            if pos == 0:   # someone saves the backup copy while we're copying
                with open(old, "wb") as fh:
                    fh.write(b"someone else's edit")
        job = self.plan([src], [a])
        self.assertEqual(Engine(self.store, fault=fault).run(job), JobStatus.DONE_WITH_ISSUES)
        self.assertEqual(read(old), b"someone else's edit")
        self.assertEqual(self.statuses(job), [FileStatus.CONFLICT])


class HistoryTests(Base):
    def test_runs_of_the_same_copy_are_grouped(self):
        src = self.make({"a.mov": 10 * KB})
        a, b = self.dest("A"), self.dest("B")
        j1 = self.plan([src], [a, b])
        Engine(self.store).run(j1)
        j2 = self.plan([src + "/"], [b, a], verify=VerifyLevel.FULL)   # order, slash and level don't matter
        Engine(self.store).run(j2)
        other = self.plan([src], [a])
        key = self.store.task_of(j1)
        self.assertEqual(self.store.task_of(j2), key)
        self.assertNotEqual(self.store.task_of(other), key)
        runs = self.store.runs(key)
        self.assertEqual([r[0] for r in runs], [j2, j1])
        self.assertIsNotNone(runs[0][4])          # started
        self.assertIsNotNone(runs[0][5])          # finished
        self.assertEqual(self.store.run_summary(j1)["copied"], 1)    # per destination, not summed
        self.assertEqual(self.store.run_summary(j2)["skipped"], 1)
        tasks = {k: (latest, n) for k, latest, n in self.store.tasks()}
        self.assertEqual(tasks[key], (j2, 2))

    def test_old_databases_get_history_keys(self):
        src = self.make({"a.mov": 10 * KB})
        job = self.plan([src], [self.dest("A")])
        self.store.db.execute("UPDATE jobs SET task_key=NULL")
        self.store.db.execute("ALTER TABLE jobs DROP COLUMN started")
        self.store.db.execute("ALTER TABLE jobs DROP COLUMN task_key")
        from surecopy.store import Store
        again = Store(self.store.path)
        self.assertTrue(again.task_of(job))
        again.close()
