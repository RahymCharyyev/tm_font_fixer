"""Скриншот окна в offscreen-режиме: QT_QPA_PLATFORM=offscreen python3 tests/gui_screenshot.py out.png fonts..."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtCore import QTimer
from tmfontfixer import gui

out, fonts = sys.argv[1], sys.argv[2:]
app = gui.App(sys.argv[:1])
w = gui.MainWindow(); app.window = w
w.resize(1280, 820); w.show()
shots = []
def snap(name):
    w.grab().save(name)
if os.environ.get("EMPTY"):
    QTimer.singleShot(300, lambda: (snap(out), app.quit()))
else:
    w.add_paths(fonts)
    def check():
        if w.pending == 0:
            sel = int(os.environ.get("SELECT", "0"))
            w.tree.setCurrentItem(w.entries[sel].item)
            QTimer.singleShot(200, lambda: (snap(out), app.quit()))
        else:
            QTimer.singleShot(100, check)
    QTimer.singleShot(100, check)
app.exec()
print("pending", w.pending, [ (e.item.text(0), e.item.text(1), e.item.text(2)) for e in w.entries])
