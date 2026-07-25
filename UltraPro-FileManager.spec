# -*- mode: python ; coding: utf-8 -*-

import sys

from ultrapro_version import __version__


windows_version_info = None
if sys.platform == 'win32':
    from PyInstaller.utils.win32 import versioninfo

    numeric_version = tuple(int(part) for part in __version__.split('.'))
    numeric_version = (*numeric_version, *(0 for _ in range(4 - len(numeric_version))))
    windows_version_info = versioninfo.VSVersionInfo(
        ffi=versioninfo.FixedFileInfo(
            filevers=numeric_version,
            prodvers=numeric_version,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,
            fileType=0x1,
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            versioninfo.StringFileInfo([
                versioninfo.StringTable('040904B0', [
                    versioninfo.StringStruct('CompanyName', 'UltraPro'),
                    versioninfo.StringStruct('FileDescription', 'UltraPro File Manager'),
                    versioninfo.StringStruct('FileVersion', __version__),
                    versioninfo.StringStruct('InternalName', 'UltraPro-FileManager'),
                    versioninfo.StringStruct('OriginalFilename', 'UltraPro-FileManager.exe'),
                    versioninfo.StringStruct('ProductName', 'UltraPro File Manager'),
                    versioninfo.StringStruct('ProductVersion', __version__),
                ]),
            ]),
            versioninfo.VarFileInfo([
                versioninfo.VarStruct('Translation', [1033, 1200]),
            ]),
        ],
    )


a = Analysis(
    ['ultrapro_qt.py'],
    pathex=[],
    binaries=[],
    datas=[('qml', 'qml')],
    hiddenimports=[
        'filecmp', 'plistlib', 'queue', 'shutil', 'subprocess', 'tempfile',
        'PIL.Image', 'ultrapro_core', 'ultrapro_updater',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickControls2',
    ],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if sys.platform == 'darwin':
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name='UltraPro-FileManager', debug=False, strip=False, upx=True,
        console=False, argv_emulation=False, target_arch=None,
        codesign_identity=None, entitlements_file=None,
    )
    bundle = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=True, upx_exclude=[],
        name='UltraPro-FileManager',
    )
    app = BUNDLE(
        bundle,
        name='UltraPro File Manager.app',
        icon='assets/ultrapro-icon.png',
        bundle_identifier='com.ultrapro.filemanager',
        version=__version__,
        info_plist={
            'CFBundleDisplayName': 'UltraPro File Manager',
            'CFBundleName': 'UltraPro File Manager',
            'CFBundleShortVersionString': __version__,
            'CFBundleVersion': __version__,
            'NSPrincipalClass': 'NSApplication',
            'NSHighResolutionCapable': True,
        },
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name='UltraPro-FileManager', debug=False,
        bootloader_ignore_signals=False, strip=False, upx=True,
        upx_exclude=[], console=False, disable_windowed_traceback=False,
        version=windows_version_info,
        icon='assets/ultrapro-icon.png',
    )
    bundle = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=True, upx_exclude=[],
        name='UltraPro-FileManager',
    )
