#!/usr/bin/env python3
"""Launch PySide6 tablet preview: Idle + Home dashboards.

Run from the ``device-ui`` directory::

    cd mini-pc/device-ui
    pip install -r requirements-qt.txt
    PYTHONPATH=src python -m qt.qt_kiosk_demo

Optional::

    PYTHONPATH=src python -m qt.qt_kiosk_demo --fullscreen
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent

if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(_SRC))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from qt.home_screen import HomeScreen
from qt.idle_screen import IdleScreen


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MeetingBoxQt")

    tabs = QTabWidget()
    tabs.setDocumentMode(True)

    idle = IdleScreen()
    idle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    home = HomeScreen()

    tabs.addTab(idle, "Idle (892×573 logical)")
    tabs.addTab(home, "Home (1024×600 logical)")

    hdr = QLabel("PySide6 • Tab between Idle and Home")
    hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
    hdr.setStyleSheet("padding:10px;color:#eaeaea;background:#111;")

    core = QWidget()
    vl = QVBoxLayout(core)
    vl.setContentsMargins(0, 0, 0, 0)
    vl.addWidget(hdr)
    vl.addWidget(tabs, stretch=1)

    mw = QMainWindow()
    mw.resize(1366, 768)
    mw.setCentralWidget(core)
    mw.setWindowTitle("MeetingBox · Qt kiosk")

    if "--fullscreen" in sys.argv:
        mw.showFullScreen()
    else:
        mw.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
