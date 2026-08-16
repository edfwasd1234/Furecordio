# -*- mode: python ; coding: utf-8 -*-
# Builds DubMaker.exe + DubStage.exe into ONE folder (dist/DubSuite) so they
# share packs/, dubs/, tools/ and settings. ffmpeg is bundled once.
from PyInstaller.utils.hooks import collect_all

sd_datas, sd_binaries, sd_hidden = collect_all("sounddevice")

_common = dict(
    pathex=["."],
    binaries=[("tools/ffmpeg.exe", "tools")] + sd_binaries,
    datas=sd_datas,
    hiddenimports=["numpy", "PIL", "PIL.ImageTk"] + sd_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "demucs", "matplotlib", "IPython"],
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
