"""Help window and its topics. Keep this text in step with what the app actually does."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QTextBrowser

from .. import __version__
from . import theme

CSS = f"""
<style>
h2 {{ color: {theme.TEXT}; font-size: 19px; margin: 6px 0 10px 0; }}
h3 {{ color: {theme.TEXT}; font-size: 14px; margin: 16px 0 4px 0; }}
p, li {{ color: {theme.TEXT_2}; font-size: 13px; line-height: 150%; }}
b {{ color: {theme.TEXT}; }}
.warn {{ color: {theme.AMBER}; }}
.red {{ color: {theme.RED}; }}
code {{ color: {theme.ACCENT_LIGHT}; }}
td {{ color: {theme.TEXT_2}; padding: 4px 14px 4px 0; vertical-align: top; }}
th {{ color: {theme.TEXT}; text-align: left; padding: 4px 14px 4px 0; }}
</style>
"""

TOPICS: list[tuple[str, str, str]] = [
    ("start", "Getting started", """
<h2>Getting started</h2>
<p>SureCopy copies folders and files to one or more destinations at the same time, keeps track of every file,
and can stop and pick up again without starting over.</p>
<h3>Make a copy</h3>
<ol>
<li>Click <b>+ New Copy</b> (⌘N), or drop folders onto the window.</li>
<li>Add <b>sources</b>: folders or files. A folder lands on each destination as a folder with the same name.</li>
<li>Add <b>destinations</b>: drives, RAIDs, network shares or Avid Nexis workspaces. Pick a verify level for each.</li>
<li>Choose what happens when a file with the same name is already there.</li>
<li>Click <b>Start Now</b> or <b>Add to Queue</b>. SureCopy shows a quick check first: the size,
whether it fits, and any file names that might cause trouble.</li>
</ol>
<h3>While it runs</h3>
<p>Each destination gets its own card with progress, speed and counts. One slow drive doesn't hold back the others
until its buffer fills. Problems with individual files show up in the <b>Issues</b> list and never stop the rest
of the copy.</p>
"""),
    ("verify", "Verify levels", """
<h2>Verify levels</h2>
<p>Set per destination. You can mix them in one copy, for example Full verify on the archive and Fast on a
working drive.</p>
<table>
<tr><th>Level</th><th>What it checks</th><th>Speed</th></tr>
<tr><td><b>Fast</b> (default)</td><td>The copied file's size matches the source.</td><td>Fastest</td></tr>
<tr><td><b>Source hash</b></td><td>Size, plus an xxHash (xxh128) of the source recorded while it's read.
No readback.</td><td>About the same as Fast</td></tr>
<tr><td><b>Full verify</b></td><td>Reads every destination file back and compares its hash with the source.
</td><td>Slower: every destination is read again</td></tr>
</table>
<p>Fast catches interrupted, truncated and out-of-space copies. It won't catch a file that has the right size but
damaged contents, which is rare on healthy drives. For archive and delivery copies, use Full verify, or run
<b>Verify later</b> on a finished copy to read everything back afterward.</p>
<p>Every report says which level each copy got, so "copied" is never mistaken for "verified".</p>
"""),
    ("sync", "Syncing and existing files", """
<h2>Syncing and existing files</h2>
<p>Running the same copy again only moves what's new or changed, like rsync.</p>
<ul>
<li><b>Same size and modified date</b> (within two seconds, since some drives round file dates): skipped as <i>already there</i>.</li>
<li><b>Different file with the same name</b>: decided by the copy's setting.</li>
</ul>
<table>
<tr><th>Setting</th><th>What happens</th></tr>
<tr><td><b>Update if the source is newer</b> (default)</td><td>The newer source replaces the older destination
file. If the destination's copy is newer, it's kept and listed as <i>kept newer</i>. Same date but a different
size is flagged as a conflict.</td></tr>
<tr><td><b>Never overwrite</b></td><td>Different files are flagged as conflicts and left alone.</td></tr>
<tr><td><b>Keep both</b></td><td>The source is saved alongside as <code>name (2).ext</code>.</td></tr>
</table>
<p>Replacing is safe: the new version is copied to a hidden partial file and checked first, and only then swapped
in. If someone edits the destination file during the copy, SureCopy leaves their version alone and flags it.</p>
"""),
    ("mirror", "Mirror mode", """
<h2>Mirror mode</h2>
<p class="red"><b>Mirror mode removes files.</b> It's off unless you turn it on for a copy.</p>
<p>With mirror mode on, after copying SureCopy looks for files on each destination that are no longer in the
source, for example renamed or deleted files.</p>
<ul>
<li>Nothing is removed until you see the full list and type <b>MIRROR</b>. This happens on every run.</li>
<li>Your approval covers only the files on that list. Anything added or changed after the list was made stays.</li>
<li><b>DANGER</b> warnings appear if a source folder is empty (wrong or unmounted drive?) or if the removal is
a large share of the backup.</li>
<li>It only runs on a destination whose copy finished with no failed or unfinished files.</li>
<li>It only looks inside the folders this copy writes, never the rest of the drive. Skipped (excluded) files are
never removed.</li>
<li>Removed files are moved to a <code>_SureCopy Removed</code> folder on that drive, so you can get them back.
Delete that folder later to free up space. Permanent delete is a separate setting with its own warning.</li>
</ul>
"""),
    ("resume", "Pause, resume and disconnects", """
<h2>Pause, resume and disconnects</h2>
<p><b>Pause</b> stops the current copy and holds the queue. <b>Resume</b> picks up where it left off.
Progress is saved every 64 MB, so even a crash or power loss only repeats a little.</p>
<p>Files are written as hidden <code>.name.surecopy-partial</code> files and only get their real name when
they're finished. Anything with its real name on a destination is complete.</p>
<p>If a destination disconnects, the other destinations keep going. When the drive comes back, SureCopy resumes
automatically (you can turn that off in Settings).</p>
<p><b>Cancel</b> stops the copy and removes its partial files. Finished files stay.</p>
<p>If a source file changes while it's being copied, that file is marked as failed and copied fresh next time.</p>
"""),
    ("history", "Favorites and history", """
<h2>Favorites and history</h2>
<p><b>Saved copies (★)</b> keep a copy's sources, destinations and settings. Click one to run it again.
Tick <b>Save as favorite</b> in New Copy, or use <b>Save as favorite</b> on any job.</p>
<p><b>History</b>: runs with the same sources going to the same destinations are grouped as one copy, even if the
name or verify levels changed. The Finished list shows each copy once with its last run time and how many times it
has run. Open it to see every run's date, result, counts and duration, and click a run to see its details and
report.</p>
<p>Removing a run from the list only removes SureCopy's record. Files on your drives are never touched.</p>
"""),
    ("speed", "Time estimates and speed", """
<h2>Time estimates and speed</h2>
<p>SureCopy learns how fast copies between two drives usually go from the copies themselves. There's no
separate speed test: every copy you run teaches it a little more.</p>
<h3>What it records</h3>
<p>After each copy, for every destination, SureCopy keeps the bytes copied, the number of files and the time
spent actually copying them. Time spent checking files that are already there doesn't count, so a re-run that
skips most files doesn't look slow. Each record is tagged with the <b>source drive</b> and the
<b>destination drive</b> (by the drive's identity, so renaming a drive keeps its history) and whether
<b>Full verify</b> was on, since reading everything back takes longer.</p>
<p>From those records it learns two things for each pair of drives: the speed for big files, and the extra
time each file adds. Together they estimate a folder of 50,000 documents and a few 100 GB files equally well.
Recent copies count the most, so a drive that's slowing down shows up over time.</p>
<h3>Where you'll see it</h3>
<ul>
<li><b>Before a copy:</b> the check counts only the files that actually need copying and estimates how long
they'll take, for example "about 3 min, based on 6 past copies between these drives".</li>
<li><b>While copying:</b> the time left comes from what these drives usually do, which is steadier than the
last few seconds' speed. Each destination shows <b>% of usual speed</b>.</li>
<li><b>History:</b> each run shows its speed and how it compared with the copies before it.</li>
</ul>
<table>
<tr><td><span style="color:#5fe08a"><b>85% and up</b></span></td><td>Normal for these drives.</td></tr>
<tr><td><span style="color:#f0b46a"><b>60 to 85%</b></span></td><td>Somewhat slower than usual: other
copies on the same drives, a busy network, or a drive that's filling up.</td></tr>
<tr><td><span style="color:#ff7b72"><b>Under 60%</b></span></td><td>Worth a look: a failing or very full
drive, a slow cable or hub, or heavy load on shared storage.</td></tr>
</table>
<h3>The first copy</h3>
<p>The first copy between two drives has no estimate, and the card says so. From the second copy on,
SureCopy estimates times, and from the third it also shows % of usual speed (one past copy isn't enough
to tell big-file speed from the time each file adds). Very short copies (a couple of seconds) aren't used, since they say more
about noise than speed. Shared storage like Avid Nexis varies with how busy it is, so expect more spread
there.</p>
"""),
    ("skip", "Skipping files", """
<h2>Skipping files</h2>
<p>These are never copied: <code>.DS_Store</code>, <code>._*</code> resource files, <code>.Spotlight-V100</code>,
<code>.Trashes</code>, <code>.fseventsd</code> and SureCopy's own partial files. Symbolic links are skipped and
listed as warnings.</p>
<p><b>Skip caches &amp; render files</b> (on by default) leaves out Adobe Media Cache and Peak Files, Premiere
previews, After Effects disk cache, DaVinci Resolve CacheClip, Final Cut render files and similar.</p>
<p><b>Also skip</b> takes your own patterns separated by commas, for example <code>*.bak, Old Exports</code>.
They match file or folder names anywhere in the copy.</p>
"""),
    ("trouble", "Troubleshooting", """
<h2>Troubleshooting</h2>
<h3>"May not copy to SMB or Avid Nexis"</h3>
<p>Names containing <code>\\ : * ? " &lt; &gt; |</code>, names ending in a space or period, and very long names
often fail on network shares. Rename the file at the source and run the copy again.</p>
<h3>"Differ only by upper/lowercase"</h3>
<p>Mac and Nexis drives treat <code>Clip.mov</code> and <code>clip.mov</code> as the same name, so a copy can't
hold both. Rename one at the source.</p>
<h3>Permission denied</h3>
<p>The source file can't be read or the destination folder can't be written. Check sharing permissions, then use
<b>Retry all</b>.</p>
<h3>Not enough space</h3>
<p>SureCopy checks free space on each destination before copying (already-copied files count toward what fits).
Free up space or remove that destination, then Resume.</p>
<h3>A destination shows "Disconnected"</h3>
<p>Reconnect the drive or remount the share. The copy picks up from its last saved point.</p>
<h3>Where things are kept</h3>
<p>Job history and reports: <code>~/Library/Application Support/SureCopy/surecopy.db</code>.</p>
"""),
    ("keys", "Keyboard shortcuts", """
<h2>Keyboard shortcuts</h2>
<table>
<tr><td><b>⌘N</b></td><td>New Copy</td></tr>
<tr><td><b>⌘,</b></td><td>Settings</td></tr>
<tr><td><b>⌘R</b></td><td>Run the selected copy again</td></tr>
<tr><td><b>⌘.</b></td><td>Pause</td></tr>
<tr><td><b>⌘I</b></td><td>View report</td></tr>
<tr><td><b>⌘?</b></td><td>SureCopy Help</td></tr>
</table>
"""),
]


class HelpDialog(QDialog):
    def __init__(self, parent=None, topic: str = "start"):
        super().__init__(parent)
        self.setWindowTitle("SureCopy Help")
        self.resize(860, 600)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.topics = QListWidget()
        self.topics.setFixedWidth(250)
        self.topics.setWordWrap(True)
        self.topics.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.topics.setStyleSheet(f"QListWidget {{ background: {theme.SIDEBAR}; border: none; border-radius: 0; "
                                  f"padding: 12px 8px; }} QListWidget::item {{ padding: 7px 10px; }}")
        for key, title, _ in TOPICS:
            self.topics.addItem(title)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        h.addWidget(self.topics)
        h.addWidget(self.view, 1)
        self.topics.currentRowChanged.connect(self._show)
        self.show_topic(topic)

    def show_topic(self, key: str) -> None:
        keys = [k for k, _, _ in TOPICS]
        self.topics.setCurrentRow(keys.index(key) if key in keys else 0)

    def _show(self, row: int) -> None:
        if 0 <= row < len(TOPICS):
            self.view.setHtml(CSS + TOPICS[row][2])


ABOUT = (f"<h3>SureCopy {__version__}</h3><p>Confident, resumable copies for post production.</p>"
         "<p>Copies to several destinations at once, syncs like rsync, and checks every file at the verify "
         "level you choose.</p>")
