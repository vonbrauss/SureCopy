"""Check a built SureCopy.app: no libraries loaded from outside the app (other than macOS's own),
no symlinks pointing out of it, and which architectures and minimum macOS versions it contains.
Usage: audit_bundle.py APP OUTPUT.json   (exit code 1 if anything points outside the app)"""
import collections
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2])
MACHO = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
external, escaping, archs, minos, dev_refs = [], [], collections.Counter(), collections.Counter(), []
count = 0
for p in root.rglob("*"):
    if p.is_symlink():
        if not p.resolve().is_relative_to(root):
            escaping.append(str(p.relative_to(root)))
        continue
    if not p.is_file():
        continue
    with p.open("rb") as fh:
        magic = fh.read(4)
    if magic not in MACHO:
        if p.suffix in (".py", ".txt", ".cfg", ".json", ".toml") and b"/Users/" in p.read_bytes()[:2_000_000]:
            dev_refs.append(str(p.relative_to(root)))
        continue
    count += 1
    for line in subprocess.check_output(["/usr/bin/otool", "-L", str(p)], text=True).splitlines()[1:]:
        dep = line.strip().split(" (")[0]
        if dep.startswith("/") and not dep.startswith(("/usr/lib/", "/System/Library/")):
            external.append([str(p.relative_to(root)), dep])
    for line in subprocess.check_output(["/usr/bin/otool", "-l", str(p)], text=True).splitlines():
        if line.strip().startswith("minos "):
            minos[line.strip().split()[1]] += 1
    archs[subprocess.check_output(["/usr/bin/lipo", "-archs", str(p)], text=True).strip()] += 1

result = {"bundle": str(root), "mach_o_files": count, "architectures": dict(archs),
          "minimum_macos_versions": dict(minos), "libraries_from_outside_the_app": external,
          "symlinks_pointing_outside": escaping, "files_mentioning_/Users/": dev_refs}
out.write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
sys.exit(1 if external or escaping else 0)
