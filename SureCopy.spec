# SureCopy as a standalone Apple Silicon macOS app. Built by packaging/build_app.sh.
import sys
from pathlib import Path

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from surecopy import __version__  # noqa: E402

icon = root / "packaging" / "SureCopy.icns"
a = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "surecopy" / "gui" / "assets"), "surecopy/gui/assets"), (str(root / "README.md"), ".")],
    hiddenimports=["PySide6.QtSvg", "xxhash"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtTest", "tkinter", "pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="SureCopy",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, argv_emulation=False, target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="SureCopy")
if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="SureCopy.app", icon=str(icon) if icon.is_file() else None,
        bundle_identifier="com.surecopy.app",
        info_plist={
            "CFBundleName": "SureCopy",
            "CFBundleDisplayName": "SureCopy",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "14.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            "NSHighResolutionCapable": True,
            "NSDocumentsFolderUsageDescription": "Choose folders and files to copy, or where copies should go.",
            "NSDesktopFolderUsageDescription": "Choose folders and files to copy, or where copies should go.",
            "NSDownloadsFolderUsageDescription": "Choose downloaded files to copy.",
            "NSRemovableVolumesUsageDescription": "Copy to and from external drives.",
            "NSNetworkVolumesUsageDescription": "Copy to and from network storage such as Avid Nexis.",
        },
    )
