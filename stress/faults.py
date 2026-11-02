from common import *
import errno, shutil, tempfile, threading
from surecopy.store import Store
from surecopy.engine import Engine, Controls
from surecopy.jobs import plan_job, resume_job, verify_later
from surecopy.models import JobSpec, Destination, VerifyLevel, JobStatus

def trial(seed):
    rnd = random.Random(seed)
    R = tempfile.mkdtemp(prefix="fault-", dir=BASE)
    src = os.path.join(R, "src", "Show"); MB = 1 << 20
    for i in range(800):
        wfile(os.path.join(src, f"d{i % 9}", f"f{i}.dat"), rnd.randbytes(rnd.randint(0, 30000)))
    for i in range(4):
        wfile(os.path.join(src, "big", f"b{i}.mov"), os.urandom(rnd.randint(10, 40) * MB))
    dests = [os.path.join(R, n) for n in ("A", "B", "C")]
    for d in dests: os.makedirs(d)
    store = Store(os.path.join(R, "j.db"))
    job, _ = plan_job(store, JobSpec("f", [src], [Destination(dests[0]), Destination(dests[1], VerifyLevel.FULL),
                                                  Destination(dests[2], VerifyLevel.SOURCE_HASH)], chunk_mib=1))
    events = {"eio": 0, "unplug": 0, "edit": 0, "pause": 0}
    unplugged = set()
    lock = threading.Lock()
    controls = Controls()

    def fault(root, rel, pos):
        r = rnd.random()
        with lock:
            if r < 0.01:
                events["eio"] += 1
                raise OSError(errno.EIO, "Input/output error (injected)")
            if r < 0.013 and root not in unplugged:          # yank the drive mid-write
                unplugged.add(root); os.rename(root, root + "-gone"); events["unplug"] += 1
                raise OSError(errno.ENXIO, "Device not configured (injected)")
            if r < 0.016:                                     # someone edits a source file mid-copy
                target = os.path.join(src, f"d{rnd.randint(0, 8)}", f"f{rnd.randrange(0, 800, 9)}.dat")
                if os.path.exists(target):
                    with open(target, "ab") as fh: fh.write(b"edited")
                    events["edit"] += 1
            if r < 0.017:
                events["pause"] += 1; controls.pause()

    passes = 0; st = None
    while passes < 60:
        passes += 1
        for d in list(unplugged):                             # plug drives back in between passes
            if rnd.random() < 0.7:
                os.rename(d + "-gone", d); unplugged.discard(d)
        controls.reset()
        resume_job(store, job)
        st = Engine(store, controls, fault=fault if passes < 40 else None, checkpoint_bytes=2 * MB).run(job)
        if st == JobStatus.DONE and not unplugged:
            break
    for d in list(unplugged):
        os.rename(d + "-gone", d)
    ok = st == JobStatus.DONE and all([compare(src, os.path.join(d, "Show"), f"seed {seed} {os.path.basename(d)}")
                                       for d in dests])
    vl = verify_later(store, job)
    ok = ok and vl["mismatch"] == 0 and vl["missing"] == 0
    print(f"trial {seed}: {passes} passes, events {events}, verify later {vl} -> {'PASS' if ok else 'FAIL'}")
    rmtree(R)
    return ok

print("OVERALL", "PASS" if all([trial(s) for s in range(1, 7)]) else "FAIL")
