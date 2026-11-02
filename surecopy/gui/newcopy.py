"""The New Copy panel: sources, destinations, options, then Start or Add to Queue."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..engine import human
from ..models import Destination, ExistingPolicy, JobSpec, VerifyLevel
from ..scan import EXCLUDE_PRESETS
from ..store import Store
from . import theme
from .util import card, clear_layout, free_space, label, section, volume_name, ElidedLabel


class SourceList(QListWidget):
    """Accepts folders and files dragged in from Finder."""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(90)
        self.setMaximumHeight(170)
        self.setStyleSheet("QListWidget { background: #1b1c20; border: 1.5px dashed #3a3d46; border-radius: 8px; "
                           "padding: 6px; } QListWidget::item { padding: 4px; }")
        self._hint()

    def _hint(self):
        if self.count() == 0:
            item = QListWidgetItem("Drop folders or files here")
            item.setFlags(Qt.NoItemFlags)
            item.setData(Qt.UserRole, None)
            self.addItem(item)

    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.UserRole) for i in range(self.count()) if self.item(i).data(Qt.UserRole)]

    def add_paths(self, paths):
        existing = set(self.paths())
        if not existing:
            self.clear()
        for p in paths:
            p = p.rstrip("/") or "/"
            if p in existing or not os.path.exists(p):
                continue
            existing.add(p)
            item = QListWidgetItem(("📁  " if os.path.isdir(p) else "📄  ") + p)
            item.setData(Qt.UserRole, p)
            self.addItem(item)
        self.changed.emit()

    def remove_selected(self):
        for item in self.selectedItems():
            self.takeItem(self.row(item))
        self._hint()
        self.changed.emit()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
        e.acceptProposedAction()


class DestRow(QWidget):
    removed = Signal(object)

    def __init__(self, root: str, level: VerifyLevel):
        super().__init__()
        self.root = root
        self.setObjectName("Clear")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 2, 0, 2)
        names = QVBoxLayout()
        names.setSpacing(0)
        n = label(volume_name(root))
        n.setStyleSheet("font-weight: 600; background: transparent;")
        free = free_space(root)
        p = ElidedLabel(root + (f" · {human(free)} free" if free is not None else " · not connected"), "Muted")
        p.setStyleSheet("font-size: 11.5px; background: transparent;")
        names.addWidget(n)
        names.addWidget(p)
        h.addLayout(names, 1)
        self.level = QComboBox()
        for lv in VerifyLevel:
            self.level.addItem(lv.label, lv.value)
        self.level.setCurrentIndex(list(VerifyLevel).index(level))
        h.addWidget(self.level)
        x = QPushButton("Remove")
        x.setObjectName("Small")
        x.clicked.connect(lambda: self.removed.emit(self))
        h.addWidget(x)


class NewCopyPanel(QWidget):
    submit = Signal(object, bool, bool)   # (JobSpec, start_now, save_as_favorite)
    delete_saved = Signal(int)

    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.saved_id: int | None = None
        self.dest_rows: list[DestRow] = []

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

        self.heading = label("New Copy", "Title")
        v.addWidget(self.heading)
        name_row = QHBoxLayout()
        name_row.addWidget(label("Name", "Muted"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Named after the sources")
        name_row.addWidget(self.name, 1)
        v.addLayout(name_row)

        src_card, sl = card()
        sl.addWidget(section("Sources"))
        self.sources = SourceList()
        self.sources.changed.connect(self._auto_name)
        sl.addWidget(self.sources)
        sb = QHBoxLayout()
        for text, fn in (("Add Folder…", self._add_folder), ("Add Files…", self._add_files),
                         ("Remove", self.sources.remove_selected)):
            b = QPushButton(text)
            b.setObjectName("Small")
            b.clicked.connect(fn)
            sb.addWidget(b)
        sb.addStretch()
        sl.addLayout(sb)
        sl.addWidget(label("A folder lands on each destination as a folder with the same name, "
                           "like rsync without a trailing slash.", "Muted", wrap=True))
        v.addWidget(src_card)

        dst_card, dl = card()
        dl.addWidget(section("Destinations"))
        self.dest_box = QVBoxLayout()
        dl.addLayout(self.dest_box)
        db = QHBoxLayout()
        add = QPushButton("Add Destination…")
        add.setObjectName("Small")
        add.clicked.connect(self._add_dest)
        db.addWidget(add)
        self.recent_box = QHBoxLayout()
        db.addLayout(self.recent_box)
        db.addStretch()
        dl.addLayout(db)
        v.addWidget(dst_card)

        opt_card, ol = card()
        ol.addWidget(section("Options"))
        r = QHBoxLayout()
        r.addWidget(label("When a different file with the same name is already there:"))
        self.existing = QComboBox()
        for p in ExistingPolicy:
            self.existing.addItem(p.label, p.value)
        r.addWidget(self.existing)
        r.addStretch()
        ol.addLayout(r)
        self.presets = {}
        for key, (name, pats) in EXCLUDE_PRESETS.items():
            cb = QCheckBox(f"Skip {name.lower()}".replace("&", "&&"))
            cb.setToolTip(", ".join(pats))
            self.presets[key] = cb
            ol.addWidget(cb)
        r = QHBoxLayout()
        r.addWidget(label("Also skip:"))
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("Patterns separated by commas, e.g. *.bak, Old Exports")
        r.addWidget(self.extra, 1)
        ol.addLayout(r)
        v.addWidget(opt_card)

        mir_card, ml = card()
        ml.addWidget(section("Mirror mode"))
        self.mirror = QCheckBox("Remove files from the destinations that are no longer in the source")
        self.mirror.toggled.connect(self._mirror_toggled)
        ml.addWidget(self.mirror)
        self.mirror_warn = label(
            "⚠ Mirror mode removes files. SureCopy always shows you the full list and asks you to type MIRROR "
            "before removing anything. Removed files are moved to a “_SureCopy Removed” folder on each drive.",
            "BannerRed", wrap=True)
        ml.addWidget(self.mirror_warn)
        self.mirror_delete = QCheckBox("Delete permanently instead (can't be undone)")
        self.mirror_delete.setStyleSheet(f"color: {theme.RED};")
        ml.addWidget(self.mirror_delete)
        v.addWidget(mir_card)

        foot = QHBoxLayout()
        self.favorite = QCheckBox("Save as favorite")
        foot.addWidget(self.favorite)
        self.delete_btn = QPushButton("Delete Favorite")
        self.delete_btn.clicked.connect(lambda: self.saved_id and self.delete_saved.emit(self.saved_id))
        foot.addWidget(self.delete_btn)
        foot.addStretch()
        q = QPushButton("Add to Queue")
        q.setObjectName("Secondary")
        q.clicked.connect(lambda: self._submit(False))
        s = QPushButton("Start Now")
        s.setObjectName("Big")
        s.clicked.connect(lambda: self._submit(True))
        foot.addWidget(q)
        foot.addWidget(s)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(28, 0, 28, 20)
        wrap.addLayout(foot)
        outer.addLayout(wrap)

    # ---------------------------------------------------------------------

    def reset(self, spec: JobSpec | None = None, saved_id: int | None = None) -> None:
        """Blank form from the user's defaults, or filled in from a saved copy."""
        settings = QSettings()
        self.saved_id = saved_id
        self.heading.setText(f"★ {spec.name}" if saved_id else "New Copy")
        self.delete_btn.setVisible(saved_id is not None)
        self.favorite.setVisible(saved_id is None)
        self.favorite.setChecked(False)
        self.sources.clear()
        self.sources._hint()
        for row in self.dest_rows:
            row.setParent(None)
        self.dest_rows.clear()
        if spec:
            self.name.setText(spec.name)
            self.sources.add_paths(spec.sources)
            for d in spec.destinations:
                self._add_dest_row(d.root, d.verify)
            self.existing.setCurrentIndex(list(ExistingPolicy).index(spec.on_existing))
            for key, cb in self.presets.items():
                cb.setChecked(set(EXCLUDE_PRESETS[key][1]) <= set(spec.excludes))
            known = {p for _, pats in EXCLUDE_PRESETS.values() for p in pats}
            self.extra.setText(", ".join(p for p in spec.excludes if p not in known))
            self.mirror.setChecked(spec.mirror)
            self.mirror_delete.setChecked(spec.mirror_delete)
        else:
            self.name.clear()
            self.existing.setCurrentIndex(list(ExistingPolicy).index(
                ExistingPolicy(settings.value("default_existing", ExistingPolicy.UPDATE.value))))
            for key, cb in self.presets.items():
                cb.setChecked(settings.value(f"preset_{key}", key == "caches", type=bool))
            self.extra.clear()
            self.mirror.setChecked(False)
            self.mirror_delete.setChecked(False)
        self._mirror_toggled(self.mirror.isChecked())
        self._refresh_recent()

    def _default_level(self) -> VerifyLevel:
        return VerifyLevel(QSettings().value("default_verify", VerifyLevel.FAST.value))

    def _refresh_recent(self):
        clear_layout(self.recent_box)
        have = {r.root for r in self.dest_rows}
        for root in self.store.recent_destinations():
            if root in have or not os.path.isdir(root):
                continue
            b = QPushButton(f"+ {volume_name(root)}")
            b.setObjectName("Small")
            b.setToolTip(root)
            b.clicked.connect(lambda _=False, r=root: self._add_dest_row(r, self._default_level()))
            self.recent_box.addWidget(b)

    def _mirror_toggled(self, on: bool):
        self.mirror_warn.setVisible(on)
        self.mirror_delete.setVisible(on)
        if not on:
            self.mirror_delete.setChecked(False)

    def _auto_name(self):
        if not self.name.isModified() and not self.saved_id:
            names = [os.path.basename(p) for p in self.sources.paths()]
            self.name.setText(", ".join(names[:3]) + (f" + {len(names) - 3} more" if len(names) > 3 else ""))

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Add a source folder")
        if path:
            self.sources.add_paths([path])

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add source files")
        self.sources.add_paths(paths)

    def _add_dest(self):
        path = QFileDialog.getExistingDirectory(self, "Choose a destination folder")
        if path:
            self._add_dest_row(path, self._default_level())

    def _add_dest_row(self, root: str, level: VerifyLevel):
        if any(r.root == root for r in self.dest_rows):
            return
        row = DestRow(root, level)
        row.removed.connect(self._remove_dest)
        self.dest_rows.append(row)
        self.dest_box.addWidget(row)
        self._refresh_recent()

    def _remove_dest(self, row):
        self.dest_rows.remove(row)
        row.setParent(None)
        self._refresh_recent()

    def spec(self) -> JobSpec:
        excludes = [p for key, cb in self.presets.items() if cb.isChecked() for p in EXCLUDE_PRESETS[key][1]]
        excludes += [p.strip() for p in self.extra.text().split(",") if p.strip()]
        sources = self.sources.paths()
        return JobSpec(
            self.name.text().strip() or ", ".join(os.path.basename(p) for p in sources) or "Copy",
            sources,
            [Destination(r.root, VerifyLevel(r.level.currentData())) for r in self.dest_rows],
            excludes,
            on_existing=ExistingPolicy(self.existing.currentData()),
            mirror=self.mirror.isChecked(),
            mirror_delete=self.mirror.isChecked() and self.mirror_delete.isChecked(),
        )

    def _submit(self, start: bool):
        spec = self.spec()
        if not spec.sources:
            QMessageBox.information(self, "SureCopy", "Add at least one source folder or file.")
            return
        if not spec.destinations:
            QMessageBox.information(self, "SureCopy", "Add at least one destination.")
            return
        if spec.mirror_delete:
            ok = QMessageBox.warning(
                self, "Delete permanently?",
                "Mirror mode is set to delete files permanently instead of moving them aside. "
                "Deleted files can't be recovered.\n\nYou'll still see the full list and confirm before anything "
                "is removed.", QMessageBox.Cancel | QMessageBox.Ok, QMessageBox.Cancel)
            if ok != QMessageBox.Ok:
                return
        self.submit.emit(spec, start, self.favorite.isChecked())
