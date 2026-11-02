from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs, get_package_paths, copy_metadata

root = Path(SPECPATH)
hidden = collect_submodules('streamlink.plugins') + collect_submodules('streamlink_cli')
hidden += ['uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
           'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'sounddevice',
           '_sounddevice_data', 'numpy', 'PIL.Image', 'mss', 'streamlink']
hidden += ['sherpa_onnx']
sherpa_root = Path(get_package_paths('sherpa_onnx')[1])
speech_binaries = collect_dynamic_libs('sherpa_onnx')
# Both recognition backends use ONNX. Put the newer compatible runtime at the
# common dependency location as well, so import order cannot pick an older DLL.
if (sherpa_root / 'lib' / 'onnxruntime.dll').exists():
    speech_binaries.insert(0, (str(sherpa_root / 'lib' / 'onnxruntime.dll'), '.'))
a = Analysis(
    [str(root / 'desktop.py')],
    pathex=[str(root)],
    binaries=speech_binaries,
    datas=[(str(root / 'web'), 'web')] + collect_data_files('streamlink') + collect_data_files('_sounddevice_data') + collect_data_files('faster_whisper') + copy_metadata('sherpa-onnx') + copy_metadata('sherpa-onnx-core'),
    hiddenimports=hidden,
    excludes=['tkinter', 'matplotlib', 'scipy', 'pandas', 'torch', 'tensorflow', 'pytest', 'IPython', 'jupyter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='StreamCompanion',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='StreamCompanion')
