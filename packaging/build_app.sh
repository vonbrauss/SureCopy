#!/bin/bash
# Build SureCopy.app for Apple Silicon Macs and check it.
#
#   cd ~/PycharmProjects/SureCopy
#   bash packaging/build_app.sh
#
# Result: dist/SureCopy.app and dist/SureCopy-<version>-macOS-arm64.zip
# Logs:   work/build-<version>/
# Uses only temporary test folders. Never touches /Applications, real drives, or SureCopy's
# job history in ~/Library/Application Support/SureCopy.
set -u -o pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ] || [ "$(uname -m)" != "arm64" ]; then
  echo "This builds the Apple Silicon Mac app, so run it on an Apple Silicon Mac."; exit 1
fi

# The project's own Python environment, created once.
if [ ! -x .venv/bin/python ]; then
  echo "Setting up .venv (one time)"
  PY312=$(command -v python3.12 || true)
  [ -n "$PY312" ] || { echo "Python 3.12 not found. Install it with: brew install python@3.12"; exit 1; }
  "$PY312" -m venv .venv || exit 1
fi
PY=.venv/bin/python
echo "Checking build tools (PyInstaller, PySide6, xxhash)"
$PY -m pip install -q -r requirements-build.txt || { echo "Couldn't install the build tools (see above)."; exit 1; }

V=$($PY -c 'import surecopy; print(surecopy.__version__)')
W="work/build-$V"; APP="dist/SureCopy.app"; ZIP="dist/SureCopy-$V-macOS-arm64.zip"
rm -rf "$W"; mkdir -p "$W" dist
fail() { echo; echo "FAILED: $1"; echo "Details: $2"; exit 1; }

echo "1/7 Tests"
QT_QPA_PLATFORM=offscreen $PY -B -m unittest discover -s tests -t . > "$W/tests.log" 2>&1 \
  || fail "the test suite" "$W/tests.log"
tail -3 "$W/tests.log" | grep -E "^(Ran|OK)"

echo "2/7 App icon"
if [ ! -f packaging/SureCopy.icns ] || [ packaging/icon-1024.png -nt packaging/SureCopy.icns ]; then
  SET="$W/SureCopy.iconset"; mkdir -p "$SET"
  for s in 16 32 128 256 512; do
    sips -z $s $s packaging/icon-1024.png --out "$SET/icon_${s}x${s}.png" > /dev/null
    sips -z $((s * 2)) $((s * 2)) packaging/icon-1024.png --out "$SET/icon_${s}x${s}@2x.png" > /dev/null
  done
  iconutil -c icns "$SET" -o packaging/SureCopy.icns || fail "making the icon" "$SET"
fi

echo "3/7 Self-test from source"
$PY -B main.py --self-test "$PWD/$W/source-self-test.json" > "$W/source-self-test.log" 2>&1 \
  || fail "the source self-test" "$W/source-self-test.log"

echo "4/7 Building SureCopy.app (a minute or two)"
rm -rf "$APP" dist/SureCopy
PYINSTALLER_CONFIG_DIR="$PWD/work/pyinstaller-cache" $PY -m PyInstaller --noconfirm --clean \
  --workpath "$W/pyinstaller" --distpath dist SureCopy.spec > "$W/build.log" 2>&1 \
  || fail "PyInstaller" "$W/build.log"
rm -rf dist/SureCopy          # the loose folder PyInstaller leaves beside the .app

echo "5/7 Signature and bundle audit"
codesign --verify --deep --strict "$APP" > "$W/codesign.log" 2>&1 || fail "the code signature" "$W/codesign.log"
$PY -B packaging/audit_bundle.py "$APP" "$W/bundle-audit.json" > "$W/bundle-audit.log" 2>&1 \
  || fail "the bundle audit (something points outside the app)" "$W/bundle-audit.json"

echo "6/7 Self-test of the built app, with the development setup hidden"
env -i HOME="$HOME" PATH=/usr/bin:/bin TMPDIR="${TMPDIR:-/tmp}" "$APP/Contents/MacOS/SureCopy" \
  --self-test "$PWD/$W/app-self-test.json" > "$W/app-self-test.log" 2>&1 \
  || fail "the built app's self-test" "$W/app-self-test.log"

echo "7/7 ZIP"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP" || fail "making the ZIP" "$ZIP"

echo
echo "Built SureCopy $V"
echo "  App: $PWD/$APP ($(du -sh "$APP" | cut -f1))"
echo "  ZIP: $PWD/$ZIP"
echo
echo "Try it:      open \"$APP\""
echo "Install it:  drag dist/SureCopy.app into Applications (quit any running copy first)"
