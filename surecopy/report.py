"""Plain-text job reports. Always says which verify level each copy got."""

from __future__ import annotations

import time
from collections import Counter

from .engine import HASH_NAME, human
from .models import FileStatus, VerifyLevel
from .store import Store

LEVEL_LABELS = {v.value: v.label for v in VerifyLevel}


def report_text(store: Store, job_id: int) -> str:
    spec = store.spec(job_id)
    files = {f.id: f for f in store.files(job_id)}
    dests = store.dests(job_id)
    copies = store.all_copies(job_id)
    status = store.job_status(job_id)
    total = sum(f.size for f in files.values())

    out = [f"SureCopy report: {spec.name}",
           f"Status: {status.value.replace('_', ' ')}",
           f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
           f"Files: {len(files)}  Size: {human(total)}  Hash: {HASH_NAME} (when used)",
           f"Existing files: {spec.on_existing.label}",
           "Mirror: " + ("off" if not spec.mirror else
                         "on, delete permanently" if spec.mirror_delete else "on, move to _SureCopy Removed"),
           "", "Sources:"]
    out += [f"  {s}" for s in spec.sources]
    out.append("")
    for d in dests:
        mine = [c for c in copies if c.dest_id == d.id]
        counts = Counter(c.status for c in mine)
        levels = Counter(c.verified_level for c in mine if c.status in (FileStatus.COPIED, FileStatus.UPDATED))
        out.append(f"Destination: {d.root}")
        out.append(f"  Verify level: {d.verify.label}")
        if d.message:
            out.append(f"  Note: {d.message}")
        out.append("  " + ", ".join(f"{counts.get(s, 0)} {s.value}" for s in FileStatus))
        if levels:
            out.append("  Copied files checked by: " +
                       ", ".join(f"{n} {LEVEL_LABELS.get(lv, lv)}" for lv, n in levels.items()))
        gone = [r for r in store.removals(job_id) if r[1] == d.id and not r[3]]
        if gone:
            states = Counter(r[6] for r in gone)
            out.append("  Mirror: " + ", ".join(f"{n} {s}" for s, n in states.items()))
            for r in gone:
                if r[6] in ("removed", "failed"):
                    out.append(f"  - {r[2]}: {r[7]}")
        issues = [c for c in mine if c.status in (FileStatus.FAILED, FileStatus.CONFLICT)
                  or "MISMATCH" in c.message]
        for c in issues:
            out.append(f"  ! {files[c.file_id].relative}: {c.status.value}: {c.message}")
        out.append("")
    warnings = store.warnings(job_id)
    if warnings:
        out.append("Warnings found when planning:")
        out += [f"  {w}" for w in warnings]
    return "\n".join(out).rstrip() + "\n"
