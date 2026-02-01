# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

# EXCLUSIONES: Librerías pesadas detectadas que no se usan en el código fuente.
# Se eliminó 'distutils' de la lista por seguridad en Python 3.12.
# Se añade OpenGL a petición del usuario (ahorro ~20MB).
excluded_modules = [
    'tkinter', 'test', 'unittest', 'pydoc', 
    'matplotlib', 'pandas', 'scipy', 
    'notebook', 'share',
    'curses',
    'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'OpenGL'
]

a = Analysis(
    ['bbsl_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('BBSL', 'BBSL'),
        ('app_media', 'app_media'),
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
)

# FILTRO MANUAL DE BINARIOS: Eliminar opengl32sw.dll (renderizador de software)
# Esto ahorra ~20MB extra si el usuario tiene GPU.
a.binaries = [x for x in a.binaries if not x[0].lower().startswith('opengl32sw')]

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
    strip=False,
    upx=True,
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
