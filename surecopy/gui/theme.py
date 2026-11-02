"""Colors and the app stylesheet. Every widget that can pop up (menus, dropdowns, message boxes,
tooltips) gets explicit colors so text stays readable on the dark theme."""

import os

ASSETS = os.path.join(os.path.dirname(__file__), "assets").replace(os.sep, "/")

BG = "#1c1d21"
SIDEBAR = "#18181c"
CARD = "#232429"
RAISED = "#2a2c33"
BORDER = "#2f3138"
INPUT = "#17181c"
TEXT = "#e8e9ec"
TEXT_2 = "#c3c6cd"
MUTED = "#9a9da7"
FAINT = "#7f828c"
ACCENT = "#2f80ed"
ACCENT_HOVER = "#4290f5"
ACCENT_LIGHT = "#7cc0ff"
GREEN = "#5fe08a"
AMBER = "#f0b46a"
RED = "#ff7b72"
BAR_COLORS = ["#4aa3ff", "#8b7bff", "#3ccf9a", "#f08a5d", "#d070e0", "#e0c050"]

STYLE = f"""
* {{ outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; selection-background-color: {ACCENT};
          selection-color: white; }}
QLabel {{ background: transparent; }}

/* ---- top bar and sidebar ---- */
#TopBar {{ background: #222328; border-bottom: 1px solid #2b2c32; }}
#AppName {{ font-size: 16px; font-weight: 700; }}
#Sidebar, #Sidebar QListWidget {{ background: {SIDEBAR}; }}
#Sidebar {{ border-right: 1px solid #28292f; }}
QLabel#Section {{ color: {FAINT}; font-size: 10.5px; font-weight: 700; letter-spacing: 1px; }}

/* ---- lists ---- */
QListWidget {{ border: none; color: {TEXT}; }}
QListWidget::item {{ border-radius: 8px; margin: 1px 0; color: {TEXT}; }}
QListWidget::item:selected {{ background: #263247; color: {TEXT}; }}
QListWidget::item:hover:!selected {{ background: #222329; }}

/* ---- cards ---- */
QFrame#Card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#Card QWidget#Clear, QFrame#Card QCheckBox {{ background: transparent; }}
QLabel#Title {{ font-size: 22px; font-weight: 650; }}
QLabel#Sub {{ color: {MUTED}; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Pill {{ font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 10px; }}
QLabel#Chip {{ background: {RAISED}; color: {TEXT_2}; border-radius: 6px; padding: 3px 9px; font-size: 12px; }}
QLabel#ChipWarn {{ background: #3a2a1a; color: {AMBER}; border-radius: 6px; padding: 3px 9px; font-size: 12px; }}
QLabel#ChipDanger {{ background: #4a1f1f; color: #ffb3ad; border-radius: 6px; padding: 3px 9px; font-size: 12px; }}
QLabel#Banner {{ background: #3a2a1a; color: {AMBER}; border-radius: 8px; padding: 8px 10px; }}
QLabel#BannerRed {{ background: #4a1f1f; color: #ffc9c4; border-radius: 8px; padding: 8px 10px; }}

/* ---- buttons ---- */
QPushButton {{ background: {RAISED}; color: #dcdee3; border: 1px solid #353841; border-radius: 7px;
              padding: 6px 14px; min-height: 18px; }}
QPushButton:hover {{ background: #33363e; }}
QPushButton:pressed {{ background: #2a2c33; }}
QPushButton:disabled {{ color: #60636d; background: #24252b; border-color: #2c2e35; }}
QPushButton#Primary, QPushButton#Big {{ background: {ACCENT}; color: white; font-weight: 650; border: none; }}
QPushButton#Primary:hover, QPushButton#Big:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#Big {{ font-size: 14px; padding: 9px 26px; border-radius: 8px; }}
QPushButton#Secondary {{ font-size: 13.5px; padding: 8px 18px; border-radius: 8px; }}
QPushButton#Danger {{ background: #c2392f; color: white; font-weight: 650; border: none; }}
QPushButton#Danger:hover {{ background: #d4463b; }}
QPushButton#Danger:disabled {{ background: #3a2525; color: #a07872; }}
QPushButton#Link {{ background: transparent; border: none; color: {ACCENT_LIGHT}; padding: 4px 6px; }}
QPushButton#Link:hover {{ color: #a8d6ff; }}
QPushButton#Small {{ padding: 3px 10px; font-size: 12px; }}
QPushButton#Icon {{ padding: 4px 9px; min-width: 14px; font-size: 14px; }}

/* ---- inputs and dropdowns ---- */
QLineEdit {{ background: {INPUT}; color: {TEXT}; border: 1px solid #383b44; border-radius: 7px; padding: 6px 9px; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit[readOnly="true"] {{ color: {MUTED}; }}
QComboBox {{ background: {RAISED}; color: {TEXT}; border: 1px solid #383b44; border-radius: 7px;
            padding: 5px 30px 5px 10px; min-height: 18px; combobox-popup: 0; }}
QComboBox:hover {{ border-color: #4a4e59; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 24px; border: none; }}
QComboBox::down-arrow {{ image: url({ASSETS}/chevron.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: #2a2b31; color: {TEXT}; border: 1px solid #3a3d46; border-radius: 8px;
                              padding: 4px; selection-background-color: {ACCENT}; selection-color: white; }}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding: 0 8px; border-radius: 5px; color: {TEXT}; }}
QComboBox QAbstractItemView::item:selected {{ background: {ACCENT}; color: white; }}

QCheckBox {{ spacing: 9px; background: transparent; color: {TEXT}; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1.5px solid #5d616c; border-radius: 4px; background: {INPUT}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT_LIGHT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({ASSETS}/check.svg); }}
QCheckBox:disabled {{ color: #60636d; }}

/* ---- menus (in-window and context menus; the macOS menu bar itself is native) ---- */
QMenuBar {{ background: #222328; color: {TEXT}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {RAISED}; border-radius: 5px; }}
QMenu {{ background: #2a2b31; color: {TEXT}; border: 1px solid #3a3d46; border-radius: 8px; padding: 5px; }}
QMenu::item {{ padding: 6px 26px 6px 14px; border-radius: 5px; color: {TEXT}; background: transparent; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::item:disabled {{ color: #6a6d77; }}
QMenu::separator {{ height: 1px; background: #3a3d46; margin: 5px 8px; }}

/* ---- dialogs, message boxes, tooltips ---- */
QDialog, QMessageBox {{ background: #24252b; }}
QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}
QMessageBox QPushButton {{ min-width: 76px; }}
QDialog QListWidget {{ background: {INPUT}; border: 1px solid {BORDER}; border-radius: 8px; }}
QToolTip {{ background: #2c2e35; color: {TEXT}; border: 1px solid #3a3d46; padding: 5px 7px; border-radius: 6px; }}
QTextBrowser {{ background: #1f2025; color: {TEXT}; border: none; padding: 6px 14px; }}
QPlainTextEdit {{ background: {INPUT}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
                 font-family: Menlo, monospace; font-size: 12px; padding: 6px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; border: none; }}
QStatusBar::item {{ border: none; }}

/* ---- progress and scrolling ---- */
QProgressBar {{ background: #2c2e35; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ border-radius: 4px; background: #4aa3ff; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: #3a3d46; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: #4a4e59; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""

STATUS_STYLE = {
    "running": ("COPYING", "#173a63", ACCENT_LIGHT),
    "queued": ("QUEUED", "#2c2e35", TEXT_2),
    "paused": ("PAUSED", "#3a3420", "#f0d26a"),
    "needs_resume": ("NEEDS RESUME", "#3a2a1a", AMBER),
    "done": ("DONE", "#173a28", GREEN),
    "done_with_issues": ("DONE WITH ISSUES", "#3a2a1a", AMBER),
    "confirm_mirror": ("WAITING FOR MIRROR OK", "#4a1f1f", "#ffb3ad"),
    "cancelled": ("CANCELLED", "#2c2e35", MUTED),
}

STATUS_WORDS = {
    "running": ("Copying", ACCENT_LIGHT), "queued": ("Queued", TEXT_2), "paused": ("Paused", "#f0d26a"),
    "needs_resume": ("Needs resume", AMBER), "done": ("Done", GREEN), "done_with_issues": ("Done with issues", AMBER),
    "confirm_mirror": ("Waiting for mirror OK", "#ffb3ad"), "cancelled": ("Cancelled", MUTED),
}
