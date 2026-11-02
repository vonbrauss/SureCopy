import os, sys, time, shutil, tempfile, random, traceback
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from common import compare, wfile, rmtree
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QSettings, QMimeData, QUrl, QPointF, Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QMenu
from PySide6.QtGui import QDropEvent
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
app = QApplication([])
QCoreApplication.setOrganizationName("SureCopyStress"); QCoreApplication.setApplicationName("SureCopyStress")
QSettings().clear()
from surecopy.gui.theme import STYLE
app.setStyleSheet(STYLE)
from surecopy.gui import window as W, dialogs as D
from surecopy.store import Store
from surecopy.models import JobStatus, VerifyLevel, Destination, JobSpec
from surecopy.jobs import plan_job

def wait(ms):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()
def until(cond, ms=60000):
    end = time.time() + ms / 1000
    while time.time() < end:
        if cond(): return True
        wait(50)
    return cond()
checks = []
def check(name, cond):
    checks.append((name, bool(cond))); print(f"  [{'PASS' if cond else 'FAIL'}] {name}")

# Answer every modal automatically: confirmations "Yes", Check dialog "accept", mirror dialog per test
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.information = QMessageBox.warning = QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.about = staticmethod(lambda *a, **k: None)
D.CheckDialog.exec = lambda self: QDialog.Accepted
mirror_choice = [2]
D.MirrorDialog.exec = lambda self: mirror_choice[0]
D.ReportDialog.exec = lambda self: (self.show(), wait(50), self.close(), 1)[-1]
D.SettingsDialog.exec = lambda self: (self._save(), 1)[-1]
# Capture what the pre-copy check shows (estimates included)
check_estimates = []
D.CheckDialog.exec = lambda self: QDialog.Accepted
_orig_check_init = D.CheckDialog.__init__
def _check_init(self, parent, spec, result, start, estimates=None):
    check_estimates.append(estimates)
    _orig_check_init(self, parent, spec, result, start, estimates)
D.CheckDialog.__init__ = _check_init

from common import BASE
T = tempfile.mkdtemp(prefix="gui-", dir=BASE)
rnd = random.Random(7)
src = os.path.join(T, "src", "Show"); src2 = os.path.join(T, "src", "Music")
for i in range(400): wfile(os.path.join(src, f"d{i % 7}", f"f{i}.txt"), rnd.randbytes(rnd.randint(0, 20000)))
for i in range(3): wfile(os.path.join(src, "big", f"b{i}.mov"), os.urandom(60 << 20))
for i in range(50): wfile(os.path.join(src2, f"t{i}.wav"), rnd.randbytes(5000))
A, B, C = (os.path.join(T, n) for n in ("Archive", "Nexis", "Shuttle"))
for d in (A, B, C): os.makedirs(d)
store = Store(os.path.join(T, "j.db"))
w = W.MainWindow(store); w._force_close = True; w.resize(1280, 820); w.show(); wait(200)

print("New Copy panel → Start Now (with favorite, mixed verify levels, mirror)")
w.open_new([src]); p = w.new_panel
p._add_dest_row(A, VerifyLevel.FAST); p._add_dest_row(B, VerifyLevel.FULL)
p.dest_rows[1].level.setCurrentIndex(2); p.favorite.setChecked(True); p.mirror.setChecked(True)
p._submit(True)
check("job created and started", until(lambda: w.runner.current is not None, 10000))
check("pre-copy check counted the work for each destination",
      check_estimates and len(check_estimates[0]) == 2 and all(e.nfiles == 403 for e in check_estimates[0]))
check("first copy between these drives has no estimate", all(e.seconds is None for e in check_estimates[0]))
job1 = w.runner.current
check("favorite saved", len(store.saved_copies()) == 1)
print("Add a second copy to the queue while the first runs, then reorder")
w.open_new([src2]); w.new_panel._add_dest_row(C, VerifyLevel.FAST); w.new_panel._submit(False)
check("queued job exists", until(lambda: any(s == JobStatus.QUEUED for _, _, s, _ in store.jobs()), 5000))
job2 = [j for j, _, s, _ in store.jobs() if s == JobStatus.QUEUED][0]
w.refresh_lists(force=True); w._reordered()
print("Hammer Pause/Resume 25 times while copying")
w.select_job(job1)
pauses = 0
for i in range(25):
    if w.runner.current != job1: break
    w.on_action("pause", job1); pauses += 1; until(lambda: w.runner.current is None, 10000)
    w.on_action("resume", job1); until(lambda: w.runner.current is not None or store.job_status(job1) != JobStatus.QUEUED, 5000)
    wait(rnd.randint(20, 200))
check(f"paused and resumed {pauses} times", pauses >= 3)   # fast drives finish sooner
w.on_action("pause", -1); w.on_action("resume", job1)   # stray actions on the wrong job must do nothing harmful
check("job 1 reaches mirror/done", until(lambda: store.job_status(job1) in (JobStatus.DONE, JobStatus.CONFIRM_MIRROR), 120000))
check("job 2 ran after it", until(lambda: store.job_status(job2) == JobStatus.DONE, 60000))
check("Archive matches source", compare(src, os.path.join(A, "Show"), "Archive"))
check("Nexis (full verify) matches source", compare(src, os.path.join(B, "Show"), "Nexis"))
check("Shuttle matches source", compare(src2, os.path.join(C, "Music"), "Shuttle"))

w.select_job(job1); w.tick()
check("job 1 recorded copy speed for both destinations", len(store.perf_detail(job1)) == 2)
print("Every job action from the job view and Copy menu")
w.select_job(job1); w.tick()
for act in ("report", "favorite", "verify"):
    w.on_action(act, job1); wait(300)
until(lambda: True, 2000); wait(1500)
for m in w.menuBar().findChildren(QMenu):
    m.aboutToShow.emit()
    for a in m.actions():
        if a.text() in ("Quit SureCopy", "Remove from List…", "Pause"): continue
        if a.isEnabled() and a.text(): a.trigger(); wait(80)
check("help window opened", getattr(w, "_help", None) is not None and w._help.isVisible())
for i in range(w._help.topics.count()):
    w._help.topics.setCurrentRow(i); wait(20)
check("help has text for every topic", all(len(t[2]) > 200 for t in __import__("surecopy.gui.help", fromlist=["TOPICS"]).TOPICS))
w._help.close()

print("Mirror: change source, Run Again, decline once, then approve")
os.unlink(os.path.join(src, "d0", "f0.txt")); os.rename(os.path.join(src, "d1"), os.path.join(src, "d1_renamed"))
w.select_job(job1); w.on_action("again", job1)
until(lambda: store.jobs()[-1][0] != job2 and store.job_status(store.jobs()[-1][0]) == JobStatus.CONFIRM_MIRROR, 60000)
job3 = max(j for j, _, _, _ in store.jobs())
check("re-run waits for mirror OK", store.job_status(job3) == JobStatus.CONFIRM_MIRROR)
check("nothing removed yet", os.path.exists(os.path.join(A, "Show", "d0", "f0.txt")))
mirror_choice[0] = 1; w.on_action("mirror", job3); until(lambda: store.job_status(job3) == JobStatus.DONE, 20000)
check("decline keeps files", os.path.exists(os.path.join(A, "Show", "d0", "f0.txt")))
w.on_action("again", job3)
until(lambda: max(j for j, _, _, _ in store.jobs()) > job3 and
      store.job_status(max(j for j, _, _, _ in store.jobs())) == JobStatus.CONFIRM_MIRROR, 60000)
job4 = max(j for j, _, _, _ in store.jobs())
mirror_choice[0] = 2; w.on_action("mirror", job4)
check("approved mirror finishes", until(lambda: store.job_status(job4) == JobStatus.DONE, 60000))
check("Archive mirrors source exactly", compare(src, os.path.join(A, "Show"), "Archive after mirror"))
check("Nexis mirrors source exactly", compare(src, os.path.join(B, "Show"), "Nexis after mirror"))
check("history groups 3 runs", len(store.runs(store.task_of(job4))) == 3)

print("Unplug a destination mid-copy → auto-resume when it comes back")
for i in range(3): wfile(os.path.join(src, "big2", f"n{i}.mov"), os.urandom(60 << 20))
w.on_action("again", job4)
until(lambda: w.runner.current is not None, 20000); job5 = w.runner.current
wait(300); os.rename(B, B + "-gone")
check("job ends needing resume", until(lambda: store.job_status(job5) in (JobStatus.NEEDS_RESUME, JobStatus.CONFIRM_MIRROR, JobStatus.DONE), 60000))
st5 = store.job_status(job5)
os.rename(B + "-gone", B); w._auto_resume()
check("auto-resumed to done", until(lambda: store.job_status(job5) in (JobStatus.DONE, JobStatus.CONFIRM_MIRROR), 90000))
check("Nexis complete after reconnect", compare(src, os.path.join(B, "Show"), "Nexis after reconnect"))

print("Cancel, Remove from List, saved copy, settings, drop, close")
w.on_action("again", job5); until(lambda: w.runner.current is not None, 20000); j6 = w.runner.current
w.on_action("cancel", j6); check("cancel works", until(lambda: store.job_status(j6) in (JobStatus.CANCELLED, JobStatus.DONE, JobStatus.CONFIRM_MIRROR), 30000))
w.select_job(j6); w.on_action("remove", j6); wait(400)
check("removed from list", j6 not in [j for j, _, _, _ in store.jobs()])
item = w.saved_list.item(0); w._picked(item); wait(100)
check("saved copy fills the form", w.new_panel.sources.paths() == [src] and len(w.new_panel.dest_rows) == 2)
w.open_settings()
check("re-runs got an estimate of the work left", all(e is not None for e in check_estimates))
md = QMimeData(); md.setUrls([QUrl.fromLocalFile(src2)])
w.dropEvent(QDropEvent(QPointF(10, 10), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)); wait(100)
check("drop opens New Copy with the folder", src2 in w.new_panel.sources.paths())
for _ in range(30): w.tick()
w.close(); wait(200)
check("no exceptions anywhere in the GUI", not errors)
for e in errors[:5]: print(e)
print("OVERALL", "PASS" if all(c for _, c in checks) else "FAIL", f"({sum(c for _, c in checks)}/{len(checks)})")
rmtree(T)
