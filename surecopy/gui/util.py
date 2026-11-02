"""Small helpers shared by the GUI modules."""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..engine import human


class _Relay(QObject):
    done = Signal(object, object)   # (callback, result-or-exception)


_relay: _Relay | None = None


def run_in_thread(fn: Callable[[], Any], callback: Callable[[Any], None]) -> None:
    """Run slow disk work (scans, previews, verify) off the UI thread, then call back on it."""
    global _relay
    if _relay is None:
        _relay = _Relay()
        _relay.done.connect(lambda cb, res: cb(res))

    def work():
        try:
            res = fn()
        except Exception as exc:   # surfaced to the callback, never swallowed
            res = exc
        _relay.done.emit(callback, res)
    threading.Thread(target=work, daemon=True).start()


def volume_name(root: str) -> str:
    parts = os.path.normpath(root).split(os.sep)
    if len(parts) > 2 and parts[1] == "Volumes":
        rest = parts[3:]
        return parts[2] + (f" · {rest[-1]}" if rest else "")
    return os.path.basename(root.rstrip("/")) or root


_free_cache: dict[str, tuple[float, int | None]] = {}


def free_space(root: str, max_age: float = 10.0) -> int | None:
    now = time.monotonic()
    hit = _free_cache.get(root)
    if hit and now - hit[0] < max_age:
        return hit[1]
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = None
    _free_cache[root] = (now, free)
    return free


def when(ts: float | None) -> str:
    """Today 3:42 PM, Yesterday 9:10 AM, Oct 28, 4:05 PM, or Oct 28, 2025, 4:05 PM."""
    if not ts:
        return ""
    t = time.localtime(ts)
    clock = time.strftime("%I:%M %p", t).lstrip("0")
    today = time.localtime()
    day_diff = (time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, 0, 0, -1)) -
                time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))) / 86400
    if round(day_diff) == 0:
        return f"Today {clock}"
    if round(day_diff) == 1:
        return f"Yesterday {clock}"
    day = time.strftime("%b %d", t).replace(" 0", " ")
    if t.tm_year == today.tm_year:
        return f"{day}, {clock}"
    return f"{day}, {t.tm_year}, {clock}"


def took(start: float | None, end: float | None) -> str:
    if not start or not end:
        return ""
    s = max(0, end - start)
    if s < 60:
        return f"{s:.0f} sec"
    if s < 3600:
        return f"{s / 60:.0f} min"
    return f"{int(s // 3600)} hr {int(s % 3600 // 60)} min"


def rate(n: float) -> str:
    return f"{human(n)}/s"


def duration(seconds: float) -> str:
    if seconds < 90:
        return "about a minute"
    minutes = seconds / 60
    if minutes < 90:
        return f"about {minutes:.0f} min"
    return f"about {minutes / 60:.1f} hr"


def card(vertical: bool = True, margins=(16, 14, 16, 14), spacing: int = 8) -> tuple[QFrame, QVBoxLayout | QHBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame) if vertical else QHBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def section(text: str) -> QLabel:
    return label(text.upper(), "Section")


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


class ElidedLabel(QLabel):
    """Shows as much of a long path as fits, with … in the middle. Full text in the tooltip."""

    def __init__(self, text: str = "", name: str | None = None):
        super().__init__()
        if name:
            self.setObjectName(name)
        self._full = ""
        self.setMinimumWidth(40)
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.set_full(text)

    def set_full(self, text: str) -> None:
        if text != self._full:
            self._full = text
            self.setToolTip(text)
            self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        from PySide6.QtCore import Qt as _Qt
        super().setText(self.fontMetrics().elidedText(self._full, _Qt.ElideMiddle, max(10, self.width())))


class FlowRow(QWidget):
    """A simple wrapping row for chips."""

    def __init__(self):
        super().__init__()
        self.setObjectName("Clear")
        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(0, 0, 0, 0)
        self._v.setSpacing(6)
        self._items: list[QWidget] = []

    def set_items(self, widgets: list[QWidget], per_row: int = 2) -> None:
        clear_layout(self._v)
        for i in range(0, len(widgets), per_row):
            row = QHBoxLayout()
            row.setSpacing(6)
            for w in widgets[i:i + per_row]:
                row.addWidget(w)
            row.addStretch()
            self._v.addLayout(row)
