# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os
import subprocess

# UPX: PyInstaller 6.x ignora `upx_path` en EXE(). El binario se localiza
# mediante CONF['upx_dir'] (solo se puede pasar por CLI `--upx-dir`).
# Lo inyectamos desde el spec para que `upx=True` funcione sin flags.
from PyInstaller.config import CONF

_upx_dir = None
for _d in sorted(os.listdir(os.path.join(SPECPATH, 'dev_tools'))):
    if not (_d.startswith('upx-') and _d.endswith('win64')):
        continue
    _cand = os.path.join(SPECPATH, 'dev_tools', _d)
    try:
        subprocess.check_output(
            [os.path.join(_cand, 'upx'), '-V'],
            stdin=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            encoding='utf-8',
        )
        _upx_dir = _cand
        break
    except Exception:
        continue
if _upx_dir is not None:
    CONF['upx_dir'] = _upx_dir
    CONF['upx_available'] = True


def _walk_datas(src_root, dst_root, skip_dirs=()):
    """Incluye archivo por archivo (evita que PyInstaller expanda el directorio
    recursivamente e incluya python_ocr/models)."""
    out = []
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        rel = os.path.relpath(root, src_root)
        dest = os.path.join(dst_root, rel) if rel != "." else dst_root
        for file_name in files:
            out.append((os.path.join(root, file_name), dest))
    return out

# EXCLUSIONES: Librerías pesadas detectadas que no se usan en el código fuente.
excluded_modules = [
    'tkinter', 'test', 'unittest', 'pydoc', 
    'matplotlib', 'pandas', 'scipy', 
    'notebook', 'share', 'curses',
    'PIL.SpiderImagePlugin',
    'encodings.cp037', 'encodings.cp424', 'execjs',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
    'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.Qt3DCore',
    'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.Qt3DExtras', 'PySide6.Qt3DAnimation', 'PySide6.QtCharts',
    'PySide6.QtDataVisualization', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtXml', 'PySide6.QtPdf',
    'PySide6.QtPdfWidgets', 'PySide6.QtPositioning', 'PySide6.QtLocation',
    'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtWebView',
    'PySide6.QtDesigner', 'PySide6.QtHelp', 'PySide6.QtHttpServer',
    'PySide6.QtRemoteObjects', 'PySide6.QtScxml', 'PySide6.QtSensors',
    'PySide6.QtSerialBus', 'PySide6.QtSerialPort', 'PySide6.QtStateMachine',
    'PySide6.QtTextToSpeech', 'PySide6.QtUiTools',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'OpenGL',
    'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
    'shiboken6.QtSvg',
    'shiboken6.QtWebEngineCore', 'shiboken6.QtWebEngineWidgets',
    'shiboken6.QtQuick', 'shiboken6.QtQml', 'shiboken6.Qt3DCore',
    'shiboken6.Qt3DRender', 'shiboken6.QtCharts'
]

# .env embebido en el EXE (el usuario lo pidió así; config.py también acepta
# un .env junto al .exe como respaldo sin recompilar).
env_datas = [('.env', '.')] if os.path.exists('.env') else []

# Playwright no tiene hook oficial: hay que meter driver/node.exe + package/cli.js.
# compute_driver_executable busca <playwright>/driver/{node.exe,package/cli.js}.
# Los navegadores NO se empaquetan (channel=/executable_path del sistema).
playwright_binaries = []
playwright_datas = []
try:
    import playwright as _pw_mod
    _pw_root = os.path.dirname(_pw_mod.__file__)
    _pw_driver = os.path.join(_pw_root, 'driver')
    _pw_node = os.path.join(_pw_driver, 'node.exe')
    if os.path.isfile(_pw_node):
        playwright_binaries.append((_pw_node, 'playwright/driver'))
    _pw_pkg = os.path.join(_pw_driver, 'package')
    if os.path.isdir(_pw_pkg):
        playwright_datas.append((_pw_pkg, 'playwright/driver/package'))
except Exception as _pw_err:
    print(f'[spec] playwright driver no disponible: {_pw_err}')

a = Analysis(
    ['bbsl_app.py'],
    pathex=[],
    binaries=playwright_binaries,
    datas=[
        ('BBSL', 'BBSL'),
        ('styles', 'styles'),
        ('app_media', 'app_media'),
    ] + env_datas
      + playwright_datas
      + _walk_datas('babylon_downloaders', 'babylon_downloaders',
                    skip_dirs=('bookwalker', '__pycache__'))
      + _walk_datas('app_tools', 'app_tools', skip_dirs=('python_ocr', 'models', '__pycache__')),
    hiddenimports=[
        'win32crypt',
        'playwright',
        'playwright.sync_api',
        'playwright.async_api',
        'playwright._impl',
        'playwright._impl._driver',
        'playwright._impl._transport',
    ],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules, 
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=1, # Cambiado de 2 a 1 para evitar errores con Numpy
)

# FILTRO AGRESIVO DE BINARIOS (Nuestro "strip" manual)
binaries_to_remove = [
    'opengl32sw', 'Qt6Pdf', 'Qt6Svg', 'Qt6WebEngine', 
    'Qt6Quick', 'Qt6Qml', 'Qt63D', 'Qt6Designer', 'Qt6Sql',
    'libcrypto-3-x64', 'libssl-3-x64',
    'ffmpeg' # FFmpeg no hace falta (Qt multimedia desactivado en runtime)
]

a.binaries = [
    x for x in a.binaries 
    if not any(rem in x[0] for rem in binaries_to_remove)
]

# Filtrar traducciones de Qt (pueden ocupar varios MBs)
a.datas = [x for x in a.datas if not 'translations' in x[0].lower()]

# Excluir perfil Chromium, __pycache__ y runtime JSON/TXT (sesiones/cookies/HAR).
# NO tocamos .env: va embebido a propósito.
_RUNTIME_UNSAFE = (
    'bw_profile',
    'babylon_downloaders\\bookwalker',
    'babylon_downloaders/bookwalker',
    '__pycache__',
    'bookwalker_cookies.txt',
    'bookwalker_har.json',
    'bookwalker_member.json',
    'bookwalker_storage.json',
    'bookwalker_session.json',
    'cf_clearance.json',
)
def _is_unsafe_data(item):
    src, dest = item[0], item[1]
    low_src = src.replace('/', '\\').lower()
    low_dest = str(dest).replace('/', '\\').lower()
    return any(p in low_src or p in low_dest for p in _RUNTIME_UNSAFE)

a.datas = [x for x in a.datas if not _is_unsafe_data(x)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='BabylonScanlation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False, # Desactivado por falta de herramientas en el sistema
    upx=True,
    upx_exclude=[
        'python3*.dll',
        'vcruntime*.dll', 
        '_ssl.pyd',
        'Qt6Core.dll',
        'Qt6Gui.dll',
        'Qt6Widgets.dll',
    ],
    runtime_tmpdir=None,
    console=False, 
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_media/img-aux/icono.ico',
)
