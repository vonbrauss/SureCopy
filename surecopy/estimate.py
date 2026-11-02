"""Learn how fast copies run between two drives, from the copies themselves.

Every finished pass records, per destination: bytes moved, files copied and the time spent actually
copying (skipped files don't count), tagged with the source drive, the destination drive and whether
Full verify was on. For a drive pair, SureCopy fits

    seconds = bytes / speed + files * per_file_cost

to the recent records (newer ones count more). The two numbers let one model estimate a folder of
50,000 documents and a handful of 100 GB files equally well.
"""

from __future__ import annotations

from dataclasses import dataclass

from .store import Store

RECENT = 30            # records used per drive pair
DECAY = 0.85           # weight of each older record relative to the next newer one
MIN_SECONDS = 2.0      # records shorter than this say more about noise than speed
MIN_WORK_BYTES = 64 * 1024 * 1024
MIN_WORK_FILES = 100
MIN_RUNS_TO_JUDGE = 2  # one past copy can't tell big-file speed from per-file cost, so don't grade yet


@dataclass
class Model:
    sec_per_byte: float
    sec_per_file: float
    runs: int              # how many past copies it's based on

    def seconds(self, nbytes: int, nfiles: int) -> float:
        return nbytes * self.sec_per_byte + nfiles * self.sec_per_file

    @property
    def bytes_per_sec(self) -> float | None:
        return 1 / self.sec_per_byte if self.sec_per_byte > 0 else None


def enough_work(nbytes: int, nfiles: int, seconds: float) -> bool:
    """Is this much copying enough to learn from, or to judge fairly?"""
    return seconds >= MIN_SECONDS and (nbytes >= MIN_WORK_BYTES or nfiles >= MIN_WORK_FILES)


def fit(samples: list[tuple[int, int, float]]) -> Model | None:
    """Weighted least squares for seconds = a*bytes + b*files with a, b >= 0. Newest sample first."""
    pts = [(b, f, s, DECAY ** i) for i, (b, f, s) in enumerate(samples[:RECENT])]
    if not pts:
        return None
    sbb = sum(w * b * b for b, f, s, w in pts)
    sff = sum(w * f * f for b, f, s, w in pts)
    sbf = sum(w * b * f for b, f, s, w in pts)
    sbs = sum(w * b * s for b, f, s, w in pts)
    sfs = sum(w * f * s for b, f, s, w in pts)

    def err(a, c):
        return sum(w * (a * b + c * f - s) ** 2 for b, f, s, w in pts)

    candidates = []
    det = sbb * sff - sbf * sbf
    if det > 1e-9 * max(sbb * sff, 1e-300):            # the mix of big and small files varied enough
        a = (sbs * sff - sfs * sbf) / det
        c = (sfs * sbb - sbs * sbf) / det
        if a >= 0 and c >= 0:
            candidates.append((a, c))
    if sbb > 0:
        candidates.append((sbs / sbb, 0.0))                  # all time explained by bytes
    if sff > 0:
        candidates.append((0.0, sfs / sff))                  # all time explained by file count
    if not candidates:
        return None
    a, c = min(candidates, key=lambda ac: err(*ac))
    return Model(a, c, len(pts))


def model_for(store: Store, src_key: str | None, dst_key: str | None, full: bool,
              exclude_job: int | None = None, before: float | None = None) -> Model | None:
    """What past copies between these two drives say. None if there's no usable history."""
    if not src_key or not dst_key:
        return None
    return fit(store.speed_samples(src_key, dst_key, full, exclude_job=exclude_job, before=before,
                                   min_seconds=MIN_SECONDS))


def pace(model: Model | None, nbytes: int, nfiles: int, seconds: float) -> float | None:
    """1.0 = as fast as usual between these drives. None if there isn't enough to judge."""
    if model is None or model.runs < MIN_RUNS_TO_JUDGE or not enough_work(nbytes, nfiles, seconds):
        return None
    return model.seconds(nbytes, nfiles) / seconds


def work_needed(files, dest_root: str) -> tuple[int, int]:
    """(bytes, files) that actually need copying to dest_root: files already there with the same size
    and date are skipped by the copy, so they don't count toward the estimate."""
    import os
    from .fsio import same_mtime
    nbytes = nfiles = 0
    for f in files:
        try:
            st = os.stat(os.path.join(dest_root, f.relative))
            if st.st_size == f.size and same_mtime(st.st_mtime_ns, f.mtime_ns):
                continue
        except OSError:
            pass
        nbytes += f.size
        nfiles += 1
    return nbytes, nfiles


@dataclass
class DestEstimate:
    root: str
    nbytes: int
    nfiles: int
    seconds: float | None
    runs: int


def estimate_job(store: Store, spec, files) -> list[DestEstimate]:
    """Per destination: what needs copying and, from past copies between the same drives, how long."""
    from .drives import sources_key, volume_for
    src = sources_key(spec.sources)
    out = []
    for d in spec.destinations:
        nbytes, nfiles = work_needed(files, d.root)
        vol = volume_for(d.root)
        m = model_for(store, src, vol.key if vol else None, d.verify.value == "full")
        out.append(DestEstimate(d.root, nbytes, nfiles, m.seconds(nbytes, nfiles) if m else None,
                                m.runs if m else 0))
    return out
