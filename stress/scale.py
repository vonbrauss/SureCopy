from common import *
import shutil, tempfile, time
from surecopy.store import Store
from surecopy.engine import Engine, CHECKPOINT_BYTES
from surecopy.jobs import plan_job, verify_later
from surecopy.models import JobSpec, Destination, VerifyLevel, JobStatus

R = tempfile.mkdtemp(prefix="scale-", dir=BASE)
src = os.path.join(R, "src", "Show")
rnd = random.Random(1)
MB = 1 << 20; chunk = 8 * MB
t = time.time()
FULL = os.environ.get("STRESS_FULL") == "1"   # full run: 20,000 files and a 1.1 GB file
for i in range(20000 if FULL else 5000):     # many small files in a nested tree
    d = f"proj{i % 7}/sub{i % 53}/deep{i % 5}"
    wfile(os.path.join(src, d, f"doc_{i:05}.txt"), rnd.randbytes(rnd.randint(0, 64 * 1024)))
for n in weird_names():
    wfile(os.path.join(src, "weird", n), rnd.randbytes(rnd.randint(0, 5000)))
deep = os.path.join(src, *[f"level{i}" for i in range(30)], "deep.txt"); wfile(deep, b"deep")
os.makedirs(os.path.join(src, "empty", "dirs", "here"))
for size in (0, 1, chunk - 1, chunk, chunk + 1, CHECKPOINT_BYTES - 1, CHECKPOINT_BYTES, CHECKPOINT_BYTES + 1,
             300 * MB, 1100 * MB if FULL else 120 * MB):
    wfile(os.path.join(src, "big", f"size_{size}.bin"), os.urandom(size) if size < 400 * MB else b"")
    if size >= 400 * MB:
        with open(os.path.join(src, "big", f"size_{size}.bin"), "wb") as f:
            for _ in range(size // MB): f.write(os.urandom(MB))
total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(src) for f in fs)
nfiles = sum(len(fs) for _, _, fs in os.walk(src))
print(f"made {nfiles} files, {total / 1e9:.2f} GB in {time.time() - t:.0f}s")

dests = [os.path.join(R, n) for n in ("A_fast", "B_source", "C_full")]
for d in dests: os.makedirs(d)
store = Store(os.path.join(R, "j.db"))
spec = JobSpec("scale", [src], [Destination(dests[0]), Destination(dests[1], VerifyLevel.SOURCE_HASH),
                                Destination(dests[2], VerifyLevel.FULL)])
job, res = plan_job(store, spec)
print("planned:", len(res.files), "files, warnings:", len(res.warnings), "problems:", res.problems)
t = time.time()
st = Engine(store).run(job)
el = time.time() - t
print(f"status {st.value} in {el:.1f}s: {nfiles / el:.0f} files/s, {total / el / 1e6:.0f} MB/s source read rate")
ok = all([compare(src, os.path.join(d, "Show"), os.path.basename(d)) for d in dests])
print("empty dirs copied:", all(os.path.isdir(os.path.join(d, "Show", "empty", "dirs", "here")) for d in dests))
c = store.run_summary(job); print("summary", c)
t = time.time(); vl = verify_later(store, job); print("verify later", vl, f"{time.time() - t:.0f}s")
# re-run: everything should be skipped, fast
j2, _ = plan_job(store, spec); t = time.time(); st2 = Engine(store).run(j2)
print(f"re-run {st2.value} in {time.time() - t:.1f}s, summary {store.run_summary(j2)}")
print("OVERALL", "PASS" if ok and st == JobStatus.DONE and st2 == JobStatus.DONE and vl["mismatch"] == 0 else "FAIL")
rmtree(R)
