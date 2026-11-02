import os
import unicodedata

from surecopy.engine import Engine
from surecopy.jobs import approve_mirror, decline_mirror, mirror_preview, plan_job
from surecopy.mirror import REMOVED_FOLDER, preview_text
from surecopy.models import JobStatus

from .helpers import Sandbox, read, tree

KB = 1024


class MirrorTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.srcdir = self.make({"keep.mov": 10 * KB, "sub/keep.wav": 5 * KB})
        self.a = self.dest("A")
        Engine(self.store).run(self.job())          # first backup, no mirror
        self.stale = self.put("Show/old_cut.mov", b"stale")
        self.stale_dir_file = self.put("Show/OldFolder/x.pdf", b"stale")

    def job(self, mirror=False, **kw):
        job_id, result = plan_job(self.store, self.spec([self.srcdir], [self.a], mirror=mirror, **kw))
        self.assertIsNotNone(job_id, result.problems)
        return job_id

    def put(self, rel, data, root=None):
        path = os.path.join(root or self.a, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def run_mirror(self, **kw):
        job = self.job(mirror=True, **kw)
        status = Engine(self.store).run(job)
        return job, status

    def test_mirror_off_never_removes(self):
        Engine(self.store).run(self.job())
        self.assertTrue(os.path.exists(self.stale))

    def test_nothing_removed_until_approved(self):
        job, status = self.run_mirror()
        self.assertEqual(status, JobStatus.CONFIRM_MIRROR)
        self.assertTrue(os.path.exists(self.stale))
        text = preview_text(mirror_preview(self.store, job), self.store.spec(job))
        self.assertIn("MIRROR MODE WARNING", text)
        self.assertIn("Show/old_cut.mov", text)
        self.assertIn("Show/OldFolder/x.pdf", text)

    def test_approved_files_move_to_removed_folder(self):
        job, _ = self.run_mirror()
        mirror_preview(self.store, job)
        approve_mirror(self.store, job)
        self.assertEqual(Engine(self.store).run(job), JobStatus.DONE)
        self.assertFalse(os.path.exists(self.stale))
        self.assertFalse(os.path.exists(os.path.dirname(self.stale_dir_file)))
        self.assertEqual(tree(os.path.join(self.a, "Show")), tree(self.srcdir))
        binned = tree(os.path.join(self.a, REMOVED_FOLDER))
        self.assertEqual(sorted(os.path.basename(k) for k in binned), ["old_cut.mov", "x.pdf"])
        self.assertTrue(all(v == b"stale" for v in binned.values()))
        # Approval is used up: the next mirror run asks again.
        self.put("Show/another.mov", b"stale")
        job2, status = self.run_mirror()
        self.assertEqual(status, JobStatus.CONFIRM_MIRROR)

    def test_permanent_delete_is_separate_setting(self):
        job, _ = self.run_mirror(mirror_delete=True)
        self.assertIn("PERMANENTLY DELETED", preview_text(mirror_preview(self.store, job), self.store.spec(job)))
        approve_mirror(self.store, job)
        Engine(self.store).run(job)
        self.assertFalse(os.path.exists(self.stale))
        self.assertFalse(os.path.exists(os.path.join(self.a, REMOVED_FOLDER)))

    def test_decline_keeps_everything(self):
        job, _ = self.run_mirror()
        decline_mirror(self.store, job)
        self.assertEqual(self.store.job_status(job), JobStatus.DONE)
        self.assertTrue(os.path.exists(self.stale))

    def test_approval_covers_only_the_previewed_files(self):
        job, _ = self.run_mirror()
        mirror_preview(self.store, job)
        late = self.put("Show/added_after_preview.mov", b"new")
        with open(self.stale, "ab") as fh:     # changed after the preview
            fh.write(b" edited")
        approve_mirror(self.store, job)
        Engine(self.store).run(job)
        self.assertTrue(os.path.exists(late))
        self.assertTrue(os.path.exists(self.stale))
        self.assertFalse(os.path.exists(self.stale_dir_file))

    def test_protects_excluded_files_and_rest_of_drive(self):
        ds = self.put("Show/.DS_Store", b"x")
        tmp = self.put("Show/render.tmp", b"x")
        other = self.put("Other Project/file.mov", b"not part of this job")
        job, _ = self.run_mirror(excludes=["*.tmp"])
        approve_mirror(self.store, job)
        Engine(self.store).run(job)
        for path in (ds, tmp, other):
            self.assertTrue(os.path.exists(path), path)

    def test_skipped_when_copy_incomplete(self):
        def fault(root, rel, pos):
            raise OSError(5, "Input/output error")
        os.utime(os.path.join(self.srcdir, "keep.mov"), ns=(2 * 10**18, 2 * 10**18))  # forces an update
        job = self.job(mirror=True)
        self.assertEqual(Engine(self.store, fault=fault).run(job), JobStatus.DONE_WITH_ISSUES)
        self.assertEqual(self.store.removals(job), [])
        self.assertTrue(os.path.exists(self.stale))

    def test_empty_source_is_flagged_as_danger(self):
        for rel in ("keep.mov", "sub/keep.wav"):
            os.unlink(os.path.join(self.srcdir, rel))
        job, status = self.run_mirror()
        self.assertEqual(status, JobStatus.CONFIRM_MIRROR)
        text = preview_text(mirror_preview(self.store, job), self.store.spec(job))
        self.assertIn("DANGER", text)
        self.assertIn("is empty", text)

    def test_large_removal_is_flagged_as_danger(self):
        for i in range(30):
            self.put(f"Show/stale_{i}.mov", b"x")
        job, _ = self.run_mirror()
        text = preview_text(mirror_preview(self.store, job), self.store.spec(job))
        self.assertIn("DANGER: This removes", text)

    def test_names_compare_like_mac_drives(self):
        name = "Café.mov"
        self.make({unicodedata.normalize("NFC", name): b"1"}, self.srcdir)
        nfd = self.put("Show/" + unicodedata.normalize("NFD", name), b"1")
        job, _ = self.run_mirror()
        preview = mirror_preview(self.store, job)
        listed = [rel for p in preview for rel, _ in p.files]
        self.assertNotIn("Show/" + unicodedata.normalize("NFD", name), listed)
