"""Run every SureCopy stress test and print a summary.

    .venv/bin/python stress/run_all.py                 # temp folder, quick sizes (about 10 minutes)
    STRESS_FULL=1 .venv/bin/python stress/run_all.py   # 20,000 files and a 1.1 GB file
    STRESS_DIR=/Volumes/Scratch .venv/bin/python stress/run_all.py   # test on a real drive or share

Uses only throwaway files it creates itself, inside a new folder under STRESS_DIR, and deletes them
afterward. It never touches anything else. Checks every copy with sha256, independently of the app.
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = [("Scale and integrity", "scale.py"), ("Hard kills and resume", "kill.py"),
         ("Random faults and disconnects", "faults.py"), ("Sync and mirror fuzzing", "syncfuzz.py"),
         ("GUI, every action", "guistress.py")]

target = os.environ.get("STRESS_DIR")
if target:
    if not os.path.isdir(target):
        print(f"STRESS_DIR doesn't exist: {target}")
        if os.path.isdir("/Volumes"):
            print("Drives connected right now:")
            for v in sorted(os.listdir("/Volumes")):
                print(f"  /Volumes/{v}")
        sys.exit(2)
    if not os.access(target, os.W_OK):
        print(f"Can't write to {target}. Pick a folder you can write to.")
        sys.exit(2)
    import shutil
    free = shutil.disk_usage(target).free
    print(f"Testing on {target} ({free / 1e9:.0f} GB free). Uses up to about "
          f"{10 if os.environ.get('STRESS_FULL') == '1' else 3} GB while running, then cleans up.")

results = []
for name, script in TESTS:
    print(f"\n=== {name} ===", flush=True)
    t = time.time()
    p = subprocess.run([sys.executable, "-u", os.path.join(HERE, script)], capture_output=True, text=True)
    out = p.stdout + p.stderr
    lines = [l for l in out.splitlines() if "FAIL" in l or "OVERALL" in l or l.startswith(("trial", "status"))]
    print("\n".join(lines[-15:]))
    ok = "OVERALL PASS" in out and p.returncode == 0
    if not ok:
        print(out[-3000:] or f"(no output; the test exited with code {p.returncode})")
    results.append((name, ok, time.time() - t))

print("\n=== Summary ===")
for name, ok, secs in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({secs:.0f}s)")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
