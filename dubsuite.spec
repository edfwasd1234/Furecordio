# -*- mode: python ; coding: utf-8 -*-
# Builds DubMaker.exe + DubStage.exe into ONE folder (dist/DubSuite) so they
# share packs/, dubs/, tools/ and settings. ffmpeg is bundled once.
from PyInstaller.utils.hooks import collect_all

# Bundle everything the apps need at runtime, incl. Demucs + PyTorch for the
# optional backing-track (vocal) separation.
_pkgs = ["sounddevice", "demucs", "torch", "soundfile", "sphn",
         "safetensors", "lameenc", "julius", "einops"]
_datas, _binaries, _hidden = [], [], []
for _p in _pkgs:
    _d, _b, _h = collect_all(_p)
    _datas += _d
    _binaries += _b
    _hidden += _h

_common = dict(
    pathex=["."],
    binaries=[("tools/ffmpeg.exe", "tools")] + _binaries,
    datas=_datas,
    hiddenimports=["numpy", "PIL", "PIL.ImageTk", "demucs.separate"] + _hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "IPython", "torchvision", "torchaudio"],
    noarchive=False,
)

a_stage = Analysis(["DubStage.pyw"], **_common)
a_maker = Analysis(["DubMaker.pyw"], **_common)

pyz_stage = PYZ(a_stage.pure)
exe_stage = EXE(pyz_stage, a_stage.scripts, [], exclude_binaries=True,
                name="DubStage", console=False)

pyz_maker = PYZ(a_maker.pure)
exe_maker = EXE(pyz_maker, a_maker.scripts, [], exclude_binaries=True,
                name="DubMaker", console=False)

coll = COLLECT(
    exe_stage, a_stage.binaries, a_stage.zipfiles, a_stage.datas,
    exe_maker, a_maker.binaries, a_maker.zipfiles, a_maker.datas,
    strip=False, upx=False, name="DubSuite",
)
