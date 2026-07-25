"""Collect only the QML modules used by TransferDesk.

PyInstaller's generic PySide6 hook deliberately collects every QML module in
the wheel.  That includes WebEngine, 3D, charts and multimedia even though the
TransferDesk interface imports only Qt Quick Controls, Layouts and Dialogs.
"""

from pathlib import PurePath

from PyInstaller.utils.hooks.qt import add_qt6_dependencies, pyside6_library_info


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)
qml_binaries, qml_datas = pyside6_library_info.collect_qtqml_files()

_QML_DESTINATION = PurePath(pyside6_library_info.qt_rel_dir) / "qml"
_MODULE_DIRECTORIES = {
    "Qt/labs/folderlistmodel",
    "QtQml",
    "QtQml/Models",
    "QtQml/WorkerScript",
    "QtQuick",
    "QtQuick/Controls",
    "QtQuick/Controls/Basic",
    "QtQuick/Controls/Basic/impl",
    "QtQuick/Controls/impl",
    "QtQuick/Dialogs",
    "QtQuick/Dialogs/quickimpl",
    "QtQuick/Layouts",
    "QtQuick/Templates",
    "QtQuick/Window",
}


def _is_required_qml_item(item: tuple[str, str]) -> bool:
    destination = PurePath(item[1])
    try:
        module = destination.relative_to(_QML_DESTINATION).as_posix()
    except ValueError:
        return False
    return module in _MODULE_DIRECTORIES


binaries += [item for item in qml_binaries if _is_required_qml_item(item)]
datas += [item for item in qml_datas if _is_required_qml_item(item)]
