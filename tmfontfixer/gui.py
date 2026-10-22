"""Окно приложения (PySide6)."""
from __future__ import annotations

import os
import sys
import traceback

from fontTools.pens.basePen import BasePen
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPalette,
                           QPen)
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPushButton, QRadioButton, QSizePolicy, QSplitter, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from . import APP_NAME, __version__, core
from .cli import FONT_EXT, collect

SAMPLE = "Türkmenistan · Aşgabat · Ňň Şş Ýý Žž Ää Çç Öö Üü"
ADDED_COLOR = QColor("#2f6fed")
CHECK_COLOR = QColor("#d98a00")
BAD_COLOR = QColor("#d64545")
OK_COLOR = QColor("#2e9e5b")


def display_name(rep, fallback=""):
    if not rep or not rep.family:
        return fallback
    fam, sty = rep.family.strip(), (rep.style or "").strip()
    if sty and sty.lower() not in fam.lower():
        return f"{fam} {sty}"
    return fam


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, rel)


def is_dark(widget=None):
    pal = (widget or QApplication.instance()).palette()
    return pal.color(QPalette.Window).lightness() < 128


# ------------------------------------------------------------------ фоновая работа

class _Signals(QObject):
    done = Signal(object)


class Job(QRunnable):
    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args
        self.signals = _Signals()

    def run(self):
        try:
            res = self.fn(*self.args)
        except Exception as e:  # noqa: BLE001
            res = e
            traceback.print_exc()
        self.signals.done.emit(res)


class Entry:
    """Один шрифт в списке."""

    def __init__(self, path):
        self.path = path
        self.original = None
        self.patched = None
        self.report = None
        self.error = None
        self.item = None
        self.adjust = {}
        self.generation = 0


def analyze(path):
    e = Entry(path)
    try:
        e.original = core.load_font(path)
        e.patched, e.report = core.process(path)
    except core.FontFixError as ex:
        e.error = str(ex)
        if e.original is not None:
            e.report = core.describe(e.original, path)
    except Exception as ex:  # noqa: BLE001
        e.error = f"Непредвиденная ошибка: {ex}"
    return e


def reprocess(path, adjust):
    return core.process(path, adjust=dict(adjust))


def save_all(entries, out_dir, rename, file_suffix):
    saved, failed = [], []
    for e in entries:
        try:
            font = e.patched
            if rename:
                font, _ = core.process(e.path, rename_suffix=rename, adjust=dict(e.adjust))
            out = core.output_path(e.path, out_dir, file_suffix)
            core.save(font, out)
            saved.append(out)
        except Exception as ex:  # noqa: BLE001
            failed.append((e.path, str(ex)))
    return saved, failed


# ------------------------------------------------------------------ превью

class _QtPathPen(BasePen):
    def __init__(self, gs):
        super().__init__(gs)
        self.path = QPainterPath()
        self.path.setFillRule(Qt.WindingFill)

    def _moveTo(self, p):
        self.path.moveTo(*p)

    def _lineTo(self, p):
        self.path.lineTo(*p)

    def _curveToOne(self, a, b, c):
        self.path.cubicTo(*a, *b, *c)

    def _qCurveToOne(self, a, b):
        self.path.quadTo(*a, *b)

    def _closePath(self):
        self.path.closeSubpath()


class Preview(QWidget):
    """Рисует строку прямо из контуров шрифта (видно ровно то, что в файле)."""

    def __init__(self, title):
        super().__init__()
        self.title = title
        self.font_obj = None
        self.highlight = set()
        self.text = SAMPLE
        self._cache = {}
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_font(self, font, highlight=()):
        self.font_obj = font
        self.highlight = set(highlight)
        self._cache = {}
        self.update()

    def set_text(self, text):
        self.text = text or SAMPLE
        self.update()

    def _path(self, gs, name):
        if name not in self._cache:
            pen = _QtPathPen(gs)
            try:
                gs[name].draw(pen)
            except Exception:  # noqa: BLE001
                pass
            self._cache[name] = pen.path
        return self._cache[name]

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        fg = pal.color(QPalette.WindowText)
        sub = QColor(fg)
        sub.setAlpha(140)
        r = self.rect().adjusted(0, 0, -1, -1)
        bg = pal.color(QPalette.Base)
        p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 40), 1))
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(r), 10, 10)
        f = QFont(self.font())
        f.setPointSizeF(max(f.pointSizeF() - 1, 8))
        p.setFont(f)
        p.setPen(sub)
        p.drawText(QRectF(r).adjusted(12, 6, -12, 0), Qt.AlignLeft | Qt.AlignTop, self.title)
        if self.font_obj is None:
            return
        font = self.font_obj
        gs = font.getGlyphSet()
        cmap = font.getBestCmap() or {}
        hmtx = font["hmtx"]
        upm = font["head"].unitsPerEm
        asc = font["hhea"].ascent if "hhea" in font else upm * 0.8
        desc = font["hhea"].descent if "hhea" in font else -upm * 0.2
        items, x = [], 0
        for ch in self.text:
            g = cmap.get(ord(ch))
            if g is None:
                w = upm * 0.55
                items.append((None, x, w, ch))
            else:
                w = hmtx[g][0]
                items.append((g, x, w, ch))
            x += w
        total = max(x, 1)
        area = QRectF(r).adjusted(14, 26, -14, -10)
        scale = min(area.width() / total, area.height() / (asc - desc))
        base_y = area.top() + (area.height() - (asc - desc) * scale) / 2 + asc * scale
        x0 = area.left()
        for g, gx, w, ch in items:
            if g is None:
                rect = QRectF(x0 + gx * scale + w * scale * 0.1, base_y - upm * 0.7 * scale,
                              w * scale * 0.8, upm * 0.7 * scale)
                pen = QPen(BAD_COLOR, 1.2, Qt.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect)
                continue
            path = self._path(gs, g)
            p.save()
            p.translate(x0 + gx * scale, base_y)
            p.scale(scale, -scale)
            p.setPen(Qt.NoPen)
            p.setBrush(ADDED_COLOR if g in self.highlight else fg)
            p.drawPath(path)
            p.restore()


# ------------------------------------------------------------------ сетка букв

class _Cell(QLabel):
    clicked = Signal(str)

    def mousePressEvent(self, e):
        self.clicked.emit(self.text())


class LetterGrid(QWidget):
    selected = Signal(str)

    def __init__(self):
        super().__init__()
        self.current = None
        self._rep = None
        grid = QGridLayout(self)
        grid.setSpacing(6)
        grid.setContentsMargins(0, 0, 0, 0)
        self.cells = {}
        for i, (ch, *_r) in enumerate(core.LETTERS):
            lab = _Cell(ch)
            lab.clicked.connect(self._click)
            lab.setCursor(Qt.PointingHandCursor)
            lab.setAlignment(Qt.AlignCenter)
            lab.setMinimumSize(QSize(38, 38))
            f = lab.font()
            f.setPointSizeF(f.pointSizeF() + 5)
            lab.setFont(f)
            row, col = (0 if ch.isupper() else 1), i // 2
            grid.addWidget(lab, row, col)
            self.cells[ch] = lab
        self.set_report(None)

    def _click(self, ch):
        self.current = ch
        self.set_report(self._rep)
        self.selected.emit(ch)

    def set_report(self, rep):
        self._rep = rep
        by = {l.char: l for l in rep.letters} if rep else {}
        dark = is_dark(self)
        for ch, lab in self.cells.items():
            l = by.get(ch)
            if l is None:
                color, tip = QColor(128, 128, 128), "—"
            elif l.status == "present":
                color, tip = OK_COLOR, "Есть в шрифте"
            elif l.status in ("added", "mapped"):
                color = CHECK_COLOR if l.confidence in ("medium", "low") else ADDED_COLOR
                tip = ("Будет добавлена: " if l.status == "added" else "Будет подключена: ") + l.note
            else:
                color, tip = BAD_COLOR, "Нельзя добавить: " + l.note
            bg = QColor(color)
            bg.setAlpha(60 if dark else 34)
            width = 3 if ch == self.current else 1.5
            lab.setStyleSheet(
                f"QLabel {{ background: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
                f" border: {width}px solid {color.name()}; border-radius: 8px; color: palette(window-text); }}")
            lab.setToolTip(f"{ch}  U+{ord(ch):04X}\n{tip}")


class GlyphView(QWidget):
    """Крупно: новая буква рядом с образцом из того же шрифта, с линиями метрик."""

    def __init__(self):
        super().__init__()
        self.font_obj, self.chars, self.highlight = None, [], set()
        self.setMinimumSize(300, 190)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set(self, font, chars, highlight):
        self.font_obj, self.chars, self.highlight = font, chars, set(highlight)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        fg = pal.color(QPalette.WindowText)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(fg.red(), fg.green(), fg.blue(), 40), 1))
        p.setBrush(pal.color(QPalette.Base))
        p.drawRoundedRect(r, 10, 10)
        font = self.font_obj
        if font is None or not self.chars:
            return
        cmap = font.getBestCmap() or {}
        gs = font.getGlyphSet()
        upm = font["head"].unitsPerEm
        os2 = font["OS/2"] if "OS/2" in font else None
        cap = getattr(os2, "sCapHeight", 0) or upm * 0.7
        xh = getattr(os2, "sxHeight", 0) or upm * 0.5
        top, bottom = upm * 1.05, -upm * 0.3
        names = [cmap.get(ord(c)) for c in self.chars]
        widths = [font["hmtx"][n][0] if n else upm // 2 for n in names]
        gap = upm * 0.25
        total = sum(widths) + gap * (len(widths) - 1)
        area = r.adjusted(14, 10, -14, -10)
        sc = min(area.width() / total, area.height() / (top - bottom))
        x0 = area.left() + (area.width() - total * sc) / 2
        base_y = area.top() + (area.height() - (top - bottom) * sc) / 2 + top * sc
        line = QColor(fg)
        line.setAlpha(45)
        p.setPen(QPen(line, 1, Qt.DashLine))
        for y in (0, xh, cap):
            p.drawLine(QPointF(area.left(), base_y - y * sc), QPointF(area.right(), base_y - y * sc))
        x = x0
        for n, w in zip(names, widths):
            if n:
                pen = _QtPathPen(gs)
                gs[n].draw(pen)
                p.save()
                p.translate(x, base_y)
                p.scale(sc, -sc)
                p.setPen(Qt.NoPen)
                p.setBrush(ADDED_COLOR if n in self.highlight else fg)
                p.drawPath(pen.path)
                p.restore()
            x += (w + gap) * sc


class AdjustPanel(QFrame):
    """Ручной сдвиг знака у выбранной добавленной буквы."""
    changed = Signal(str, int, int)

    def __init__(self):
        super().__init__()
        self.setObjectName("adjust")
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        self.view = GlyphView()
        h.addWidget(self.view)
        v = QVBoxLayout()
        v.setSpacing(6)
        self.caption = QLabel("")
        self.caption.setWordWrap(True)
        v.addWidget(self.caption)
        pad = QGridLayout()
        pad.setSpacing(4)
        self.btns = {}
        for key, txt, rr, cc in (("up", "↑", 0, 1), ("left", "←", 1, 0), ("down", "↓", 1, 1), ("right", "→", 1, 2)):
            b = QPushButton(txt)
            b.setFixedSize(40, 32)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(120)
            b.clicked.connect(lambda _=False, k=key: self._move(k))
            pad.addWidget(b, rr, cc)
        v.addLayout(pad)
        self.fine = QCheckBox("мелкий шаг")
        v.addWidget(self.fine)
        self.offset = QLabel("")
        self.offset.setStyleSheet("color: palette(placeholder-text);")
        v.addWidget(self.offset)
        reset = QPushButton("Сбросить")
        reset.clicked.connect(self._reset)
        v.addWidget(reset)
        v.addStretch(1)
        h.addLayout(v)
        h.addStretch(1)
        self.ch, self.upm, self.dx, self.dy = None, 1000, 0, 0

    def show_for(self, font, ch, ref, adj, highlight):
        self.ch = ch
        self.upm = font["head"].unitsPerEm
        self.dx, self.dy = adj
        chars = [ch] + ([ref] if ref else [])
        self.view.set(font, chars, highlight)
        self.caption.setText(f"<b>{ch}</b> рядом с образцом «{ref}» из этого шрифта" if ref else f"<b>{ch}</b>")
        self.offset.setText(f"сдвиг: {self.dx:+d}, {self.dy:+d} ед." if (self.dx or self.dy) else "сдвиг: нет")

    def _move(self, k):
        step = max(1, round(self.upm * (0.002 if self.fine.isChecked() else 0.01)))
        ddx, ddy = {"left": (-step, 0), "right": (step, 0), "up": (0, step), "down": (0, -step)}[k]
        self.dx += ddx
        self.dy += ddy
        self.changed.emit(self.ch, self.dx, self.dy)

    def _reset(self):
        self.dx = self.dy = 0
        self.changed.emit(self.ch, 0, 0)


def ref_char(font, ch):
    """Готовая буква шрифта с тем же знаком и регистром — для сравнения."""
    acc = [l for l in core.LETTERS if l[0] == ch][0][3]
    spec = core.ACCENTS[acc]
    cmap = font.getBestCmap() or {}
    refs = spec["refs_upper"] if ch.isupper() else spec["refs_lower"]
    refs += "".join(l[0] for l in core.LETTERS if l[3] == acc and l[0].isupper() == ch.isupper())
    for r in refs:
        if r != ch and ord(r) in cmap:
            return r
    return None


def legend():
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(14)
    for color, text in ((OK_COLOR, "уже есть"), (ADDED_COLOR, "будет добавлена"),
                        (CHECK_COLOR, "добавлена — проверьте"), (BAD_COLOR, "нельзя добавить")):
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{color.name()}; border-radius:5px;")
        lab = QLabel(text)
        lab.setStyleSheet("color: palette(placeholder-text);")
        h.addWidget(dot)
        h.addWidget(lab)
    h.addStretch(1)
    return w


# ------------------------------------------------------------------ окно

class DropZone(QFrame):
    def __init__(self, on_open):
        super().__init__()
        self.setObjectName("dropzone")
        v = QVBoxLayout(self)
        v.addStretch(1)
        big = QLabel("Ä Ç Ň Ö Ş Ü Ý Ž")
        f = big.font()
        f.setPointSizeF(f.pointSizeF() * 3)
        f.setWeight(QFont.DemiBold)
        big.setFont(f)
        big.setAlignment(Qt.AlignCenter)
        t = QLabel("Перетащите сюда шрифты или папку со шрифтами")
        f2 = t.font()
        f2.setPointSizeF(f2.pointSizeF() + 3)
        t.setFont(f2)
        t.setAlignment(Qt.AlignCenter)
        s = QLabel(".ttf · .otf · .woff · .woff2 · вариативные TrueType")
        s.setAlignment(Qt.AlignCenter)
        s.setStyleSheet("color: palette(placeholder-text);")
        b = QPushButton("Выбрать файлы…")
        b.setObjectName("primary")
        b.setDefault(True)
        b.clicked.connect(on_open)
        b.setFixedWidth(200)
        v.addWidget(big)
        v.addSpacing(14)
        v.addWidget(t)
        v.addWidget(s)
        v.addSpacing(18)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.setAcceptDrops(True)
        self.entries: list[Entry] = []
        self.pool = QThreadPool.globalInstance()
        self.pending = 0
        self.out_dir = None
        self._jobs = set()

        self.stack = QStackedWidget()
        self.drop = DropZone(self.open_files)
        self.stack.addWidget(self.drop)
        self.stack.addWidget(self._build_main())
        self.setCentralWidget(self.stack)
        self._build_menu()
        self._apply_style()

    # -- построение
    def _build_menu(self):
        m = self.menuBar().addMenu("Файл")
        a = QAction("Открыть шрифты…", self)
        a.setShortcut("Ctrl+O")
        a.triggered.connect(self.open_files)
        m.addAction(a)
        a = QAction("Очистить список", self)
        a.triggered.connect(self.clear)
        m.addAction(a)
        h = self.menuBar().addMenu("Справка")
        a = QAction(f"О программе {APP_NAME}", self)
        a.setMenuRole(QAction.AboutRole)
        a.triggered.connect(self.about)
        h.addAction(a)

    def _build_main(self):
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(12)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # слева: список
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Шрифт", "Не хватало", "Результат"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(lambda *_: self.show_selected())
        lv.addWidget(self.tree)
        row = QHBoxLayout()
        b_add = QPushButton("Добавить…")
        b_add.clicked.connect(self.open_files)
        b_rm = QPushButton("Убрать")
        b_rm.clicked.connect(self.remove_selected)
        row.addWidget(b_add)
        row.addWidget(b_rm)
        row.addStretch(1)
        lv.addLayout(row)
        split.addWidget(left)

        # справа: детали
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        rv.setSpacing(10)
        self.title = QLabel("—")
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() + 6)
        f.setWeight(QFont.DemiBold)
        self.title.setFont(f)
        self.subtitle = QLabel("")
        self.subtitle.setStyleSheet("color: palette(placeholder-text);")
        self.subtitle.setWordWrap(True)
        self.subtitle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rv.addWidget(self.title)
        rv.addWidget(self.subtitle)
        self.grid = LetterGrid()
        rv.addWidget(self.grid)
        rv.addWidget(legend())
        self.hint = QLabel("Нажмите на добавленную букву, чтобы рассмотреть её крупно и при необходимости подвинуть знак.")
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        self.hint.setWordWrap(True)
        rv.addWidget(self.hint)
        self.adjust = AdjustPanel()
        self.adjust.setVisible(False)
        self.adjust.changed.connect(self._adjusted)
        self.grid.selected.connect(lambda ch: self._update_adjust())
        rv.addWidget(self.adjust)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setObjectName("notes")
        self.notes.setTextFormat(Qt.RichText)
        rv.addWidget(self.notes)
        self.sample = QLineEdit(SAMPLE)
        self.sample.setPlaceholderText("Текст для просмотра")
        self.sample.setClearButtonEnabled(True)
        self.sample.textChanged.connect(self._sample_changed)
        rv.addWidget(self.sample)
        self.prev_before = Preview("Было")
        self.prev_after = Preview("Станет  (синим — добавленные буквы)")
        rv.addWidget(self.prev_before, 1)
        rv.addWidget(self.prev_after, 1)
        split.addWidget(right)
        split.setSizes([400, 780])
        v.addWidget(split, 1)

        # низ: параметры и сохранение
        bar = QFrame()
        bar.setObjectName("bar")
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(14, 10, 14, 10)
        bh.setSpacing(12)
        self.cb_rename = QCheckBox("Переименовать семейство, добавив:")
        self.cb_rename.setToolTip("Чтобы установить исправленный шрифт рядом с оригиналом,\n"
                                  "а также если лицензия требует сменить имя изменённого шрифта (OFL).")
        self.ed_rename = QLineEdit(" TM")
        self.ed_rename.setFixedWidth(70)
        self.ed_rename.setEnabled(False)
        self.cb_rename.toggled.connect(self.ed_rename.setEnabled)
        bh.addWidget(self.cb_rename)
        bh.addWidget(self.ed_rename)
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        bh.addWidget(sep)
        bh.addWidget(QLabel("Сохранить:"))
        self.rb_near = QRadioButton("рядом с оригиналом (имя-TM)")
        self.rb_dir = QRadioButton("в папку…")
        self.rb_near.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.rb_near)
        grp.addButton(self.rb_dir)
        self.rb_dir.clicked.connect(self.choose_dir)
        bh.addWidget(self.rb_near)
        bh.addWidget(self.rb_dir)
        bh.addStretch(1)
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setObjectName("primary")
        self.btn_save.setMinimumWidth(220)
        self.btn_save.clicked.connect(self.save)
        bh.addWidget(self.btn_save)
        v.addWidget(bar)
        self.status = QLabel("")
        self.status.setStyleSheet("color: palette(placeholder-text);")
        v.addWidget(self.status)
        return root

    def _apply_style(self):
        dark = is_dark(self)
        line = "rgba(255,255,255,0.14)" if dark else "rgba(0,0,0,0.12)"
        zone_bg = "rgba(255,255,255,0.03)" if dark else "rgba(0,0,0,0.02)"
        self.setStyleSheet(f"""
            #dropzone {{ border: 2px dashed {line}; border-radius: 18px; margin: 28px; background: {zone_bg}; }}
            #bar {{ border: 1px solid {line}; border-radius: 12px; }}
            #notes {{ padding: 0px; }}
            QPushButton#primary {{ background: {ADDED_COLOR.name()}; color: white; border: none;
                                   border-radius: 8px; padding: 8px 18px; font-weight: 600; }}
            QPushButton#primary:disabled {{ background: rgba(128,128,128,0.35); color: rgba(255,255,255,0.7); }}
            QPushButton#primary:hover {{ background: #255fd3; }}
        """)

    # -- файлы
    def open_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Выберите шрифты", "",
                                                "Шрифты (*.ttf *.otf *.woff *.woff2);;Все файлы (*)")
        if files:
            self.add_paths(files)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def add_paths(self, paths):
        known = {os.path.abspath(x.path) for x in self.entries}
        files = [p for p in collect(paths) if p.lower().endswith(FONT_EXT) and os.path.abspath(p) not in known]
        if not files:
            if paths:
                self.status.setText("Шрифтов среди перетащенных файлов не найдено.")
            return
        self.stack.setCurrentIndex(1)
        for p in files:
            e = Entry(p)
            item = QTreeWidgetItem([os.path.basename(p), "…", "анализ…"])
            item.setToolTip(0, p)
            e.item = item
            self.tree.addTopLevelItem(item)
            self.entries.append(e)
            self.pending += 1
            job = Job(analyze, p)
            job.signals.done.connect(lambda res, e=e, job=job: self._analyzed(e, res, job))
            self._jobs.add(job)
            self.pool.start(job)
        if self.tree.currentItem() is None:
            self.tree.setCurrentItem(self.entries[0].item)
        self._update_status()

    def _analyzed(self, e, res, job):
        self._jobs.discard(job)
        self.pending -= 1
        if e not in self.entries:
            return
        if isinstance(res, Exception):
            e.error = str(res)
        else:
            e.original, e.patched, e.report, e.error = res.original, res.patched, res.report, res.error
        self._analyzed_update_row(e)
        if self.tree.currentItem() is e.item:
            self.show_selected()
        self._update_status()

    def _analyzed_update_row(self, e):
        it = e.item
        rep = e.report
        if rep and rep.family:
            it.setText(0, display_name(rep))
            it.setToolTip(0, e.path)
        if e.error:
            it.setText(1, "")
            it.setText(2, "✕ не поддерживается")
            it.setForeground(2, BAD_COLOR)
        else:
            it.setText(1, "".join(rep.missing_before) or "—")
            if not rep.missing_before:
                it.setText(2, "✓ всё уже есть")
                it.setForeground(2, OK_COLOR)
            else:
                n, chk, sk = len(rep.added), len(rep.needs_check), len(rep.skipped)
                txt = f"+{n}"
                if chk:
                    txt += f" · {chk} проверить"
                if sk:
                    txt += f" · {sk} нельзя"
                it.setText(2, txt)
                it.setForeground(2, CHECK_COLOR if (chk or sk) else ADDED_COLOR)

    def current(self):
        it = self.tree.currentItem()
        for e in self.entries:
            if e.item is it:
                return e
        return None

    def remove_selected(self):
        e = self.current()
        if not e:
            return
        self.entries.remove(e)
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(e.item))
        if not self.entries:
            self.stack.setCurrentIndex(0)
        self._update_status()
        self.show_selected()

    def clear(self):
        self.entries.clear()
        self.tree.clear()
        self.stack.setCurrentIndex(0)
        self._update_status()

    # -- показ
    def _sample_changed(self, t):
        self.prev_before.set_text(t)
        self.prev_after.set_text(t)

    def show_selected(self):
        e = self.current()
        if e is None or (e.report is None and e.error is None):
            self.title.setText("Анализ…" if e else "—")
            self.subtitle.setText(e.path if e else "")
            self.grid.set_report(None)
            self.notes.setText("")
            self.prev_before.set_font(None)
            self.prev_after.set_font(None)
            return
        rep = e.report
        self.title.setText(display_name(rep, os.path.basename(e.path)))
        self.subtitle.setText(f"{rep.kind + ' · ' if rep and rep.kind else ''}{e.path}")
        self.grid.set_report(rep if not e.error else None)
        notes = []
        if e.error:
            notes.append(f"<b style='color:{BAD_COLOR.name()}'>Не удалось обработать:</b> {e.error}")
        else:
            for l in rep.needs_check:
                notes.append(f"<span style='color:{CHECK_COLOR.name()}'>●</span> <b>{l.char}</b> — {l.note}")
            for l in rep.skipped:
                notes.append(f"<span style='color:{BAD_COLOR.name()}'>●</span> <b>{l.char}</b> — {l.note}")
            for w in rep.warnings:
                notes.append(f"<span style='color:{CHECK_COLOR.name()}'>⚠</span> {w}")
        if rep:
            lic = rep.license.strip().replace("\n", " ")
            if len(lic) > 160:
                lic = lic[:160] + "…"
            lic_line = f"Лицензия: {lic}" if lic else "Лицензия в файле не указана"
            if rep.license_url:
                lic_line += f" (<a href='{rep.license_url}'>ссылка</a>)"
            if rep.copyright:
                lic_line = f"{rep.copyright.strip()}<br>{lic_line}"
            notes.append(f"<span style='color:gray'>{lic_line}<br>"
                         f"Перед использованием убедитесь, что лицензия разрешает изменять шрифт.</span>")
        self.notes.setText("<br>".join(notes))
        self.notes.setOpenExternalLinks(True)
        self.prev_before.set_font(e.original)
        hl = {l.glyph for l in rep.added} if rep and not e.error else set()
        self.prev_after.set_font(e.patched if not e.error else None, hl)
        self._update_adjust()

    def _update_adjust(self):
        e = self.current()
        ch = self.grid.current
        ok = (e is not None and not e.error and e.report is not None and ch is not None)
        letter = None
        if ok:
            letter = next((l for l in e.report.letters if l.char == ch), None)
        if not letter or letter.status != "added":
            self.adjust.setVisible(False)
            self.hint.setVisible(bool(e and e.report and not e.error and e.report.added))
            return
        self.hint.setVisible(False)
        self.adjust.setVisible(True)
        hl = {l.glyph for l in e.report.added}
        self.adjust.show_for(e.patched, ch, ref_char(e.patched, ch), e.adjust.get(ch, (0, 0)), hl)

    def _adjusted(self, ch, dx, dy):
        e = self.current()
        if not e:
            return
        if dx or dy:
            e.adjust[ch] = (dx, dy)
        else:
            e.adjust.pop(ch, None)
        e.generation += 1
        gen = e.generation
        job = Job(reprocess, e.path, dict(e.adjust))
        job.signals.done.connect(lambda res, e=e, gen=gen, job=job: self._reprocessed(e, gen, res, job))
        self._jobs.add(job)
        self.pool.start(job)

    def _reprocessed(self, e, gen, res, job):
        self._jobs.discard(job)
        if gen != e.generation or isinstance(res, Exception):
            return
        e.patched, e.report = res
        self._analyzed_update_row(e)
        if self.current() is e:
            self.show_selected()

    def _savable(self):
        return [e for e in self.entries if e.report and not e.error and e.report.added]

    def _update_status(self):
        n = len(self._savable())
        self.btn_save.setText(f"Сохранить исправленные ({n})" if n else "Сохранить")
        self.btn_save.setEnabled(n > 0 and self.pending == 0)
        total = len(self.entries)
        if self.pending:
            self.status.setText(f"Анализ… осталось {self.pending}")
        elif total:
            ok = sum(1 for e in self.entries if e.report and not e.error and not e.report.missing_before)
            bad = sum(1 for e in self.entries if e.error)
            parts = [f"шрифтов: {total}", f"к исправлению: {n}"]
            if ok:
                parts.append(f"уже полные: {ok}")
            if bad:
                parts.append(f"не поддерживаются: {bad}")
            self.status.setText(" · ".join(parts))
        else:
            self.status.setText("")

    # -- сохранение
    def choose_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для исправленных шрифтов", self.out_dir or "")
        if d:
            self.out_dir = d
            self.rb_dir.setText(f"в папку: {os.path.basename(d) or d}")
            self.rb_dir.setToolTip(d)
        elif not self.out_dir:
            self.rb_near.setChecked(True)

    def save(self):
        items = self._savable()
        if not items:
            return
        out_dir = self.out_dir if self.rb_dir.isChecked() else None
        rename = self.ed_rename.text() if self.cb_rename.isChecked() and self.ed_rename.text().strip() else None
        self.btn_save.setEnabled(False)
        self.status.setText("Сохранение…")
        job = Job(save_all, items, out_dir, rename, "-TM")
        job.signals.done.connect(lambda res, job=job: self._saved(res, job))
        self._jobs.add(job)
        self.pool.start(job)

    def _saved(self, res, job):
        self._jobs.discard(job)
        self._update_status()
        if isinstance(res, Exception):
            QMessageBox.critical(self, APP_NAME, f"Ошибка при сохранении:\n{res}")
            return
        saved, failed = res
        msg = QMessageBox(self)
        msg.setWindowTitle(APP_NAME)
        msg.setIcon(QMessageBox.Information if not failed else QMessageBox.Warning)
        text = f"Сохранено шрифтов: {len(saved)}"
        if failed:
            text += "\n\nНе удалось:\n" + "\n".join(f"{os.path.basename(p)}: {e}" for p, e in failed)
        msg.setText(text)
        if saved:
            msg.setInformativeText("\n".join(os.path.basename(s) for s in saved[:12]) +
                                   ("\n…" if len(saved) > 12 else ""))
            open_btn = msg.addButton("Показать в папке", QMessageBox.AcceptRole)
            msg.addButton("Готово", QMessageBox.RejectRole)
        msg.exec()
        if saved and msg.clickedButton() is open_btn:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(saved[0])))

    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"<b>{APP_NAME}</b> {__version__}<br><br>"
                          "Добавляет в шрифты недостающие туркменские буквы "
                          "Ä Ç Ň Ö Ş Ü Ý Ž (заглавные и строчные), собирая их из "
                          "букв и диакритических знаков самого шрифта.<br><br>"
                          "Шрифты обрабатываются на этом компьютере и никуда не отправляются.")


class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self._queued = []

    def event(self, e):
        if e.type() == QEvent.FileOpen:  # macOS: «Открыть с помощью», перетаскивание на иконку
            path = e.file()
            if self.window:
                self.window.add_paths([path])
            else:
                self._queued.append(path)
            return True
        return super().event(e)


def run(args=None) -> int:
    app = App(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    icon = resource_path("assets/icon.png")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    w = MainWindow()
    app.window = w
    w.show()
    paths = [a for a in (args or []) if os.path.exists(a)] + app._queued
    if paths:
        w.add_paths(paths)
    return app.exec()
