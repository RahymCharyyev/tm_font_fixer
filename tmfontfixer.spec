# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --noconfirm --clean tmfontfixer.spec
#   macOS   -> dist/TM Font Fixer.app
#   Windows -> dist/TM-Font-Fixer.exe (один файл)
import sys

sys.path.insert(0, SPECPATH)
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402
from tmfontfixer import APP_NAME, __version__  # noqa: E402

hidden = collect_submodules("fontTools.ttLib.tables") + ["brotli"]

a = Analysis(
    ["main.py"],
    pathex=[SPECPATH],
    datas=[("assets/icon.png", "assets")],
    hiddenimports=hidden,
    excludes=["tkinter", "numpy", "matplotlib", "PIL", "scipy", "pandas",
              "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSql",
              "PySide6.QtOpenGL", "PySide6.QtPdf", "PySide6.QtSvg", "PySide6.QtDBus"],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_NAME, console=False,
              argv_emulation=False, icon="assets/icon.png", upx=False)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=APP_NAME)
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon="assets/icon.png",
        bundle_identifier="tm.fontfixer.app",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Font",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["public.font", "public.truetype-ttf-font", "public.opentype-font",
                                       "org.w3.woff", "org.w3.woff2"],
            }],
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="TM-Font-Fixer", console=False,
              icon="assets/icon.png", upx=False)
