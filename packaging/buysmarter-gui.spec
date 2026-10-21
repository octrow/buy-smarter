# PyInstaller one-dir build of the desktop app (ADR 0003, openspec desktop-packaging).
# Build from the repo root: uv run pyinstaller packaging/buysmarter-gui.spec --noconfirm
# Check the result:         dist/buysmarter/buysmarter --self-test
import os
import sys

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

NAME = "buysmarter"

datas = [
    # Alembic reads env.py and the revisions as files, not from the PYZ archive.
    *collect_data_files("buysmarter", include_py_files=True, includes=["migrations/**"]),
    *collect_data_files("buysmarter", includes=["gui/theme/**"]),
    # Playwright's driver (Node) runs Ozon on the system Chrome; no browser is bundled.
    *collect_data_files("playwright", includes=["driver/**"]),
    # importlib.metadata lookups: buysmarter.__version__, keyring's backend entry points, ...
    *copy_metadata("buysmarter", recursive=True),
]
hiddenimports = [
    "aiosqlite",  # SQLAlchemy loads the store's async driver by name from DATABASE_URL
    # What migrations/env.py and the revisions import: Alembic runs them as files, unanalysed.
    "logging.config",
    "buysmarter.db.models",
    "buysmarter.llm.models",
    "sqlalchemy.dialects.postgresql",
    *collect_submodules("keyring.backends"),
]
# Qt pieces the app never loads; PySide6-Essentials already leaves out WebEngine, 3D, Multimedia.
excludes = [
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "tkinter",
    "asyncpg",  # the desktop store is SQLite; Postgres is for the server deployment
]

a = Analysis(
    ["../src/buysmarter/gui/__main__.py"],
    binaries=collect_dynamic_libs("curl_cffi"),
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name=NAME,
    console=False,  # a window app; --self-test still exits with its code
    icon=os.path.join(SPECPATH, "windows", "buysmarter.ico") if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)

if sys.platform == "darwin":
    from importlib.metadata import version

    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=os.path.join(SPECPATH, "macos", "buysmarter.icns"),
        bundle_identifier="io.github.octrow.buysmarter",
        version=version("buysmarter"),
        info_plist={"CFBundleDisplayName": "buy-smarter", "NSHighResolutionCapable": True},
    )
