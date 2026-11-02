"""Shared helpers for stress runs. Independent checker uses sha256, not the app's xxhash."""
import hashlib, os, random, sys, tempfile, unicodedata
# Where test files go. Default: the system temp folder. Set STRESS_DIR to test a real drive, e.g.
#   STRESS_DIR=/Volumes/Scratch .venv/bin/python stress/run_all.py
BASE = os.environ.get("STRESS_DIR") or tempfile.gettempdir()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PARTIAL = (".surecopy-partial", ".surecopy-old")
SKIP = {".DS_Store"}

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 20):
            h.update(b)
    return h.hexdigest()

def is_mac_metadata(name):
    """AppleDouble ("._name") and .DS_Store files that macOS creates by itself on exFAT/FAT drives and
    network shares. SureCopy skips them on purpose, and macOS makes fresh ones on the destination."""
    return name.startswith("._") or name == ".DS_Store"

def rmtree(path):
    """Delete a test folder. On exFAT/SMB, deleting a file also removes its "._" twin, so ignore
    files that disappear on their own."""
    import shutil
    shutil.rmtree(path, ignore_errors=True)

def snapshot(root, ignore_top=("_SureCopy Removed",)):
    """{relative: (size, mtime_s, sha256)} for every regular file (macOS metadata files excluded)."""
    out = {}
    for dp, dn, fn in os.walk(root):
        if os.path.relpath(dp, root) == ".":
            dn[:] = [d for d in dn if d not in ignore_top]
        for n in fn:
            if is_mac_metadata(n):
                continue
            p = os.path.join(dp, n)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, sha(p))
    return out

def leftovers(root):
    return [os.path.join(dp, n) for dp, _, fn in os.walk(root) for n in fn
            if n.endswith(PARTIAL) and not n.startswith("._")]

def compare(src_root, dst_root, label):
    a, b = snapshot(src_root), snapshot(dst_root)
    missing = sorted(set(a) - set(b)); extra = sorted(set(b) - set(a))
    same = lambda x, y: x[0] == y[0] and x[2] == y[2] and abs(x[1] - y[1]) <= 2_000_000_000   # FAT rounds to 2 s
    diff = sorted(k for k in set(a) & set(b) if not same(a[k], b[k]))
    left = leftovers(dst_root)
    ok = not (missing or extra or diff or left)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {len(a)} src files, {len(b)} dest files, "
          f"missing {len(missing)}, extra {len(extra)}, different {len(diff)}, partial leftovers {len(left)}")
    for name, lst in (("missing", missing), ("extra", extra), ("different", diff), ("leftover", left)):
        for x in lst[:5]:
            print(f"      {name}: {x}", (a.get(x), b.get(x)) if name == "different" else "")
    return ok

def wfile(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)

def weird_names():
    return ["Café NFC.txt", unicodedata.normalize("NFD", "Résumé NFD.txt"), "emoji 🎬 clip.mov",
            "spaces   in   name .wav".strip(), "#hash %percent &amp.txt", ".hidden_but_wanted",
            "a" * 240 + ".txt", "UPPER.TXT", "dots.in.the.name.v2.final.mov", "ünïcödé/文件.txt",
            "brackets [1] (2) {3}.psd", "quote's.txt"]
