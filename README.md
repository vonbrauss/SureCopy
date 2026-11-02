# SureCopy

Confident, resumable copies for post production. Queue sources, copy them to several
destinations at once (local drives, RAIDs, SMB shares, Avid Nexis), stop any time and
pick up where you left off.

Status: the app, copy engine and command line are working.

## Download

Get the latest Mac app (Apple Silicon, macOS 14 or later) from
[Releases](https://github.com/vonbrauss/SureCopy/releases/latest). It's ad hoc signed, not notarized, so the
first time you open it, Control-click the app and choose Open (or allow it in System Settings › Privacy &
Security).

## Setup (macOS)

```bash
cd ~/PycharmProjects/SureCopy
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest -v        # synthetic temp files only
```

## Build the Mac app

```bash
cd ~/PycharmProjects/SureCopy
bash packaging/build_app.sh
```

Takes a few minutes. It runs the tests, makes the icon, builds **dist/SureCopy.app** with PyInstaller
(Python and Qt bundled, Apple Silicon), checks the signature, audits the bundle for anything pointing
outside it, runs the built app's self-test with the development setup hidden, and zips it as
**dist/SureCopy-<version>-macOS-arm64.zip**. Logs go to `work/build-<version>/`.

Then `open dist/SureCopy.app`, or drag it into Applications. The app is ad hoc signed (not notarized),
which is fine on the Mac that built it. On another Mac, Control-click it and choose Open the first time.
Job history stays in `~/Library/Application Support/SureCopy`, shared with the development version.

Self-test of any build: `SureCopy.app/Contents/MacOS/SureCopy --self-test report.json` (temporary
folders only; exit code 0 means every check passed).

## Open the app

Double-click **Launch SureCopy.command**, or run `.venv/bin/python main.py`.

- **+ New Copy** (or drop folders on the window): add sources, pick destinations and a verify
  level for each, choose how existing files are handled, then **Start Now** or **Add to Queue**.
  A quick check shows the size, whether it fits, and any name problems before anything copies.
- **Queue** on the left: drag to reorder. **Finished** keeps each job's report.
- **Saved copies (★)**: tick *Save as favorite* to re-run the same backup in one click.
  Re-runs only copy what's new or changed.
- **History**: runs with the same sources and destinations are grouped as one copy (name and
  verify levels can change). *Finished* shows each copy once with its last run time and run
  count; the job view lists every run with date, result, counts and duration. Click a run to
  open it and its report.
- **Help** menu (and the ? button) explains verify levels, syncing, mirror mode, resume,
  history, skipping files and common problems.
- **Pause** keeps progress and holds the queue. **Resume** continues where it stopped.
  A destination that disconnects resumes on its own when it comes back (Settings can turn that off).
- **Mirror mode** shows a red warning with the full list and stays locked until you type MIRROR.
- Job history lives in `~/Library/Application Support/SureCopy/surecopy.db`.

## Time estimates and speed

SureCopy learns copy speeds from real copies; there is no separate speed test. After each copy it records,
per destination, the bytes, files and time spent actually copying (skipped files don't count), tagged with
the source drive, destination drive (by identity, so renames don't matter) and whether Full verify was on.
For each drive pair it fits big-file speed plus a per-file cost, weighting recent copies most.

- **Before a copy:** the check counts what actually needs copying and estimates the time
  ("about 3 min, based on 6 past copies between these drives").
- **While copying:** time left comes from that history; each destination shows **% of usual speed**.
- **History:** each run shows its speed and how it compared with earlier copies.
- The first copy between two drives has no estimate. Estimates start with the second copy; % of usual
  speed starts with the third.

## Stress tests

`stress/` holds tests that try to break SureCopy, using only throwaway files they create and
delete themselves. Every copy is checked with sha256, independently of the app's own checks.

```bash
.venv/bin/python stress/run_all.py                               # about 5 minutes
STRESS_FULL=1 .venv/bin/python stress/run_all.py                 # 20,000 files + a 1.1 GB file
STRESS_DIR=/Volumes/Scratch .venv/bin/python stress/run_all.py   # run on a real drive or share
```

| Test | What it does |
|---|---|
| Scale and integrity | Thousands of files (odd names, 255-byte names, 30-level folders, empty folders, files sized at chunk and checkpoint edges) to 3 destinations at all 3 verify levels; then Verify later and a re-run |
| Hard kills and resume | Kills the process at random moments (like a crash or power cut), resumes until done, including updating older files in place |
| Random faults and disconnects | Random write errors, destinations unplugged mid-write, source files edited mid-copy, random pauses |
| Sync and mirror fuzzing | Random adds, edits, deletes and folder renames between runs with mirror mode, including kills during removal; destinations must match the source exactly and every removed file must be recoverable |
| GUI, every action | Every button, menu, dialog and state, 15+ pause/resume cycles mid-copy, mirror decline/approve, auto-resume after unplug |

## Try it from the command line

```bash
.venv/bin/python -m surecopy copy "/Volumes/RAID/Show/Day01" --to /Volumes/Backup1 --to /Volumes/Nexis/Show
.venv/bin/python -m surecopy list
.venv/bin/python -m surecopy resume 3     # after Ctrl-C, a crash or an unplugged drive
.venv/bin/python -m surecopy verify 3     # "Verify later": read back finished copies
.venv/bin/python -m surecopy report 3
```

Add `--existing never` or `--existing keep_both` to change how existing files are handled.
Add `--mirror` to remove files that are no longer in the source. It shows the list and asks
you to type MIRROR before removing anything (`surecopy mirror JOB` reviews it later).
Ctrl-C pauses and keeps progress. A folder source lands as `Destination/<folder name>/...`,
like rsync without a trailing slash.

## Verify levels (per destination)

| Level | What it checks | Cost |
|---|---|---|
| **Fast** (default) | File size matches the source | Fastest |
| **Source hash** | Size, plus an xxh128 of the source recorded for later | Almost free |
| **Full verify** | Reads each destination file back and compares hashes | Re-reads every destination |

Fast and Source hash copies can be checked any time later with `verify`. Reports always
say which level each copy got.

## How it works

- **Read once, write many.** Each chunk (8 MiB) is read from the source once and handed to
  one writer thread per destination, each with its own buffer, so a slow Nexis doesn't
  stall a fast SSD until its buffer fills.
- **Resume.** Files are written as hidden `.name.surecopy-partial` files. Every 64 MiB the
  partial is fsynced and the byte count saved to SQLite
  (`~/Library/Application Support/SureCopy/surecopy.db`). Resuming continues from that
  checkpoint. Full verify re-checks the earlier bytes before continuing; Fast trusts them.
- **Sync like rsync.** Re-running a copy only moves what changed. Identical files (same
  size, modified time within 2 s, since FAT32 drives round dates) are skipped. When a different file with the same name
  is already there, the job's setting decides:
  - **Update if the source is newer** (default, like `rsync --update`). The new version
    is copied to a hidden partial, checked, then swapped in. The old file stays intact
    until then, and if someone edits it during the copy it is left alone and flagged.
    A newer copy at the destination is kept as is.
  - **Never overwrite:** flag it as a conflict.
  - **Keep both:** save the source alongside as `name (2).ext`.
- **Mirror mode** (off by default, like `rsync --delete`) also removes destination files
  that are no longer in the source. It is strict:
  - Nothing is removed until you see the exact list and approve it, on every run.
    Approval covers only that list; anything new or changed since the preview stays.
  - DANGER warnings when the source folder is empty (wrong or unmounted drive?) or the
    removal is a large share of the backup.
  - Only runs on a destination whose copy finished with no failed or unfinished files.
  - Only looks inside the folders the job copies, never the rest of the drive. Excluded
    files are protected.
  - Removed files are moved to `_SureCopy Removed/<date> <job>` on that drive. Permanent
    delete is a separate setting.
- **Destinations fail independently.** A file error fails that copy only. A disconnected
  or full destination stops for the rest of the pass while the others finish; `resume`
  continues it once it's back.
- **Planning checks.** Free space per destination, destination inside a source,
  duplicate destinations, names SMB/Nexis reject, and names that differ only by case.
- **Source safety.** If a source file changes during the copy, that file fails and is
  copied fresh on the next pass.
- On macOS, reads use `F_NOCACHE` so verification reads the actual drive or share. Every
  file is fsynced before it's marked done; files of 64 MB or more also get `F_FULLFSYNC`
  (skipping it on small files keeps big folders of documents fast on spinning RAIDs).

## Layout

```
surecopy/
  models.py   data types and verify levels
  scan.py     source walk, excludes, name checks
  store.py    SQLite job records and checkpoints
  engine.py   the copy engine
  jobs.py     plan, queue runner, resume, verify later, mirror approval
  mirror.py   mirror mode preview and removals
  drives.py   which drive a folder is on (volume UUID or share address)
  estimate.py learns copy speed per drive pair from past copies
  report.py   text reports
  fsio.py     macOS filesystem helpers (no-cache reads, full fsync, safe publish)
  cli.py      command line
  selftest.py --self-test: proves a built app works on its own
  app.py      starts the GUI
  gui/        PySide6 interface (window, job view, New Copy panel, dialogs, help, theme)
tests/        unittest suite using temporary synthetic files (GUI tests run offscreen)
stress/       stress tests (run_all.py)
```

## License

MIT, see [LICENSE](LICENSE).
