"""The right-hand view of one job: progress, destinations, issues, actions."""

from __future__ import annotations

import collections
import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .. import estimate
from ..drives import sources_key, volume_for
from ..engine import human
from ..models import ExistingPolicy, JobStatus, Progress
from ..scan import EXCLUDE_PRESETS
from ..store import Store
from . import theme
from .util import (card, clear_layout, duration, free_space, label, rate, section, took, volume_name, when,
                   ElidedLabel, FlowRow)

LEVEL_SHORT = {"fast": "Fast", "source": "Source hash", "full": "Full verify"}


class DestinationCard(QWidget):
    def __init__(self, color: str):
        super().__init__()
        self.frame, lay = card(spacing=6)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)
        top = QHBoxLayout()
        names = QVBoxLayout()
        names.setSpacing(1)
        self.name = label("", None)
        self.name.setStyleSheet("font-weight: 650; font-size: 14px;")
        self.path = ElidedLabel("", "Muted")
        self.path.setStyleSheet("font-size: 11.5px;")
        names.addWidget(self.name)
        names.addWidget(self.path)
        top.addLayout(names, 1)
        self.level = label("", "Chip")
        top.addWidget(self.level, 0, Qt.AlignTop)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 1000)
        self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {color}; }}")
        lay.addSpacing(4)
        lay.addWidget(self.bar)
        stats = QHBoxLayout()
        self.stats = label("", "Muted")
        self.stats.setStyleSheet("font-size: 12px;")
        self.speed = label("")
        self.speed.setStyleSheet("font-weight: 650; font-size: 12px;")
        stats.addWidget(self.stats, 1)
        stats.addWidget(self.speed)
        lay.addLayout(stats)
        self.usual = label("", "Muted")
        self.usual.setStyleSheet("font-size: 12px;")
        lay.addWidget(self.usual)
        self.banner = label("", "Banner", wrap=True)
        lay.addWidget(self.banner)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def set_usual(self, text: str, color: str, tip: str) -> None:
        self.usual.setText(text)
        self.usual.setStyleSheet(f"font-size: 12px; color: {color};")
        self.usual.setToolTip(tip)
        self.usual.setVisible(bool(text))

    def update(self, root, level, done, total, counts, speed, state, message, running):
        self.name.setText(volume_name(root))
        free = free_space(root)
        self.path.set_full(root + (f" · {human(free)} free" if free is not None else ""))
        self.level.setText(LEVEL_SHORT.get(level, level))
        self.bar.setValue(int(1000 * done / total) if total else 1000)
        bits = []
        for key, word in (("copied", "copied"), ("updated", "updated"), ("skipped", "already there"),
                          ("kept_newer", "kept newer")):
            if counts.get(key):
                bits.append(f"{counts[key]:,} {word}")
        problems = counts.get("failed", 0) + counts.get("conflict", 0)
        if problems:
            bits.append(f"<span style='color:{theme.AMBER}'>{problems:,} issue{'s' * (problems != 1)}</span>")
        self.stats.setText(" · ".join(bits) if bits else ("Starting…" if running else "Not started"))
        self.speed.setText(rate(speed) if running and speed > 0 and state not in ("disconnected", "failed") else "")
        if state == "disconnected":
            self.banner.setObjectName("Banner")
            self.banner.setText("⚠ Disconnected. Picks up where it stopped when you plug it back in.")
        elif state == "failed":
            self.banner.setObjectName("BannerRed")
            self.banner.setText(f"✕ {message}")
        else:
            self.banner.setText("")
        self.banner.style().unpolish(self.banner)   # re-apply the stylesheet after the name change
        self.banner.style().polish(self.banner)
        self.banner.setVisible(bool(self.banner.text()))


class JobView(QWidget):
    action = Signal(str, int)   # (action name, job id)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.job_id: int | None = None
        self.cards: dict[int, DestinationCard] = {}
        self.speeds: dict[int, collections.deque] = {}
        self._issue_sig = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        v = QVBoxLayout(body)
        v.setContentsMargins(28, 22, 28, 18)
        v.setSpacing(14)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title = ElidedLabel("", "Title")
        self.title.setMinimumWidth(200)
        self.sub = label("", "Sub")
        self.when = label("", "Muted")
        self.when.setStyleSheet("font-size: 12px;")
        titles.addWidget(self.title)
        titles.addWidget(self.sub)
        titles.addWidget(self.when)
        head.addLayout(titles, 1)
        self.pill = label("", "Pill")
        head.addWidget(self.pill, 0, Qt.AlignTop)
        head.addSpacing(16)
        self.mirror = label("", "Muted")
        head.addWidget(self.mirror, 0, Qt.AlignTop)
        v.addLayout(head)

        self.overall = QProgressBar()
        self.overall.setTextVisible(False)
        self.overall.setRange(0, 1000)
        self.overall.setStyleSheet("QProgressBar { max-height: 6px; min-height: 6px; border-radius: 3px; }"
                                   "QProgressBar::chunk { border-radius: 3px; background: qlineargradient("
                                   "x1:0, y1:0, x2:1, y2:0, stop:0 #2f80ed, stop:1 #4fb6ff); }")
        v.addWidget(self.overall)

        row = QHBoxLayout()
        row.setSpacing(16)
        src_card, self.src_lay = card()
        self.src_lay.addWidget(section("Sources"))
        self.src_list = QVBoxLayout()
        self.src_lay.addLayout(self.src_list)
        set_card, set_lay = card()
        set_lay.addWidget(section("Settings"))
        self.chips = FlowRow()
        set_lay.addWidget(self.chips)
        row.addWidget(src_card, 5)
        row.addWidget(set_card, 4)
        v.addLayout(row)

        v.addWidget(section("Destinations"))
        self.dest_grid = QGridLayout()
        self.dest_grid.setSpacing(16)
        self.dest_grid.setAlignment(Qt.AlignTop)
        v.addLayout(self.dest_grid)

        self.issue_card, il = card()
        ih = QHBoxLayout()
        self.issue_title = section("Issues")
        ih.addWidget(self.issue_title)
        ih.addStretch()
        self.retry = QPushButton("Retry all")
        self.retry.setObjectName("Link")
        self.retry.clicked.connect(lambda: self.action.emit("retry", self.job_id))
        ih.addWidget(self.retry)
        il.addLayout(ih)
        self.issue_list = QListWidget()
        self.issue_list.setStyleSheet("QListWidget { background: transparent; } QListWidget::item { padding: 5px 0; "
                                      "border-bottom: 1px solid #2c2e35; border-radius: 0; }")
        self.issue_list.setMinimumHeight(60)
        self.issue_list.setMaximumHeight(200)
        il.addWidget(self.issue_list)
        v.addWidget(self.issue_card)

        self.history_card, hl = card()
        hh = QHBoxLayout()
        self.history_title = section("History")
        hh.addWidget(self.history_title)
        hh.addStretch()
        hh.addWidget(label("Click a run to open it", "Muted"))
        hl.addLayout(hh)
        self.history = QListWidget()
        self.history.setStyleSheet("QListWidget { background: transparent; } QListWidget::item { border-radius: 7px; }")
        self.history.itemClicked.connect(
            lambda item: self.action.emit("open_run", item.data(Qt.UserRole)))
        hl.addWidget(self.history)
        v.addWidget(self.history_card)
        from PySide6.QtWidgets import QSizePolicy
        for c in (self.issue_card, self.history_card, src_card, set_card):
            c.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)   # extra room goes below, not inside
        v.addStretch(1)

        foot = QHBoxLayout()
        self.primary = QPushButton()
        self.primary.setObjectName("Big")
        self.primary.clicked.connect(lambda: self.action.emit(self.primary.property("act"), self.job_id))
        self.secondary = QPushButton()
        self.secondary.setObjectName("Secondary")
        self.secondary.clicked.connect(lambda: self.action.emit(self.secondary.property("act"), self.job_id))
        foot.addWidget(self.primary)
        foot.addWidget(self.secondary)
        foot.addStretch()
        self.links = {}
        for act, text in (("verify", "Verify later"), ("report", "View report"), ("favorite", "Save as favorite")):
            b = QPushButton(text)
            b.setObjectName("Link")
            b.clicked.connect(lambda _=False, a=act: self.action.emit(a, self.job_id))
            foot.addWidget(b)
            self.links[act] = b
        outer.addLayout(self._padded(foot))

    @staticmethod
    def _padded(layout):
        wrap = QVBoxLayout()
        wrap.setContentsMargins(28, 0, 28, 20)
        wrap.addLayout(layout)
        return wrap

    # ---------------------------------------------------------------------

    def show_job(self, job_id: int) -> None:
        if job_id != self.job_id:
            self.job_id = job_id
            self.speeds.clear()
            self._issue_sig = None
            for c in self.cards.values():
                c.setParent(None)
            self.cards.clear()
            spec = self.store.spec(job_id)
            clear_layout(self.src_list)
            for s in spec.sources:
                row = QHBoxLayout()
                icon = label("📁" if os.path.isdir(s) else "📄")
                row.addWidget(icon)
                row.addWidget(ElidedLabel(s), 1)
                self.src_list.addLayout(row)
            chips = [label(f"Existing files: {spec.on_existing.label.lower()}", "Chip")]
            for key, (name, pats) in EXCLUDE_PRESETS.items():
                if set(pats) <= set(spec.excludes):
                    chips.append(label(f"Skipping {name.lower()}", "Chip"))
            custom = [p for p in spec.excludes if not any(p in v[1] for v in EXCLUDE_PRESETS.values())]
            if custom:
                chips.append(label("Also skipping: " + ", ".join(custom), "Chip"))
            warnings = self.store.warnings(job_id)
            if warnings:
                chips.append(label(f"{len(warnings)} name warning{'s' * (len(warnings) != 1)}", "ChipWarn"))
                chips[-1].setToolTip("\n".join(warnings[:30]))
            if spec.mirror:
                chips.append(label("Mirror: delete permanently" if spec.mirror_delete
                                   else "Mirror: move removed files aside", "ChipDanger"))
            self.chips.set_items(chips)
            self._history_sig = None
            self._src_key = sources_key(spec.sources)
            self._models = {}
            self.mirror.setText("Mirror mode: On" if spec.mirror else "Mirror mode: Off")
            self.mirror.setStyleSheet(f"color: {'#ffb3ad' if spec.mirror else theme.MUTED};")
        self.refresh(None, running=False)

    def refresh(self, progress: Progress | None, running: bool) -> None:
        if self.job_id is None:
            return
        job_id = self.job_id
        name, status, created, finished = self.store.job_info(job_id)
        if running:
            status = JobStatus.RUNNING
        files, total = self.store.totals(job_id)
        dests = self.store.dests(job_id)
        counts = self.store.dest_counts(job_id)
        saved = self.store.dest_bytes(job_id)
        live = progress.dest_bytes if (progress and running) else {}
        now = time.monotonic()

        per_dest = {}
        for d in dests:
            done = live.get(d.root, saved.get(d.id, 0))
            per_dest[d.id] = done
            q = self.speeds.setdefault(d.id, collections.deque(maxlen=30))
            q.append((now, done))

        def speed(dest_id):
            q = self.speeds.get(dest_id)
            if not q or len(q) < 2:
                return 0.0
            (t0, b0), (t1, b1) = q[0], q[-1]
            return max(0.0, (b1 - b0) / (t1 - t0)) if t1 > t0 else 0.0

        active = [d for d in dests if d.state not in ("disconnected", "failed")] or dests
        slowest = min((per_dest[d.id] for d in active), default=0)
        self.title.set_full(name)
        done_files = progress.files_done if (progress and running) else self.store.files_finished(job_id)
        sub = f"{min(done_files, files):,} of {files:,} files · {human(slowest)} of {human(total)}"
        if running:
            left = self._time_left(job_id, active, per_dest, total, files, counts, speed)
            if left is not None:
                sub += f" · {duration(left)} left"
        self.sub.setText(sub)
        created, started, ended = self.store.job_times(job_id)
        if running:
            self.when.setText(f"Started {when(started)}")
        elif ended:
            self.when.setText(f"Finished {when(ended)}" + (f" · took {took(started, ended)}" if started else ""))
        elif started:
            self.when.setText(f"Started {when(started)}")
        else:
            self.when.setText(f"Added {when(created)}")
        self._refresh_history(job_id, status)
        text, bg, fg = theme.STATUS_STYLE.get(status.value, (status.value.upper(), "#2c2e35", theme.TEXT))
        self.pill.setText(text)
        self.pill.setStyleSheet(f"background: {bg}; color: {fg};")
        self.overall.setValue(int(1000 * slowest / total) if total else (1000 if status != JobStatus.QUEUED else 0))

        for i, d in enumerate(dests):
            c = self.cards.get(d.id)
            if c is None:
                c = DestinationCard(theme.BAR_COLORS[i % len(theme.BAR_COLORS)])
                self.cards[d.id] = c
                self.dest_grid.addWidget(c, i // 3, i % 3)
            c.update(d.root, d.verify.value, per_dest[d.id], total, counts.get(d.id, {}), speed(d.id),
                     d.state, d.message, running)
            live_perf = progress.perf.get(d.root) if (progress and running and progress.perf) else None
            c.set_usual(*self._usual_text(job_id, d, live_perf if running else None, running))

        n_issues = self.store.issue_count(job_id)
        self.issue_card.setVisible(n_issues > 0)
        if n_issues:
            sig = (n_issues, status)
            if sig != self._issue_sig:
                self._issue_sig = sig
                roots = {d.id: volume_name(d.root) for d in dests}
                self.issue_title.setText(f"ISSUES ({n_issues:,})")
                self.issue_list.clear()
                for rel, dest_id, st, msg in self.store.issues(job_id, 300):
                    mark = "⚠" if st == "conflict" else "✕"
                    self.issue_list.addItem(f"{mark}  {rel}    {roots.get(dest_id, '')} · {msg}")
                if n_issues > 300:
                    self.issue_list.addItem(f"…and {n_issues - 300:,} more (see the report)")
                rows = self.issue_list.count()
                self.issue_list.setFixedHeight(min(200, max(1, rows) * 30 + 6))
            self.retry.setVisible(status != JobStatus.RUNNING)

        self._set_actions(status)

    # ---- speed history: how fast copies between these two drives usually go ------------------

    def _model(self, job_id: int, d, before: float | None = None, keys=None):
        """Model from past copies between this copy's source drive and d's drive (this run excluded)."""
        if keys is None:
            vol = volume_for(d.root)
            keys = (getattr(self, "_src_key", None), vol.key if vol else None, d.verify.value == "full")
        cache_key = (job_id, d.id, before, keys)
        models = getattr(self, "_models", None)
        if models is None:
            models = self._models = {}
        if cache_key not in models:
            models[cache_key] = estimate.model_for(self.store, *keys, exclude_job=job_id, before=before)
        return models[cache_key]

    def _time_left(self, job_id, active, per_dest, total, files, counts, speed) -> float | None:
        """Time left from speed history when there is some, otherwise from the current speed."""
        lefts = []
        for d in active:
            m = self._model(job_id, d)
            c = counts.get(d.id, {})
            rem_files = c.get("pending", 0) + c.get("partial", 0)
            rem_bytes = max(0, total - per_dest[d.id])
            done = c.get("copied", 0) + c.get("updated", 0)
            skipped = c.get("skipped", 0) + c.get("kept_newer", 0)
            if done + skipped >= 50:          # on a re-run most files are skipped; scale what's left to match
                share = (done + 1) / (done + skipped + 1)
                rem_files, rem_bytes = int(rem_files * share), int(rem_bytes * share)
            if m is not None:
                lefts.append(m.seconds(rem_bytes, rem_files))
            elif speed(d.id) > 0:
                lefts.append(rem_bytes / speed(d.id))
        return max(lefts) if lefts else None

    def _usual_text(self, job_id: int, d, live_perf, running) -> tuple[str, str, str]:
        """The line under each destination: how this copy compares with usual for these two drives."""
        if d.state in ("disconnected", "failed"):
            return ("", theme.FAINT, "")          # the banner says what's going on
        if running and (not live_perf or not (live_perf[0] or live_perf[1])):
            return ("", theme.FAINT, "")          # nothing copied to this drive yet in this pass
        if running:
            perf, model = live_perf, self._model(job_id, d)
        else:
            row = self.store.perf_detail(job_id).get(d.id)
            if not row:
                return ("", theme.FAINT, "")
            perf = row[:3]
            created = self.store.job_times(job_id)[0]
            model = self._model(job_id, d, before=created, keys=row[3:])
        if model is None:
            return (("First copy between these drives" if running else ""), theme.FAINT,
                    "SureCopy learns how fast copies between two drives usually go from the copies themselves. "
                    "After this one, it can estimate times and compare speeds.")
        tip = self._model_tip(model)
        if model.runs < estimate.MIN_RUNS_TO_JUDGE:
            return (("Learning these drives (1 past copy)" if running else ""), theme.FAINT,
                    tip + "\nSpeed is compared with usual from the third copy on; one copy isn't enough "
                          "to tell big-file speed from per-file time.")
        pace = estimate.pace(model, *perf) if perf else None
        if pace is None:
            return ("Comparing with usual speed…" if running else "", theme.FAINT, tip)
        tip += (f"\nExpected for this work: {took(0.001, 0.001 + model.seconds(perf[0], perf[1]))}"
                f" · actual: {took(0.001, 0.001 + perf[2])}")
        pct = pace * 100
        if pct > 115:
            return ("Faster than usual", theme.GREEN, tip)
        color = theme.GREEN if pct >= 85 else theme.AMBER if pct >= 60 else theme.RED
        return (f"{min(pct, 100):.0f}% of usual speed", color, tip)

    @staticmethod
    def _model_tip(m) -> str:
        parts = []
        if m.bytes_per_sec:
            parts.append(f"{rate(m.bytes_per_sec)} for big files")
        if m.sec_per_file > 0:
            parts.append(f"about {1 / m.sec_per_file:,.0f} small files/s")
        return (f"Usually {' and '.join(parts) or 'similar'} between these drives "
                f"(based on {m.runs} past cop{'ies' if m.runs != 1 else 'y'}).")

    def _refresh_history(self, job_id: int, status: JobStatus) -> None:
        runs = self.store.runs(self.store.task_of(job_id))
        sig = (job_id, tuple((r[0], r[2], r[5]) for r in runs), status)
        if sig == getattr(self, "_history_sig", None):
            return
        self._history_sig = sig
        n = len(runs)
        self.history_title.setText(f"HISTORY · {n} RUN{'S' * (n != 1)}")
        self.history.clear()
        for rid, name, st, created, started, ended in runs:
            if rid == job_id and status == JobStatus.RUNNING:
                st = JobStatus.RUNNING
            word, color = theme.STATUS_WORDS.get(st.value, (st.value, theme.TEXT))
            counts = self.store.run_summary(rid)
            bits = []
            for key, w in (("copied", "copied"), ("updated", "updated"), ("skipped", "already there"),
                           ("removed", "removed by mirror")):
                if counts.get(key):
                    bits.append(f"{counts[key]:,} {w}")
            problems = counts.get("failed", 0) + counts.get("conflict", 0)
            if problems:
                bits.append(f"{problems:,} issue{'s' * (problems != 1)}")
            # speed for this run (slowest destination) and how it compared with copies before it
            speeds, paces = [], []
            for d in self.store.dests(rid):
                row = self.store.perf_detail(rid).get(d.id)
                if not row or not estimate.enough_work(*row[:3]):
                    continue
                if row[0] >= estimate.MIN_WORK_BYTES:
                    speeds.append(row[0] / row[2])
                p = estimate.pace(self._model(rid, d, before=created, keys=row[3:]), *row[:3])
                if p is not None:
                    paces.append(p)
            if speeds:
                bits.append(rate(min(speeds)))
            if paces:
                worst = min(paces) * 100
                bits.append("faster than usual" if worst > 115 else f"{min(worst, 100):.0f}% of usual speed")
            row = QWidget()
            row.setAttribute(Qt.WA_TranslucentBackground)
            h = QHBoxLayout(row)
            h.setContentsMargins(10, 6, 10, 6)
            stamp = label(when(ended or started or created))
            stamp.setFixedWidth(170)
            stamp.setStyleSheet("font-weight: 600;" if rid == job_id else "")
            state = label(word)
            state.setFixedWidth(150)
            state.setStyleSheet(f"color: {color};")
            summary = label(" · ".join(bits) or "—", "Muted")
            length = label(took(started, ended), "Muted")
            h.addWidget(stamp)
            h.addWidget(state)
            h.addWidget(summary, 1)
            h.addWidget(length)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, rid)
            item.setSizeHint(row.sizeHint())
            self.history.addItem(item)
            self.history.setItemWidget(item, row)
            if rid == job_id:
                item.setSelected(True)
        self.history.setFixedHeight(min(260, n * (self.history.sizeHintForRow(0) + 2) + 6))

    def _set_actions(self, status: JobStatus) -> None:
        primary = {
            JobStatus.RUNNING: ("Pause", "pause"),
            JobStatus.QUEUED: ("Start", "start"),
            JobStatus.PAUSED: ("Resume", "resume"),
            JobStatus.NEEDS_RESUME: ("Resume", "resume"),
            JobStatus.CANCELLED: ("Start Over", "resume"),
            JobStatus.DONE_WITH_ISSUES: ("Retry Issues", "retry"),
            JobStatus.CONFIRM_MIRROR: ("Review Mirror…", "mirror"),
            JobStatus.DONE: ("Run Again", "again"),
        }[status]
        self.primary.setText(primary[0])
        self.primary.setProperty("act", primary[1])
        if status == JobStatus.RUNNING:
            self.secondary.setText("Cancel")
            self.secondary.setProperty("act", "cancel")
        else:
            self.secondary.setText("Remove from List")
            self.secondary.setProperty("act", "remove")
        finished = status in (JobStatus.DONE, JobStatus.DONE_WITH_ISSUES, JobStatus.CONFIRM_MIRROR)
        self.links["verify"].setVisible(finished)
