"""Main window: saved copies, queue and history on the left; the selected job on the right."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..engine import human
from ..jobs import (QueueRunner, approve_mirror, check_job, decline_mirror, mirror_preview, plan_job, resume_job,
                    verify_later)
from ..models import JobStatus
from ..report import report_text
from ..store import Store
from . import theme
from ..estimate import estimate_job
from ..store import task_key
from .dialogs import CheckDialog, MirrorDialog, ReportDialog, SettingsDialog
from .help import ABOUT, HelpDialog
from .jobview import JobView
from .newcopy import NewCopyPanel
from .util import label, run_in_thread, section, when

ACTIVE = (JobStatus.RUNNING, JobStatus.QUEUED)
FINISHED = (JobStatus.DONE, JobStatus.DONE_WITH_ISSUES, JobStatus.CANCELLED)


class SideItem(QWidget):
    def __init__(self, title: str, sub: str, progress: float | None = None):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 7, 10, 7)
        v.setSpacing(2)
        self.title = label(title)
        self.title.setStyleSheet("font-weight: 550; background: transparent;")
        self.sub = label(sub, "Muted")
        self.sub.setStyleSheet("font-size: 11.5px; background: transparent;")
        self.sub.setTextFormat(Qt.RichText)
        v.addWidget(self.title)
        v.addWidget(self.sub)
        self.bar = None
        if progress is not None:
            self.bar = QProgressBar()
            self.bar.setRange(0, 1000)
            self.bar.setTextVisible(False)
            self.bar.setStyleSheet("QProgressBar { max-height: 3px; min-height: 3px; border-radius: 1px; }"
                                   "QProgressBar::chunk { border-radius: 1px; }")
            self.bar.setValue(int(progress * 1000))
            v.addSpacing(3)
            v.addWidget(self.bar)


class SideList(QListWidget):
    def __init__(self, reorder: bool = False):
        super().__init__()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if reorder:
            self.setDragDropMode(QAbstractItemView.InternalMove)
            self.setDefaultDropAction(Qt.MoveAction)

    def fit(self):
        h = sum(self.sizeHintForRow(i) + 2 for i in range(self.count()))
        self.setFixedHeight(max(h, 4))


class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.setWindowTitle("SureCopy")
        self.resize(1280, 820)
        self.setMinimumSize(1040, 640)
        self.setAcceptDrops(True)
        self._latest = None
        self._list_sig = None
        self.selected: tuple[str, int | None] = ("none", None)
        self.runner = QueueRunner(store, on_progress=self._on_progress)
        self._recover_after_crash()

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        top = QWidget()
        top.setObjectName("TopBar")
        top.setFixedHeight(52)
        th = QHBoxLayout(top)
        th.setContentsMargins(18, 0, 14, 0)
        name = label(f"Sure<span style='color:#5fb3ff'>Copy</span>", "AppName")
        name.setTextFormat(Qt.RichText)
        th.addWidget(name)
        th.addStretch()
        new = QPushButton("+ New Copy")
        new.setObjectName("Primary")
        new.clicked.connect(lambda: self.open_new())
        gear = QPushButton("⚙︎")
        gear.setToolTip("Settings")
        gear.clicked.connect(self.open_settings)
        help_btn = QPushButton("?")
        help_btn.setObjectName("Icon")
        help_btn.setToolTip("SureCopy Help")
        help_btn.clicked.connect(lambda: self.open_help())
        gear.setObjectName("Icon")
        th.addWidget(new)
        th.addSpacing(4)
        th.addWidget(help_btn)
        th.addWidget(gear)
        outer.addWidget(top)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(280)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 16, 12, 12)
        sv.setSpacing(6)
        self.saved_list, self.queue_list, self.done_list = SideList(), SideList(reorder=True), SideList()
        self.saved_empty = label("Save a copy as a favorite to run it again in one click.", "Muted", wrap=True)
        self.queue_empty = label("Nothing queued.", "Muted")
        self.done_empty = label("Finished copies show up here.", "Muted")
        for title, lst, empty in (("Saved copies", self.saved_list, self.saved_empty),
                                  ("Queue", self.queue_list, self.queue_empty),
                                  ("Finished", self.done_list, self.done_empty)):
            sv.addWidget(section(title))
            for w in (empty, lst):
                w.setStyleSheet(w.styleSheet() + "margin-left: 2px;")
            empty.setStyleSheet("font-size: 12px; margin: 0 8px 4px 8px; background: transparent;")
            sv.addWidget(empty)
            sv.addWidget(lst)
            sv.addSpacing(12)
            lst.itemClicked.connect(self._picked)
        sv.addStretch()
        self.queue_list.model().rowsMoved.connect(self._reordered)
        body.addWidget(side)

        self.stack = QStackedWidget()
        self.empty = self._empty_state()
        self.job_view = JobView(store)
        self.job_view.action.connect(self.on_action)
        self.new_panel = NewCopyPanel(store)
        self.new_panel.submit.connect(self.submit)
        self.new_panel.delete_saved.connect(self._delete_saved)
        for w in (self.empty, self.job_view, self.new_panel):
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)

        self._build_menus()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(300)
        self.reconnect_timer = QTimer(self)
        self.reconnect_timer.timeout.connect(self._auto_resume)
        self.reconnect_timer.start(3000)
        self.refresh_lists(force=True)
        jobs = [j for j in store.jobs()]
        if jobs:
            self.select_job(jobs[-1][0])

    # ---- menus --------------------------------------------------------------

    def _build_menus(self):
        bar = self.menuBar()

        def act(menu, text, fn, shortcut=None, role=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if role is not None:
                a.setMenuRole(role)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        f = bar.addMenu("&File")
        act(f, "New Copy", lambda: self.open_new(), QKeySequence.New)
        act(f, "Settings…", self.open_settings, QKeySequence.Preferences, QAction.PreferencesRole)
        f.addSeparator()
        act(f, "Quit SureCopy", self.close, QKeySequence.Quit, QAction.QuitRole)

        j = bar.addMenu("&Copy")
        self.job_actions = [
            act(j, "Pause", lambda: self._job_act("pause"), "Ctrl+."),
            act(j, "Resume", lambda: self._job_act("resume")),
            act(j, "Run Again", lambda: self._job_act("again"), "Ctrl+R"),
        ]
        j.addSeparator()
        self.job_actions += [
            act(j, "View Report", lambda: self._job_act("report"), "Ctrl+I"),
            act(j, "Verify Later", lambda: self._job_act("verify")),
            act(j, "Save as Favorite", lambda: self._job_act("favorite")),
        ]
        j.addSeparator()
        self.job_actions.append(act(j, "Remove from List…", lambda: self._job_act("remove")))
        j.aboutToShow.connect(self._update_job_menu)

        h = bar.addMenu("&Help")
        act(h, "SureCopy Help", lambda: self.open_help(), QKeySequence.HelpContents)
        h.addSeparator()
        for key, title in (("verify", "Verify Levels"), ("sync", "Syncing and Existing Files"),
                           ("speed", "Time Estimates and Speed"),
                           ("mirror", "Mirror Mode"), ("resume", "Pause, Resume and Disconnects"),
                           ("history", "Favorites and History"), ("trouble", "Troubleshooting"),
                           ("keys", "Keyboard Shortcuts")):
            act(h, title, lambda _=False, k=key: self.open_help(k))
        h.addSeparator()
        act(h, "About SureCopy", lambda: QMessageBox.about(self, "About SureCopy", ABOUT),
            role=QAction.AboutRole)

    def _job_act(self, act: str):
        if self.selected[0] == "job" and self.selected[1] is not None:
            self.on_action(act, self.selected[1])

    def _update_job_menu(self):
        has_job = self.selected[0] == "job"
        for a in self.job_actions:
            a.setEnabled(has_job)
        if has_job:
            running = self.runner.current == self.selected[1]
            status = self.store.job_status(self.selected[1])
            self.job_actions[0].setEnabled(running)
            self.job_actions[1].setEnabled(not running and status in (
                JobStatus.PAUSED, JobStatus.NEEDS_RESUME, JobStatus.DONE_WITH_ISSUES, JobStatus.CANCELLED))
            self.job_actions[-1].setEnabled(not running)

    def open_help(self, topic: str = "start"):
        dlg = getattr(self, "_help", None)
        if dlg is None:
            dlg = self._help = HelpDialog(self, topic)
        else:
            dlg.show_topic(topic)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ---- layout pieces ------------------------------------------------------

    def _empty_state(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.addStretch()
        t = label("Drop folders here to start a copy", "Title")
        t.setAlignment(Qt.AlignCenter)
        s = label("Or click + New Copy. Pick one or more sources, choose where they go, and SureCopy keeps "
                  "track of everything, even if you stop halfway.", "Sub", wrap=True)
        s.setAlignment(Qt.AlignCenter)
        s.setMaximumWidth(520)
        v.addWidget(t)
        h = QHBoxLayout()
        h.addStretch()
        h.addWidget(s)
        h.addStretch()
        v.addLayout(h)
        v.addStretch()
        return w

    # ---- engine callbacks (called on the engine's thread) ---------------------

    def _on_progress(self, p):
        self._latest = p

    def _recover_after_crash(self):
        for job_id, _, status, _ in self.store.jobs():
            if status == JobStatus.RUNNING:
                self.store.set_job_status(job_id, JobStatus.PAUSED)
                self.store.log(job_id, "warn", "SureCopy closed during this copy; progress was kept")

    # ---- periodic refresh ------------------------------------------------------

    def tick(self):
        self.refresh_lists()
        current = self.runner.current
        kind, sel = self.selected
        if kind == "job" and sel is not None:
            try:
                p = self._latest if (self._latest and self._latest.job_id == sel) else None
                self.job_view.refresh(p, running=(current == sel))
            except IndexError:   # job was removed
                self.selected = ("none", None)
                self.stack.setCurrentWidget(self.empty)
        if current is not None:
            self._update_running_item(current)

    def _job_rows(self):
        return self.store.jobs()

    def refresh_lists(self, force: bool = False):
        jobs = self._job_rows()
        saved = self.store.saved_copies()
        current = self.runner.current
        sig = (tuple((j, s, p) for j, _, s, p in jobs), tuple((i, n, lr) for i, n, _, lr in saved), current)
        if sig == self._list_sig and not force:
            return
        self._list_sig = sig
        kind, sel = self.selected

        self.saved_list.clear()
        for sid, name, spec, last in saved:
            dests = " + ".join(os.path.basename(d.root.rstrip("/")) or d.root for d in spec.destinations)
            runs = self.store.runs(task_key(spec))
            last_ts = next((r[5] or r[4] or r[3] for r in runs), None)
            sub = f"Last run {when(last_ts)}" if last_ts else "Not run yet"
            if spec.mirror:
                sub = "Mirror on · " + sub
            self._add_item(self.saved_list, ("saved", sid), f"★ {name}", sub, tooltip=f"To: {dests}")

        self.queue_list.clear()
        self.done_list.clear()
        self._running_widget = None
        queued = [j for j in jobs if j[2] in ACTIVE or j[0] == current or j[2] in
                  (JobStatus.PAUSED, JobStatus.NEEDS_RESUME, JobStatus.CONFIRM_MIRROR)]
        queued.sort(key=lambda j: (j[0] != current, j[3]))
        for job_id, name, status, _ in queued:
            if job_id == current:
                w = self._add_item(self.queue_list, ("job", job_id), name,
                                   f"<span style='color:{theme.ACCENT_LIGHT}'>●</span> Copying", progress=0.0)
                self._running_widget = w
            else:
                sub = {JobStatus.QUEUED: "○ Queued", JobStatus.PAUSED: "❚❚ Paused",
                       JobStatus.NEEDS_RESUME: f"<span style='color:{theme.AMBER}'>⚠</span> Needs resume",
                       JobStatus.CONFIRM_MIRROR: f"<span style='color:{theme.RED}'>⚠</span> Waiting for mirror OK",
                       JobStatus.RUNNING: "Starting…"}.get(status, status.value)
                files, total = self.store.totals(job_id)
                self._add_item(self.queue_list, ("job", job_id), name, f"{sub} · {human(total)}")
        # One row per copy: its latest finished run, when it ran, and how many runs it has.
        self._task_rows = {}
        for key, _, _ in self.store.tasks():
            runs = self.store.runs(key)
            done = [r for r in runs if r[2] in FINISHED and r[0] != current]
            if not done:
                continue
            job_id, name, status, created, started, ended = done[0]
            if status == JobStatus.DONE:
                mark = f"<span style='color:{theme.GREEN}'>✓</span>"
            elif status == JobStatus.CANCELLED:
                mark = f"<span style='color:{theme.MUTED}'>–</span>"
            else:
                mark = f"<span style='color:{theme.AMBER}'>⚠</span>"
            sub = f"{mark} {when(ended or created)}"
            if len(runs) > 1:
                sub += f" · {len(runs)} runs"
            self._task_rows[key] = job_id
            self._add_item(self.done_list, ("task", key), name, sub)

        for lst, empty in ((self.saved_list, self.saved_empty), (self.queue_list, self.queue_empty),
                           (self.done_list, self.done_empty)):
            lst.fit()
            lst.setVisible(lst.count() > 0)
            empty.setVisible(lst.count() == 0)
        self._restore_selection()

    def _add_item(self, lst, key, title, sub, progress=None, tooltip=None):
        item = QListWidgetItem()
        item.setData(Qt.UserRole, key)
        w = SideItem(title, sub, progress)
        if tooltip:
            w.setToolTip(tooltip)
        item.setSizeHint(w.sizeHint())
        lst.addItem(item)
        lst.setItemWidget(item, w)
        if key[0] == "job" and key[1] != self.runner.current and lst is self.queue_list:
            item.setFlags(item.flags() | Qt.ItemIsDragEnabled)
        elif lst is self.queue_list:
            item.setFlags(item.flags() & ~Qt.ItemIsDragEnabled)
        return w

    def _update_running_item(self, job_id):
        w = getattr(self, "_running_widget", None)
        if not w or not w.bar:
            return
        p = self._latest if (self._latest and self._latest.job_id == job_id) else None
        if p and p.total_bytes:
            ok = {d.root for d in self.store.dests(job_id) if d.state not in ("disconnected", "failed")}
            vals = [b for r, b in p.dest_bytes.items() if r in ok] or list(p.dest_bytes.values())
            frac = min(vals) / p.total_bytes if vals else 0
            w.bar.setValue(int(frac * 1000))
            w.sub.setText(f"<span style='color:{theme.ACCENT_LIGHT}'>●</span> Copying · {frac * 100:.0f}%")

    def _restore_selection(self):
        sel_task = None
        if self.selected[0] == "job" and self.selected[1] is not None:
            try:
                sel_task = ("task", self.store.task_of(self.selected[1]))
            except IndexError:
                pass
        for lst in (self.saved_list, self.queue_list, self.done_list):
            lst.blockSignals(True)
            lst.clearSelection()
            for i in range(lst.count()):
                key = lst.item(i).data(Qt.UserRole)
                if key == self.selected or (lst is self.done_list and key == sel_task
                                            and not self._in_queue(self.selected[1])):
                    lst.item(i).setSelected(True)
            lst.blockSignals(False)

    def _in_queue(self, job_id) -> bool:
        return any(self.queue_list.item(i).data(Qt.UserRole) == ("job", job_id)
                   for i in range(self.queue_list.count()))

    def _picked(self, item):
        kind, ident = item.data(Qt.UserRole)
        if kind == "task":
            self.select_job(self._task_rows[ident])
            return
        if kind == "saved":
            self.selected = ("saved", ident)
            self.new_panel.reset(self.store.saved_spec(ident), saved_id=ident)
            self.stack.setCurrentWidget(self.new_panel)
        else:
            self.select_job(ident)
        self._restore_selection()

    def _reordered(self, *_):
        ids = [self.queue_list.item(i).data(Qt.UserRole)[1] for i in range(self.queue_list.count())]
        ids = [i for i in ids if i != self.runner.current]
        rows = {j: p for j, _, _, p in self.store.jobs()}
        slots = sorted(rows[i] for i in ids)
        with self.store.tx() as db:
            db.executemany("UPDATE jobs SET position=? WHERE id=?", list(zip(slots, ids)))
        self._list_sig = None

    def select_job(self, job_id: int):
        self.selected = ("job", job_id)
        self.job_view.show_job(job_id)
        self.stack.setCurrentWidget(self.job_view)
        self._restore_selection()

    def open_new(self, sources: list[str] | None = None):
        self.selected = ("new", None)
        self.new_panel.reset()
        if sources:
            self.new_panel.sources.add_paths(sources)
        self.stack.setCurrentWidget(self.new_panel)
        self._restore_selection()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if self.stack.currentWidget() is self.new_panel:
            self.new_panel.sources.add_paths(paths)
        else:
            self.open_new(paths)

    # ---- creating jobs ------------------------------------------------------------

    def submit(self, spec, start: bool, favorite: bool):
        saved_id = self.new_panel.saved_id
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("Checking sources…")

        def done(payload):
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
            if isinstance(payload, Exception):
                QMessageBox.critical(self, "SureCopy", f"Couldn't read the sources: {payload}")
                return
            result, estimates = payload
            if CheckDialog(self, spec, result, start, estimates).exec() != CheckDialog.Accepted:
                return
            try:
                job_id, result2 = plan_job(self.store, spec, result)
            except Exception as exc:   # e.g. the disk holding SureCopy's job history is full
                QMessageBox.critical(self, "SureCopy", f"Couldn't save this copy to SureCopy's job history:\n{exc}\n\n"
                                     f"History is kept in {self.store.path}. Free up space on that drive and try again.")
                return
            if job_id is None:
                return
            if favorite:
                self.store.save_copy(spec)
            if saved_id:
                self.store.touch_saved(saved_id)
            if start:
                self.store.move_job(job_id, 0)
                self.runner.start()
            self._list_sig = None
            self.select_job(job_id)
        def work():
            result = check_job(spec)
            try:
                estimates = [] if result.problems else estimate_job(self.store, spec, result.files)
            except OSError:
                estimates = []
            return result, estimates
        run_in_thread(work, done)

    def _delete_saved(self, saved_id):
        if QMessageBox.question(self, "SureCopy", "Delete this favorite? Jobs and files aren't affected.") \
                == QMessageBox.Yes:
            self.store.delete_saved(saved_id)
            self.selected = ("none", None)
            self.stack.setCurrentWidget(self.empty)
            self._list_sig = None

    # ---- job actions -----------------------------------------------------------------

    def on_action(self, act: str, job_id: int):
        store = self.store
        if act == "open_run":
            self.select_job(job_id)
            return
        if act == "pause":
            self.runner.stop_all(job_id)   # only ever pauses this job, checked under the runner's lock
        elif act == "start":
            self.runner.start()
        elif act in ("resume", "retry"):
            if self.runner.current != job_id and store.job_status(job_id) in (
                    JobStatus.PAUSED, JobStatus.NEEDS_RESUME, JobStatus.DONE_WITH_ISSUES, JobStatus.CANCELLED):
                resume_job(store, job_id)
            self.runner.start()
        elif act == "again":
            spec = store.spec(job_id)
            self.new_panel.saved_id = None
            self.submit(spec, True, False)
        elif act == "cancel":
            if QMessageBox.question(
                    self, "Cancel this copy?",
                    "Files that finished copying stay on the destinations. Partly copied files are removed, "
                    "so starting over copies them from the beginning.") == QMessageBox.Yes:
                self.runner.cancel_current(job_id)
        elif act == "remove":
            if QMessageBox.question(self, "Remove from list?",
                                    "This removes the job and its report from SureCopy. Files on your drives "
                                    "aren't touched.") == QMessageBox.Yes:
                store.delete_job(job_id)
                self.selected = ("none", None)
                self.job_view.job_id = None
                self.stack.setCurrentWidget(self.empty)
                self._list_sig = None
        elif act == "report":
            ReportDialog(self, store.job_info(job_id)[0], report_text(store, job_id)).exec()
        elif act == "favorite":
            store.save_copy(store.spec(job_id))
            self._list_sig = None
            self.statusBar().showMessage("Saved as a favorite", 4000)
        elif act == "verify":
            self._verify(job_id)
        elif act == "mirror":
            self._review_mirror(job_id)

    def _verify(self, job_id):
        self.statusBar().showMessage("Verifying: reading every finished copy back…")

        def done(counts):
            self.statusBar().clearMessage()
            if isinstance(counts, Exception):
                QMessageBox.critical(self, "Verify later", str(counts))
                return
            bad = counts["mismatch"] + counts["missing"]
            msg = (f"{counts['verified']:,} copies match the source exactly.\n"
                   f"{counts['mismatch']:,} don't match. {counts['missing']:,} are missing.\n"
                   f"{counts['source_changed']:,} couldn't be checked because the source changed.")
            (QMessageBox.warning if bad else QMessageBox.information)(self, "Verify later", msg)
            self._list_sig = None
        run_in_thread(lambda: verify_later(self.store, job_id), done)

    def _review_mirror(self, job_id):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("Building the mirror list…")

        def done(previews):
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
            if isinstance(previews, Exception):
                QMessageBox.critical(self, "Mirror mode", str(previews))
                return
            if not previews:
                decline_mirror(self.store, job_id)
                QMessageBox.information(self, "Mirror mode", "Nothing needs to be removed.")
                return
            choice = MirrorDialog(self, self.store.spec(job_id), previews).exec()
            if choice == 2:
                approve_mirror(self.store, job_id)
                self.runner.start()
            elif choice == 1:
                decline_mirror(self.store, job_id)
            self._list_sig = None
        run_in_thread(lambda: mirror_preview(self.store, job_id), done)

    def _auto_resume(self):
        if not QSettings().value("auto_resume", True, type=bool):
            return
        for job_id, _, status, _ in self.store.jobs():
            if status != JobStatus.NEEDS_RESUME:
                continue
            gone = [d for d in self.store.dests(job_id) if d.state == "disconnected"]
            if gone and any(os.path.isdir(d.root) for d in gone):
                self.store.log(job_id, "info", "A disconnected destination is back; resuming")
                resume_job(self.store, job_id)
                self.runner.start()

    def open_settings(self):
        SettingsDialog(self, self.store.path).exec()

    def closeEvent(self, e):
        if self.runner.current is not None and not getattr(self, "_force_close", False):
            answer = QMessageBox.question(
                self, "A copy is running",
                "Pause the copy and quit? Progress is saved, and you can resume it next time.")
            if answer != QMessageBox.Yes:
                e.ignore()
                return
        self.runner.stop_all()
        self.runner.wait(20)
        self.timer.stop()
        self.reconnect_timer.stop()
        e.accept()
