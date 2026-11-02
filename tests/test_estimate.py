import os
from unittest import mock

from surecopy import drives
from surecopy.drives import identify, sources_key, volume_for
from surecopy.engine import Engine
from surecopy.estimate import enough_work, estimate_job, fit, model_for, pace, work_needed
from surecopy.jobs import check_job, plan_job
from surecopy.models import Destination, JobSpec, VerifyLevel

from .helpers import Sandbox

MB = 1024 * 1024


class FitTests(Sandbox):
    def test_learns_big_file_speed_and_per_file_cost(self):
        a, b = 1 / (200 * MB), 0.004            # 200 MB/s and 4 ms per file
        samples = [(n, f, n * a + f * b) for n, f in
                   ((5000 * MB, 10), (300 * MB, 20000), (1200 * MB, 3000), (50 * MB, 8000))]
        m = fit(samples)
        self.assertAlmostEqual(m.bytes_per_sec / MB, 200, delta=1)
        self.assertAlmostEqual(m.sec_per_file, 0.004, delta=0.0002)
        self.assertEqual(m.runs, 4)
        # estimates a mix it hasn't seen
        self.assertAlmostEqual(m.seconds(2000 * MB, 50000), 2000 * MB * a + 50000 * b, delta=1)

    def test_only_big_files_still_gives_a_speed(self):
        m = fit([(10_000 * MB, 4, 50.0), (20_000 * MB, 9, 100.0)])
        self.assertAlmostEqual(m.bytes_per_sec / MB, 200, delta=2)
        self.assertGreaterEqual(m.sec_per_file, 0)

    def test_recent_copies_count_most(self):
        newest_slow = [(1000 * MB, 1, 20.0)] * 3          # drive got slower lately: 50 MB/s
        older_fast = [(1000 * MB, 1, 5.0)] * 10           # used to be 200 MB/s
        m = fit(newest_slow + older_fast)
        self.assertLess(m.bytes_per_sec / MB, 100)

    def test_no_history_no_model_and_pace(self):
        self.assertIsNone(fit([]))
        self.assertIsNone(pace(fit([(1000 * MB, 100, 10.0)]), 1000 * MB, 100, 20.0))   # one copy: don't grade
        m = fit([(1000 * MB, 100, 10.0), (500 * MB, 2000, 7.0)])
        self.assertAlmostEqual(pace(m, 1000 * MB, 100, 20.0), 0.5, delta=0.01)
        self.assertIsNone(pace(m, 1 * MB, 3, 0.5))          # too little work to judge
        self.assertFalse(enough_work(10 * MB, 10, 5.0))
        self.assertTrue(enough_work(10 * MB, 500, 5.0))


class HistoryTests(Sandbox):
    def setUp(self):
        super().setUp()
        drives._cache.clear()

    def test_engine_tags_runs_with_both_drives(self):
        src = self.make({"a.mov": 6 * MB, "b.txt": 1000})
        a, b = self.dest("A"), self.dest("B")
        job, _ = plan_job(self.store, JobSpec("p", [src], [Destination(a), Destination(b, VerifyLevel.FULL)]))
        Engine(self.store).run(job)
        rows = sorted(self.store.perf_detail(job).values(), key=lambda r: r[5])
        self.assertEqual([r[:2] for r in rows], [(6 * MB + 1000, 2)] * 2)
        self.assertEqual(rows[0][3], sources_key([src]))
        self.assertEqual(rows[0][4], volume_for(a).key)
        self.assertEqual([r[5] for r in rows], [False, True])
        job2, _ = plan_job(self.store, JobSpec("p", [src], [Destination(a)]))
        Engine(self.store).run(job2)                    # everything skipped: nothing to learn from
        self.assertEqual(self.store.perf_detail(job2), {})

    def test_model_uses_only_the_same_drives_and_verify_level(self):
        src = self.make({"a.mov": 1000})
        job, _ = plan_job(self.store, JobSpec("p", [src], [Destination(self.dest("A"))]))
        dest_id = self.store.dests(job)[0].id
        self.store.add_perf(job, dest_id, 1000 * MB, 10, 10.0, "src", "dstA", False)
        other, _ = plan_job(self.store, JobSpec("p", [src], [Destination(self.dest("A"))]))
        self.store.add_perf(other, self.store.dests(other)[0].id, 1000 * MB, 10, 50.0, "src", "dstB", False)
        m = model_for(self.store, "src", "dstA", False)
        self.assertAlmostEqual(m.seconds(1000 * MB, 10), 10.0, delta=0.1)
        self.assertEqual(m.runs, 1)
        self.assertIsNone(model_for(self.store, "src", "dstA", True))          # no Full verify history
        self.assertIsNone(model_for(self.store, "src", "dstA", False, exclude_job=job))
        self.assertIsNone(model_for(self.store, None, "dstA", False))          # mixed or missing source drive

    def test_estimate_counts_only_what_needs_copying(self):
        src = self.make({"a.mov": 2000, "b.mov": 3000, "c.mov": 4000})
        a = self.dest("A")
        spec = JobSpec("p", [src], [Destination(a)])
        job, _ = plan_job(self.store, spec)
        Engine(self.store).run(job)
        with open(os.path.join(src, "c.mov"), "ab") as fh:
            fh.write(b"changed")
        result = check_job(spec)
        self.assertEqual(work_needed(result.files, a), (4007, 1))
        est = estimate_job(self.store, spec, result.files)[0]
        self.assertEqual((est.nbytes, est.nfiles, est.seconds, est.runs), (4007, 1, None, 0))   # no usable history


class DriveIdentityTests(Sandbox):
    def test_identity_is_stable_and_ignores_share_user(self):
        a = identify(self.dest("A"))
        self.assertEqual(identify(self.dest("A")).key, a.key)
        with mock.patch.object(drives, "mount_point", return_value="/Volumes/Projects"), \
             mock.patch.object(drives, "_mount_table", return_value={
                 "/Volumes/Projects": ("//kevin@nexis.local/Projects", "smbfs")}):
            v1 = identify("/Volumes/Projects/2026")
        with mock.patch.object(drives, "mount_point", return_value="/Volumes/Projects-1"), \
             mock.patch.object(drives, "_mount_table", return_value={
                 "/Volumes/Projects-1": ("//editor2@NEXIS.local/Projects", "smbfs")}):
            v2 = identify("/Volumes/Projects-1/x")
        self.assertEqual(v1.kind, "network")
        self.assertEqual(v1.key, v2.key)

    def test_sources_on_two_drives_have_no_single_source(self):
        with mock.patch.object(drives, "volume_for", side_effect=lambda p: drives.Volume(p[:2], p, "local", "/")):
            self.assertIsNone(sources_key(["/a/x", "/b/y"]))
            self.assertEqual(sources_key(["/a/x", "/a/y"]), "/a")


class VolumeCacheTests(Sandbox):
    def test_a_drive_that_comes_back_is_recognized_at_once(self):
        drives._cache.clear()
        a = self.dest("A")
        gone = a + "-unplugged"
        os.rename(a, gone)
        self.assertIsNone(volume_for(a))
        os.rename(gone, a)
        self.assertIsNotNone(volume_for(a))
