"""Mirror warning, pre-copy check, report and settings dialogs."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout)

from ..engine import human
from ..mirror import REMOVED_FOLDER, DestPreview
from ..models import ExistingPolicy, JobSpec, VerifyLevel
from ..scan import EXCLUDE_PRESETS, ScanResult
from . import theme
from .util import free_space, label, volume_name


class MirrorDialog(QDialog):
    """The strict warning. Removal stays locked until the user types MIRROR."""

    def __init__(self, parent, spec: JobSpec, previews: list[DestPreview]):
        super().__init__(parent)
        self.setWindowTitle("Mirror mode")
        self.setModal(True)
        self.setMinimumWidth(720)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        total = sum(len(p.files) for p in previews)
        head = QLabel()
        head.setTextFormat(Qt.RichText)
        head.setWordWrap(True)
        head.setText(f"<div style='font-size:19px;font-weight:700;color:#ffb3ad'>⚠ Mirror mode will remove "
                     f"{total:,} file{'s' * (total != 1)}</div><div style='color:#e9c3bf;margin-top:4px'>These "
                     "files are on the destination but no longer in the source. Nothing has been removed yet.</div>")
        head.setStyleSheet("background: #3b1c1c; padding: 20px 26px; border-bottom: 1px solid #5a2c2c;")
        v.addWidget(head)

        inner = QVBoxLayout()
        inner.setContentsMargins(26, 18, 26, 18)
        inner.setSpacing(10)
        for p in previews:
            for danger in p.dangers:
                d = label(f"<b style='color:{theme.RED}'>DANGER:</b> {danger}", "BannerRed", wrap=True)
                d.setTextFormat(Qt.RichText)
                inner.addWidget(d)
            hdr = QHBoxLayout()
            name = label(f"{volume_name(p.root)} · {p.root}")
            name.setStyleSheet("font-weight: 650;")
            hdr.addWidget(name)
            hdr.addStretch()
            nf, nd = len(p.files), len(p.folders)
            hdr.addWidget(label(f"{nf:,} file{'s' * (nf != 1)} · {nd:,} folder{'s' * (nd != 1)} · "
                                f"{human(p.total_bytes)}", "Muted"))
            inner.addLayout(hdr)
            lst = QListWidget()
            lst.setStyleSheet("QListWidget { background: #1b1c20; border: 1px solid #2e3037; border-radius: 8px; "
                              "font-family: Menlo, monospace; font-size: 12px; }")
            lst.setMaximumHeight(170)
            for rel, size in p.files[:5000]:
                lst.addItem(f"{rel}    ({human(size)})")
            if len(p.files) > 5000:
                lst.addItem(f"…and {len(p.files) - 5000:,} more (all listed in the report afterward)")
            for rel in p.folders[:500]:
                lst.addItem(f"{rel}/    (folder)")
            inner.addWidget(lst)

        if spec.mirror_delete:
            where = label("<b style='color:#ff7b72'>They will be PERMANENTLY DELETED. This can't be undone.</b>",
                          wrap=True)
        else:
            where = label(f"They'll be moved to <b>{REMOVED_FOLDER}</b> on each drive, so you can get them back. "
                          "Delete that folder later to free up space.", "Muted", wrap=True)
        where.setTextFormat(Qt.RichText)
        inner.addWidget(where)
        inner.addWidget(label("To approve, type <b>MIRROR</b> below."))
        self.typed = QLineEdit()
        self.typed.setPlaceholderText("Type MIRROR")
        self.typed.setStyleSheet(f"border: 1.5px solid {theme.RED}; font-family: Menlo, monospace; font-size: 14px;"
                                 " padding: 8px;")
        self.typed.textChanged.connect(lambda t: self.remove.setEnabled(t.strip() == "MIRROR"))
        inner.addWidget(self.typed)
        v.addLayout(inner)

        foot = QHBoxLayout()
        foot.setContentsMargins(26, 14, 26, 18)
        later = QPushButton("Decide Later")
        later.clicked.connect(lambda: self.done(0))
        foot.addWidget(later)
        foot.addStretch()
        keep = QPushButton("Don't Remove Anything")
        keep.setObjectName("Primary")
        keep.setDefault(True)
        keep.clicked.connect(lambda: self.done(1))
        self.remove = QPushButton(f"Remove {total:,} File{'s' * (total != 1)}")
        self.remove.setObjectName("Danger")
        self.remove.setEnabled(False)
        self.remove.setAutoDefault(False)
        self.remove.clicked.connect(lambda: self.done(2))
        foot.addWidget(keep)
        foot.addWidget(self.remove)
        v.addLayout(foot)
    # result: 0 = decide later, 1 = don't remove, 2 = approved


def estimate_words(seconds: float) -> str:
    """"under a minute", "about 3 min", "about 1.5 hr"."""
    if seconds < 60:
        return "under a minute"
    from .util import duration
    return duration(seconds)


class CheckDialog(QDialog):
    """Summary shown before a job starts: size, fit, and anything to look at."""

    def __init__(self, parent, spec: JobSpec, result: ScanResult, start: bool, estimates=None):
        super().__init__(parent)
        self.setWindowTitle("Ready to copy")
        self.setMinimumWidth(600)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(10)
        blocked = bool(result.problems)
        t = label("Can't start this copy" if blocked else spec.name, "Title")
        t.setStyleSheet("font-size: 18px;")
        v.addWidget(t)
        v.addWidget(label(f"{len(result.files):,} files · {human(result.total_bytes)}", "Sub"))
        est = {e.root: e for e in (estimates or [])}
        if est:
            times = [e.seconds for e in est.values()]
            if all(t is not None for t in times):
                runs = min(e.runs for e in est.values())
                t = label(f"Estimated time: {estimate_words(max(times))}  ·  based on {runs} past "
                          f"cop{'ies' if runs != 1 else 'y'} between these drives")
                t.setStyleSheet(f"color: {theme.ACCENT_LIGHT}; font-weight: 600;")
                v.addWidget(t)
            elif not any(e.nfiles for e in est.values()):
                v.addWidget(label("Everything is already there. Nothing needs copying.", "Muted"))
        for d in spec.destinations:
            free = free_space(d.root)
            if free is None:
                line, name = f"✕ {volume_name(d.root)}: not connected", "BannerRed"
            elif free >= result.total_bytes:
                line, name = f"✓ {volume_name(d.root)}: fits ({human(free)} free)", "Muted"
            else:
                line, name = (f"⚠ {volume_name(d.root)}: {human(free)} free. That's only enough if some files are "
                              "already there and get skipped. SureCopy checks again before copying.", "Banner")
            v.addWidget(label(line, name, wrap=True))
            e = est.get(d.root)
            if e is not None:
                if e.nfiles == 0:
                    text = "Nothing new to copy"
                else:
                    text = f"{e.nfiles:,} new or changed file{'s' * (e.nfiles != 1)} · {human(e.nbytes)}"
                    text += (f" · {estimate_words(e.seconds)}" if e.seconds is not None
                             else " · no time estimate yet (first copy between these drives)")
                note = label("     " + text, "Muted")
                note.setStyleSheet("font-size: 12px;")
                v.addWidget(note)
        if spec.mirror:
            v.addWidget(label("Mirror mode is on. After copying, you'll review anything to be removed and type "
                              "MIRROR before it happens.", "BannerRed", wrap=True))
        items = [f"✕ {p}" for p in result.problems] + [f"⚠ {w}" for w in result.warnings]
        if items:
            v.addWidget(label("Needs your attention:" if blocked else "Heads up:"))
            lst = QListWidget()
            lst.setWordWrap(True)
            lst.setMaximumHeight(180)
            lst.addItems(items[:2000])
            v.addWidget(lst)
        foot = QHBoxLayout()
        foot.addStretch()
        cancel = QPushButton("Close" if blocked else "Cancel")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        if not blocked:
            go = QPushButton("Start Copy" if start else "Add to Queue")
            go.setObjectName("Primary")
            go.setDefault(True)
            go.clicked.connect(self.accept)
            foot.addWidget(go)
        v.addLayout(foot)


class ReportDialog(QDialog):
    def __init__(self, parent, title: str, text: str):
        super().__init__(parent)
        self.setWindowTitle(f"Report: {title}")
        self.resize(820, 620)
        self.text = text
        v = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        v.addWidget(box)
        h = QHBoxLayout()
        h.addStretch()
        save = QPushButton("Save…")
        save.clicked.connect(lambda: self._save(title))
        close = QPushButton("Close")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        h.addWidget(save)
        h.addWidget(close)
        v.addLayout(h)

    def _save(self, title):
        path, _ = QFileDialog.getSaveFileName(self, "Save report", os.path.expanduser(f"~/Desktop/{title} report.txt"),
                                              "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text)


class SettingsDialog(QDialog):
    def __init__(self, parent, db_path: str):
        super().__init__(parent)
        self.setWindowTitle("SureCopy Settings")
        self.setMinimumWidth(520)
        s = QSettings()
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        form = QFormLayout()
        self.verify = QComboBox()
        for lv in VerifyLevel:
            self.verify.addItem(lv.label, lv.value)
        self.verify.setCurrentIndex(self.verify.findData(s.value("default_verify", VerifyLevel.FAST.value)))
        form.addRow("Default verify level", self.verify)
        self.existing = QComboBox()
        for p in ExistingPolicy:
            self.existing.addItem(p.label, p.value)
        self.existing.setCurrentIndex(self.existing.findData(s.value("default_existing", ExistingPolicy.UPDATE.value)))
        form.addRow("Existing files", self.existing)
        self.presets = {}
        for key, (name, _) in EXCLUDE_PRESETS.items():
            cb = QCheckBox(f"Skip {name.lower()} by default".replace("&", "&&"))
            cb.setChecked(s.value(f"preset_{key}", key == "caches", type=bool))
            self.presets[key] = cb
            form.addRow("", cb)
        self.auto = QCheckBox("Resume automatically when a disconnected drive comes back")
        self.auto.setChecked(s.value("auto_resume", True, type=bool))
        form.addRow("", self.auto)

        v.addLayout(form)
        info = label(f"Job history: {db_path}", "Muted", wrap=True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(info)
        h = QHBoxLayout()
        h.addStretch()
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        ok = QPushButton("Save")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._save)
        h.addWidget(c)
        h.addWidget(ok)
        v.addLayout(h)

    def _save(self):
        s = QSettings()
        s.setValue("default_verify", self.verify.currentData())
        s.setValue("default_existing", self.existing.currentData())
        for key, cb in self.presets.items():
            s.setValue(f"preset_{key}", cb.isChecked())
        s.setValue("auto_resume", self.auto.isChecked())
        self.accept()
