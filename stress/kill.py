from common import *
import shutil, signal, subprocess, tempfile, time
from surecopy.store import Store
from surecopy.jobs import plan_job
from surecopy.models import JobSpec, Destination, VerifyLevel, ExistingPolicy

def trial(seed, checkpoint, policy=ExistingPolicy.UPDATE, preexisting=False):
    rnd = random.Random(seed)
    R = tempfile.mkdtemp(prefix="kill-", dir=BASE)
    src = os.path.join(R, "src", "Show"); MB = 1 << 20
    for i in range(1500):
        wfile(os.path.join(src, f"d{i % 13}", f"f{i}.dat"), rnd.randbytes(rnd.randint(0, 40000)))
    for i in range(6):
        wfile(os.path.join(src, "big", f"b{i}.mov"), os.urandom(rnd.randint(20, 90) * MB))
    dests = [os.path.join(R, n) for n in ("A", "B", "C")]
    for d in dests: os.makedirs(d)
    if preexisting:   # older versions already at destination A: exercises update-in-place under kills
        for i in range(0, 1500, 10):
            p = os.path.join(dests[0], "Show", f"d{i % 13}", f"f{i}.dat"); wfile(p, b"old version")
            os.utime(p, ns=(10**18, 10**18))
        p = os.path.join(dests[0], "Show", "big", "b0.mov"); wfile(p, b"old big"); os.utime(p, ns=(10**18, 10**18))
    db = os.path.join(R, "j.db")
    store = Store(db)
    job, _ = plan_job(store, JobSpec("k", [src], [Destination(dests[0]), Destination(dests[1], VerifyLevel.SOURCE_HASH),
                                                  Destination(dests[2], VerifyLevel.FULL)], on_existing=policy))
    store.close()
    kills = 0; final = None
    for attempt in range(80):
        p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py"), db, str(job), str(checkpoint)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        delay = rnd.uniform(0.05, 1.5)
        try:
            out, _ = p.communicate(timeout=delay)
            final = out.strip().splitlines()[-1] if out.strip() else "?"
            if "Traceback" in out: print(out[-2000:])
            break
        except subprocess.TimeoutExpired:
            p.send_signal(signal.SIGKILL); p.wait(); kills += 1
    ok = final == "done" and all([compare(src, os.path.join(d, "Show"), f"seed {seed} {os.path.basename(d)}")
                                  for d in dests])
    print(f"trial seed={seed} checkpoint={checkpoint} preexisting={preexisting}: {kills} hard kills, "
          f"final={final} -> {'PASS' if ok else 'FAIL'}")
    rmtree(R)
    return ok

results = [trial(s, c, preexisting=p) for s, c, p in ((3, 4 << 20, True), (5, 16 << 20, False), (6, 1 << 20, False),
                                                       (7, 2 << 20, True), (8, 64 << 20, True), (9, 1 << 20, False))]
print("OVERALL", "PASS" if all(results) else "FAIL")
