from common import *
import shutil, signal, subprocess, tempfile
from surecopy.store import Store
from surecopy.engine import Engine
from surecopy.jobs import plan_job, mirror_preview, approve_mirror, decline_mirror
from surecopy.models import JobSpec, Destination, VerifyLevel, JobStatus
from surecopy.mirror import REMOVED_FOLDER

def run(seed, rounds=12, kill_mirror=False):
    rnd = random.Random(seed)
    R = tempfile.mkdtemp(prefix="sync-", dir=BASE)
    src = os.path.join(R, "src", "Show")
    clock = [1_700_000_000]
    def tick():
        clock[0] += rnd.randint(5, 500); return clock[0]
    def put(rel, data):
        p = os.path.join(src, rel); wfile(p, data); t = tick(); os.utime(p, (t, t))
    def files():
        return [os.path.relpath(os.path.join(dp, n), src) for dp, _, fs in os.walk(src) for n in fs if not n.startswith("._")]
    for i in range(300):
        put(f"f{i % 5}/s{i % 11}/doc{i}.txt", rnd.randbytes(rnd.randint(0, 20000)))
    dests = [os.path.join(R, n) for n in ("A", "B")]
    for d in dests: os.makedirs(d)
    db = os.path.join(R, "j.db"); store = Store(db)
    spec = JobSpec("sync", [src], [Destination(dests[0]), Destination(dests[1], VerifyLevel.FULL)], mirror=True)
    all_ok = True
    removed_hashes = set()
    for rnd_i in range(rounds):
        if rnd_i:   # mutate the source
            fl = files(); stats = {"add": 0, "edit": 0, "delete": 0, "rename": 0, "dir_rename": 0, "dir_delete": 0}
            for _ in range(rnd.randint(5, 40)):
                op = rnd.choice(list(stats)); fl = files()
                if op == "add":
                    put(f"f{rnd.randint(0, 6)}/new{rnd.randint(0, 10**6)}.txt", rnd.randbytes(rnd.randint(0, 9000)))
                elif op == "edit" and fl:
                    rel = rnd.choice(fl); put(rel, rnd.randbytes(rnd.randint(0, 9000)))
                elif op == "delete" and fl:
                    rel = rnd.choice(fl); removed_hashes.add(sha(os.path.join(src, rel))); os.unlink(os.path.join(src, rel))
                elif op == "rename" and fl:
                    rel = rnd.choice(fl); new = os.path.join(os.path.dirname(rel), f"renamed{rnd.randint(0, 10**6)}.txt")
                    removed_hashes.add(sha(os.path.join(src, rel))); os.rename(os.path.join(src, rel), os.path.join(src, new))
                elif op in ("dir_rename", "dir_delete"):
                    dirs = [os.path.relpath(dp, src) for dp, _, _ in os.walk(src) if dp != src]
                    if not dirs: continue
                    d = rnd.choice(dirs); full = os.path.join(src, d)
                    for dp, _, fs in os.walk(full):
                        for n in fs: removed_hashes.add(sha(os.path.join(dp, n)))
                    if op == "dir_rename": os.rename(full, full + f"_r{rnd.randint(0, 999)}")
                    else: rmtree(full)
                stats[op] += 1
        before = {d: snapshot(os.path.join(d, "Show")) if os.path.isdir(os.path.join(d, "Show")) else {}
                  for d in dests}
        job, res = plan_job(store, spec)
        st = Engine(store).run(job)
        approved = 0
        if st == JobStatus.CONFIRM_MIRROR:
            prev = mirror_preview(store, job)
            approved = sum(len(p.files) for p in prev)
            approve_mirror(store, job)
            if kill_mirror:
                store.close()
                for attempt in range(30):     # hard-kill while mirror removals are happening
                    p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker.py"), db, str(job), str(1 << 20)],
                                         stdout=subprocess.PIPE, text=True)
                    try:
                        out, _ = p.communicate(timeout=rnd.uniform(0.01, 0.4)); st = JobStatus(out.split()[-1]); break
                    except subprocess.TimeoutExpired:
                        p.kill(); p.wait()
                store = Store(db)
            else:
                st = Engine(store).run(job)
        ok = st == JobStatus.DONE and all([compare(src, os.path.join(d, "Show"), f"round {rnd_i} {os.path.basename(d)}")
                                           for d in dests])
        # every destination file that disappeared in this run must be in _SureCopy Removed, byte-identical
        for d in dests:
            after = snapshot(os.path.join(d, "Show"))
            binned = snapshot(os.path.join(d, REMOVED_FOLDER)) if os.path.isdir(os.path.join(d, REMOVED_FOLDER)) else {}
            binned_by_path = {}
            for k, v in binned.items():          # "<stamp> <job>/Show/<rel>"
                binned_by_path.setdefault(k.split(os.sep, 1)[1], set()).add(v[2])
            gone = {rel: v for rel, v in before[d].items() if rel not in after}
            lost = [rel for rel, v in gone.items() if v[2] not in binned_by_path.get(os.path.join("Show", rel), set())]
            if lost:
                ok = False; print(f"    {len(lost)} of {len(gone)} removed files NOT recoverable on {d}: {lost[:3]}")
            elif gone:
                print(f"    {len(gone)} removed files all recoverable on {os.path.basename(d)}")
        print(f"  round {rnd_i}: status {st.value}, mirror approved {approved} -> {'PASS' if ok else 'FAIL'}")
        all_ok &= ok
    rmtree(R)
    return all_ok

res = [run(1), run(2), run(3, kill_mirror=True), run(4, kill_mirror=True)]
print("OVERALL", "PASS" if all(res) else "FAIL")
