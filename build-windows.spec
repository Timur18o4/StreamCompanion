from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

root = Path(SPECPATH)
hidden = collect_submodules('streamlink.plugins') + collect_submodules('streamlink_cli')
hidden += ['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
           'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'sounddevice',
           '_sounddevice_data', 'numpy', 'PIL.Image', 'mss', 'streamlink']
a = Analysis(
    [str(root / 'desktop.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'web'), 'web')] + collect_data_files('streamlink') + collect_data_files('_sounddevice_data'),
    hiddenimports=hidden,
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'torch', 'tensorflow', 'pytest', 'IPython', 'jupyter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='StreamCompanion',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='StreamCompanion')
