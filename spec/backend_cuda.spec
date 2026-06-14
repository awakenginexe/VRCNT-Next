# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


a = Analysis(
    ['..\\src-python\\mainloop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('./../src-python/models/overlay/fonts', 'fonts/'),
        ('./../src-python/models/translation/translation_settings/prompt', 'translation_settings/prompt/'),
        ('./../src-python/models/translation/translation_settings/languages', 'translation_settings/languages/'),
        ('./../.venv_cuda/Lib/site-packages/zeroconf', 'zeroconf/'),
        ('./../.venv_cuda/Lib/site-packages/openvr', 'openvr/'),
        ('./../.venv_cuda/Lib/site-packages/faster_whisper', 'faster_whisper/'),
        ('./../.venv_cuda/Lib/site-packages/hf_xet', 'hf_xet/'),
        ('./../.venv_cuda/Lib/site-packages/sherpa_onnx', 'sherpa_onnx/'),
        ('./../.venv_cuda/Lib/site-packages/vosk', 'vosk/'),
        ('./../.venv_cuda/Lib/site-packages/onnx_asr', 'onnx_asr/'),
        ('./../.venv_cuda/Lib/site-packages/onnxruntime', 'onnxruntime/'),
        *collect_data_files('translators'),
        *copy_metadata('sherpa-onnx'),
        *copy_metadata('onnx-asr'),
        *copy_metadata('translators'),
        ],
    hiddenimports=[
        'ctranslate2',
        'translators',
        'models.translation.translation_plamo',
        'models.translation.translation_gemini',
        'models.translation.translation_openai',
        'models.translation.translation_groq',
        'models.translation.translation_openrouter',
        'models.translation.translation_lmstudio',
        'models.translation.translation_ollama',
        'sherpa_onnx',
        'vosk',
        'onnx_asr',
        'onnxruntime',
        'onnxruntime.capi._pybind_state',
        *collect_submodules('translators'),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pandas', 'matplotlib', 'PyQt5'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VRCT-sidecar-x86_64-pc-windows-msvc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='.',
)
