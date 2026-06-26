# PyInstaller spec — Naver Rank Checker 배포용 (onedir: 실행·시작이 더 빠름)
from PyInstaller.utils.hooks import collect_all

block_cipher = None

datas = []
binaries = []
hiddenimports = [
    "customtkinter",
    "tkinter",
    "tkinter.ttk",
    "naver_rank_checker",
    "naver_rank_checker.gui",
    "naver_rank_checker.checker",
    "naver_rank_checker.rank_search",
    "naver_rank_checker.storage",
    "naver_rank_checker.constants",
    "naver_rank_checker.cli",
    "naver_rank_checker.updater",
    "naver_rank_checker.runtime",
]

for pkg in ("customtkinter",):
    tmp = collect_all(pkg)
    datas += tmp[0]
    binaries += tmp[1]
    hiddenimports += tmp[2]

a = Analysis(
    ["run_gui.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "matplotlib", "pandas", "scipy", "IPython", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NaverRankChecker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NaverRankChecker",
)
