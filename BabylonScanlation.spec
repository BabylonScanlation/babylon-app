# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# EXCLUSIONES: Librerías pesadas detectadas que no se usan en el código fuente.
excluded_modules = [
    'tkinter', 'test', 'unittest', 'pydoc', 
    'matplotlib', 'pandas', 'scipy', 
    'notebook', 'share', 'curses',
    'playwright', 'node', 'PIL.SpiderImagePlugin',
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
    'PySide6.QtNetwork', 'PySide6.QtSvg', 'PySide6.QtSvgWidgets',
    'shiboken6.QtNetwork', 'shiboken6.QtSvg',
    'shiboken6.QtWebEngineCore', 'shiboken6.QtWebEngineWidgets',
    'shiboken6.QtQuick', 'shiboken6.QtQml', 'shiboken6.Qt3DCore',
    'shiboken6.Qt3DRender', 'shiboken6.QtCharts'
]

a = Analysis(
    ['bbsl_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('BBSL', 'BBSL'),
        ('app_media/img-aux/logo.png', 'app_media/img-aux'),
        ('app_media/img-aux/icono.ico', 'app_media/img-aux'),
        ('.env', '.')
    ],
    hiddenimports=[],
    hookspath=['hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules, 
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
    optimize=2, # Optimización de bytecode (elimina docstrings y asserts)
)

# FILTRO AGRESIVO DE BINARIOS (Nuestro "strip" manual)
binaries_to_remove = [
    'opengl32sw', 'Qt6Pdf', 'Qt6Network', 'Qt6Svg', 'Qt6WebEngine', 
    'Qt6Quick', 'Qt6Qml', 'Qt63D', 'Qt6Designer', 'Qt6Sql',
    'libcrypto-3-x64', 'libssl-3-x64',
    'playwright', 'node.exe', 'ffmpeg' # Eliminamos drivers pesados de Playwright y FFmpeg si se cuelan
]

a.binaries = [
    x for x in a.binaries 
    if not any(rem in x[0] for rem in binaries_to_remove)
]

# Filtrar traducciones de Qt (pueden ocupar varios MBs)
a.datas = [x for x in a.datas if not 'translations' in x[0].lower()]

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
    upx_path='dev_tools/upx-5.0.2-win64', 
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, 
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app_media/img-aux/icono.ico',
)
