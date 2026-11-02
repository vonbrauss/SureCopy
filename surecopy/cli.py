"""Command line for trying the engine before the GUI exists.

  surecopy copy SOURCE... --to DEST [--to DEST ...] [--verify fast|source|full]
  surecopy list | resume JOB | report JOB | verify JOB

Ctrl-C pauses the job and keeps its progress; `surecopy resume JOB` continues it.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time

from .engine import Controls, Engine, human
from .jobs import approve_mirror, decline_mirror, mirror_preview, plan_job, resume_job, verify_later
from .mirror import preview_text
from .models import Destination, ExistingPolicy, JobSpec, VerifyLevel
from .report import report_text
from .store import Store


def _progress(p):
    done = min(p.dest_bytes.values()) if p.dest_bytes else 0
    pct = 100 * done / p.total_bytes if p.total_bytes else 100
    name = (p.file or "")[-50:]
    sys.stdout.write(f"\r{pct:5.1f}%  {human(done):>9} of {human(p.total_bytes):<9} "
                     f"files {p.files_done}/{p.files_total}  {name:<50}")
    sys.stdout.flush()


def _run(store: Store, job_id: int) -> int:
    controls = Controls()
    signal.signal(signal.SIGINT, lambda *_: (controls.pause(), print("\nPausing, saving progress...")))
    started = time.monotonic()
    status = Engine(store, controls, _progress).run(job_id)
    print(f"\n\nJob {job_id}: {status.value.replace('_', ' ')} in {time.monotonic() - started:.1f}s\n")
    print(report_text(store, job_id))
    if status.value == "confirm_mirror":
        if sys.stdin.isatty() and store.removals(job_id, "planned"):
            return _mirror(store, job_id)
        print(f"Mirror mode is waiting for approval. Review it with: surecopy mirror {job_id}")
    return 0 if status.value == "done" else 1


def _mirror(store: Store, job_id: int) -> int:
    previews = mirror_preview(store, job_id)
    if not previews:
        print("Nothing to remove.")
        return 0
    print(preview_text(previews, store.spec(job_id)))
    if not sys.stdin.isatty():
        print("Not approved (needs a person at the keyboard).")
        return 1
    answer = input("Type MIRROR to approve these removals, or press Return to cancel: ")
    if answer.strip() != "MIRROR":
        decline_mirror(store, job_id)
        print("Declined. Nothing was removed.")
        return 1
    approve_mirror(store, job_id)
    return _run(store, job_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="surecopy")
    ap.add_argument("--db", help="job database (default: ~/Library/Application Support/SureCopy)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("copy")
    c.add_argument("sources", nargs="+")
    c.add_argument("--to", action="append", required=True, dest="dests")
    c.add_argument("--verify", choices=[v.value for v in VerifyLevel], default="fast")
    c.add_argument("--exclude", action="append", default=[])
    c.add_argument("--existing", choices=[p.value for p in ExistingPolicy], default="update",
                   help="when a different file is already there (default: update if source is newer)")
    c.add_argument("--name")
    c.add_argument("--mirror", action="store_true",
                   help="also remove destination files no longer in the source (asks first, every time)")
    c.add_argument("--mirror-delete-permanently", action="store_true",
                   help="with --mirror: delete instead of moving to '_SureCopy Removed'")
    sub.add_parser("list")
    for name in ("resume", "report", "verify", "mirror"):
        sub.add_parser(name).add_argument("job", type=int)
    a = ap.parse_args(argv)
    store = Store(a.db)

    if a.cmd == "copy":
        spec = JobSpec(a.name or ", ".join(s.rstrip("/").split("/")[-1] for s in a.sources),
                       a.sources, [Destination(d, VerifyLevel(a.verify)) for d in a.dests], a.exclude,
                       on_existing=ExistingPolicy(a.existing), mirror=a.mirror,
                       mirror_delete=a.mirror and a.mirror_delete_permanently)
        job_id, result = plan_job(store, spec)
        for w in result.warnings:
            print(f"warning: {w}")
        if job_id is None:
            for p in result.problems:
                print(f"can't start: {p}")
            return 2
        print(f"Job {job_id}: {len(result.files)} files, {human(result.total_bytes)}")
        return _run(store, job_id)
    if a.cmd == "list":
        for job_id, name, status, _ in store.jobs():
            print(f"{job_id:>4}  {status.value:<17} {name}")
        return 0
    if a.cmd == "resume":
        resume_job(store, a.job)
        return _run(store, a.job)
    if a.cmd == "report":
        print(report_text(store, a.job))
        return 0
    if a.cmd == "mirror":
        return _mirror(store, a.job)
    if a.cmd == "verify":
        print(verify_later(store, a.job, lambda f: print(f"checking {f}")))
        print(report_text(store, a.job))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
