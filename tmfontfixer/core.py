"""Добавление недостающих туркменских латинских букв в шрифты.

Буквы собираются из глифов самого шрифта: базовая буква + диакритика
(Ň = N + гачек, Ş = S + седиль и т.д.). Положение диакритики берётся из
уже существующих в шрифте букв (Š Ž Č, Ä Ö Ü, Ç ...), поэтому новые буквы
повторяют решения дизайнера. Если образцов нет — по метрикам шрифта
(такие буквы помечаются «проверьте»).

Поддерживается: TrueType (.ttf), OpenType CFF (.otf), вариативные TrueType,
WOFF / WOFF2. Не поддерживается: CFF2 (вариативные .otf), CID-шрифты, .ttc.
"""
from __future__ import annotations

import copy
import io
import math
import statistics
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from fontTools.misc.vector import Vector
from fontTools.pens.basePen import BasePen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.models import VariationModel

# (символ, имя глифа по AGL, базовая буква, диакритика)
LETTERS = [
    ("Ä", "Adieresis", "A", "dieresis"), ("ä", "adieresis", "a", "dieresis"),
    ("Ç", "Ccedilla", "C", "cedilla"), ("ç", "ccedilla", "c", "cedilla"),
    ("Ň", "Ncaron", "N", "caron"), ("ň", "ncaron", "n", "caron"),
    ("Ö", "Odieresis", "O", "dieresis"), ("ö", "odieresis", "o", "dieresis"),
    ("Ş", "Scedilla", "S", "cedilla"), ("ş", "scedilla", "s", "cedilla"),
    ("Ü", "Udieresis", "U", "dieresis"), ("ü", "udieresis", "u", "dieresis"),
    ("Ý", "Yacute", "Y", "acute"), ("ý", "yacute", "y", "acute"),
    ("Ž", "Zcaron", "Z", "caron"), ("ž", "zcaron", "z", "caron"),
]
TURKMEN_CHARS = "".join(l[0] for l in LETTERS)

ACCENTS = {
    "dieresis": dict(top=True, spacing=0x00A8, combining=0x0308, sign="¨",
                     names=("dieresis", "dieresiscomb", "dieresiscmb", "uni0308", "uni00A8"),
                     refs_upper="ÄËÖÜŸ", refs_lower="äëöüÿ"),
    "caron": dict(top=True, spacing=0x02C7, combining=0x030C, sign="ˇ",
                  names=("caron", "caroncomb", "caroncmb", "uni030C", "uni02C7"),
                  refs_upper="ŠŽČŘĚŇĎŤ", refs_lower="šžčřěň"),
    "acute": dict(top=True, spacing=0x00B4, combining=0x0301, sign="´",
                  names=("acute", "acutecomb", "acutecmb", "uni0301", "uni00B4"),
                  refs_upper="ÁÉÓÚÝŃŚŹĆŔ", refs_lower="áéóúýńśźćŕ"),
    "cedilla": dict(top=False, spacing=0x00B8, combining=0x0327, sign="¸",
                    names=("cedilla", "cedillacomb", "cedillacmb", "uni0327", "uni00B8"),
                    refs_upper="ÇŞ", refs_lower="çş"),
}
# Из чего можно синтезировать знак, если его нет: (источник, преобразование)
SYNTH = {
    "caron": dict(cps=(0x02C6, 0x0302), names=("circumflex", "circumflexcomb", "uni0302", "uni02C6"),
                  flip="vertical", what="перевёрнутого циркумфлекса ˆ"),
    "acute": dict(cps=(0x0060, 0x0300), names=("grave", "gravecomb", "uni0300"),
                  flip="horizontal", what="отражённого грависа `"),
}
CASE_SUFFIXES = (".case", ".cap", ".uc")


# Привязка по горизонтали: (ширина полосы у края буквы, режим смещения).
# Для каждого шрифта выбирается вариант, лучше всего предсказывающий его же
# готовые буквы (Š Ž Č ř ě… / Ç Ţ Ņ…). DEFAULT — если проверять не на чем.
STRATEGIES = [(b, m) for b in (0.05, 0.1, 0.25, 0.5, 1.0) for m in ("abs", "rel")]
CONFIG = dict(default=(0.25, "abs"), adaptive=True, ced_anchor="top", inconsistent=0.02)
# Дополнительные образцы седили (если знак в них совпадает по размеру с седилью)
CEDILLA_EXTRA = "ŢţŅņĶķĻļŖŗȨȩ"


class FontFixError(Exception):
    """Шрифт нельзя обработать (формат не поддерживается и т.п.)."""


@dataclass
class LetterResult:
    char: str
    status: str            # present | added | mapped | skipped
    note: str = ""         # пояснение для пользователя
    confidence: str = ""   # high | medium | low (для added)
    glyph: str = ""


@dataclass
class Report:
    path: str = ""
    family: str = ""
    style: str = ""
    kind: str = ""
    flavor: Optional[str] = None
    letters: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    copyright: str = ""
    license: str = ""
    license_url: str = ""
    consistency: dict = field(default_factory=dict)  # ошибка перекрёстной проверки, % кегля

    @property
    def missing_before(self):
        return [l.char for l in self.letters if l.status != "present"]

    @property
    def added(self):
        return [l for l in self.letters if l.status in ("added", "mapped")]

    @property
    def skipped(self):
        return [l for l in self.letters if l.status == "skipped"]

    @property
    def needs_check(self):
        return [l for l in self.added if l.confidence in ("medium", "low")]


# ----------------------------------------------------------------- геометрия

def _contours(gs, name):
    """Контуры глифа (с раскрытыми компонентами) как списки точек."""
    pen = DecomposingRecordingPen(gs)
    gs[name].draw(pen)
    out, cur = [], None
    for op, args in pen.value:
        if op == "moveTo":
            cur = [args[0]]
            out.append(cur)
        elif op in ("lineTo", "curveTo", "qCurveTo"):
            if cur is None:
                cur = []
                out.append(cur)
            cur.extend(p for p in args if p is not None)
        elif op in ("closePath", "endPath"):
            cur = None
    return [c for c in out if c]


def _cbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _glyph_box(gs, name):
    cs = _contours(gs, name)
    return _union([_cbox(c) for c in cs]) if cs else None


class _SamplePen(BasePen):
    """Точки, лежащие на самом контуре (кривые разбиты на отрезки)."""

    def __init__(self, gs):
        super().__init__(gs)
        self.pts = []

    def _moveTo(self, p):
        self.pts.append(p)

    def _lineTo(self, p):
        a = self._getCurrentPoint()
        for i in range(1, 5):
            t = i / 4
            self.pts.append((a[0] + (p[0] - a[0]) * t, a[1] + (p[1] - a[1]) * t))

    def _curveToOne(self, b, c, d):
        a = self._getCurrentPoint()
        for i in range(1, 11):
            t = i / 10
            m = 1 - t
            self.pts.append((m ** 3 * a[0] + 3 * m * m * t * b[0] + 3 * m * t * t * c[0] + t ** 3 * d[0],
                             m ** 3 * a[1] + 3 * m * m * t * b[1] + 3 * m * t * t * c[1] + t ** 3 * d[1]))

    def _qCurveToOne(self, b, c):
        a = self._getCurrentPoint()
        for i in range(1, 11):
            t = i / 10
            m = 1 - t
            self.pts.append((m * m * a[0] + 2 * m * t * b[0] + t * t * c[0],
                             m * m * a[1] + 2 * m * t * b[1] + t * t * c[1]))


def _outline_points(gs, name):
    pen = _SamplePen(gs)
    gs[name].draw(pen)
    return pen.pts


def _band_center(pts, top, band):
    """Центр контура в полосе у верхнего (top) или нижнего края."""
    if not pts:
        return 0.0
    ys = [p[1] for p in pts]
    lo, hi = min(ys), max(ys)
    h = hi - max(lo, 0) if top else (hi - lo)
    h = max(h, 1)
    if top:
        sel = [x for x, y in pts if y >= hi - band * h]
    else:
        sel = [x for x, y in pts if y <= lo + band * h]
    return (min(sel) + max(sel)) / 2


def _anchor(pts, top, strat):
    """Якорь буквы по горизонтали и масштаб смещения для стратегии."""
    if not pts:
        return 0.0, 1.0
    band, mode = strat
    c = _band_center(pts, top, band)
    if mode == "abs":
        return c, 1.0
    xs = [p[0] for p in pts]
    return c, max(max(xs) - min(xs), 1)


def _accent_x(points, top):
    """Якорь знака: центр рамки; у седили — её «ножка» (верхняя часть)."""
    if not top and CONFIG["ced_anchor"] == "top":
        return _band_center(points, True, 0.3)
    b = _cbox(points)
    return (b[0] + b[2]) / 2


def _accent_part(gs, ref, base, top, tol):
    """Рамка диакритики внутри готовой буквы (ref) относительно её базы."""
    rc, bc = _contours(gs, ref), _contours(gs, base)
    if not rc or not bc:
        return None
    key = lambda pts: tuple(round(v) for v in _cbox(pts))
    pool = Counter(key(c) for c in bc)
    rest = []
    for c in rc:
        k = key(c)
        if pool[k] > 0:
            pool[k] -= 1
        else:
            rest.append(c)
    if rest and len(rest) < len(rc) and sum(pool.values()) == 0:
        return _union([_cbox(c) for c in rest]), [p for c in rest for p in c]
    if top:  # контуры базы изменены (слиты) — берём всё, что выше буквы
        bb = _union([_cbox(c) for c in bc])
        rest = [c for c in rc if _cbox(c)[1] >= bb[3] - tol]
        if rest and len(rest) < len(rc):
            return _union([_cbox(c) for c in rest]), [p for c in rest for p in c]
    return None


@dataclass
class _Sample:
    accent: str
    upper: bool
    part: tuple
    acc_x: float
    base_pts: list
    ref: str
    hint: Optional[str]


# ------------------------------------------------------------------- работа

class _Fixer:
    def __init__(self, font: TTFont, report: Report, adjust=None):
        self.adjust = adjust or {}
        self.f = font
        self.r = report
        self.is_cff = "CFF " in font
        self.is_tt = "glyf" in font
        self.is_var = "fvar" in font and "gvar" in font
        self.upm = font["head"].unitsPerEm
        self.cmap = font.getBestCmap() or {}
        self.order = list(font.getGlyphOrder())
        self.gs = font.getGlyphSet()
        os2 = font["OS/2"] if "OS/2" in font else None
        self.xh = getattr(os2, "sxHeight", 0) or self._height("x", 0.5)
        self.cap = getattr(os2, "sCapHeight", 0) or self._height("H", 0.7)
        self.italic = font["post"].italicAngle if "post" in font else 0
        self.synth_notes = {}
        self.strat = {}
        self.cv_err = {}

    # -- вспомогательное
    def _height(self, ch, frac):
        g = self.cmap.get(ord(ch))
        b = _glyph_box(self.gs, g) if g else None
        return b[3] if b else self.upm * frac

    def gname(self, ch):
        return self.cmap.get(ord(ch))

    def exists(self, name):
        return name in self.gs and _glyph_box(self.gs, name) is not None

    # -- образцы
    def _cedilla_dims(self, gs):
        spec = ACCENTS["cedilla"]
        names = [self.cmap.get(spec["combining"]), self.cmap.get(spec["spacing"])] + list(spec["names"])
        dims = []
        for n in dict.fromkeys(x for x in names if x):
            if n in gs:
                b = _glyph_box(gs, n)
                if b:
                    dims.append((b[2] - b[0], b[3] - b[1]))
        return dims

    def samples(self, gs):
        out = []
        tol = self.upm * 0.01
        ced_dims = self._cedilla_dims(gs)
        for acc, spec in ACCENTS.items():
            extra_u = extra_l = ""
            if acc == "cedilla":
                extra_u = "".join(c for c in CEDILLA_EXTRA if c.isupper())
                extra_l = "".join(c for c in CEDILLA_EXTRA if c.islower())
            for upper, refs in ((True, spec["refs_upper"] + extra_u), (False, spec["refs_lower"] + extra_l)):
                for ch in refs:
                    ref = self.gname(ch)
                    base = self.gname(unicodedata.normalize("NFD", ch)[0])
                    if not ref or not base or ref == base:
                        continue
                    found = _accent_part(gs, ref, base, spec["top"], tol)
                    if not found:
                        continue
                    part, ppts = found
                    if ch in CEDILLA_EXTRA:
                        w, h = part[2] - part[0], part[3] - part[1]
                        if not any(abs(w - dw) <= tol * 3 and abs(h - dh) <= tol * 3 for dw, dh in ced_dims):
                            continue  # там запятая, а не седиль
                    acc_x = _accent_x(ppts, spec["top"])
                    hint = None
                    if self.is_tt:
                        g = self.f["glyf"][ref]
                        if g.isComposite():
                            others = [c.glyphName for c in g.components if c.glyphName != base]
                            if len(others) == 1:
                                hint = others[0]
                    out.append(_Sample(acc, upper, part, acc_x, _outline_points(gs, base), ref, hint))
        return out

    # -- выбор привязки
    def choose_strategy(self, samples, top):
        default = CONFIG["default"]
        if not CONFIG["adaptive"]:
            return default, None
        groups = {}
        for smp in samples:
            if ACCENTS[smp.accent]["top"] == top:
                groups.setdefault((smp.accent, smp.upper), []).append(smp)
        groups = [g for g in groups.values() if len(g) >= 2]
        if not groups:
            return default, None
        best, best_err = default, None
        for strat in [default] + [x for x in STRATEGIES if x != default]:
            errs = []
            for g in groups:
                anc = [_anchor(smp.base_pts, top, strat) for smp in g]
                offs = [(smp.acc_x - c) / sc for smp, (c, sc) in zip(g, anc)]
                for i in range(len(g)):
                    pred = statistics.median(offs[:i] + offs[i + 1:])
                    errs.append(abs(offs[i] - pred) * anc[i][1])
            err = sum(errs) / len(errs)
            if best_err is None or err < best_err - self.upm * 0.002:
                best, best_err = strat, err
        return best, best_err

    # -- выбор знака диакритики
    def accent_glyph(self, acc, upper, samples):
        spec = ACCENTS[acc]
        same = [s for s in samples if s.accent == acc and s.upper == upper]
        hints = Counter(s.hint for s in same if s.hint and self.exists(s.hint))
        if hints:
            return hints.most_common(1)[0][0], False
        base_names = [self.cmap.get(spec["combining"]), self.cmap.get(spec["spacing"])] + list(spec["names"])
        base_names = [n for n in dict.fromkeys(base_names) if n]
        cands = []
        if upper:
            cands += [n + suf for n in base_names for suf in CASE_SUFFIXES]
        cands += base_names
        cands = [n for n in dict.fromkeys(cands) if self.exists(n)]
        if cands and same:
            # образцы без компонентов (CFF): выбираем знак, совпадающий по размеру
            w = statistics.median(s.part[2] - s.part[0] for s in same)
            h = statistics.median(s.part[3] - s.part[1] for s in same)

            def diff(n):
                b = _glyph_box(self.gs, n)
                return abs((b[2] - b[0]) - w) + abs((b[3] - b[1]) - h)
            cands.sort(key=diff)
        if cands:
            return cands[0], False
        name = self.synthesize(acc, upper)
        return (name, True) if name else (None, False)

    def synthesize(self, acc, upper):
        spec = SYNTH.get(acc)
        if not spec:
            return None
        src = [self.cmap.get(cp) for cp in spec["cps"]] + list(spec["names"])
        src = [n for n in dict.fromkeys(src) if n]
        if upper:
            src = [n + s for n in src for s in CASE_SUFFIXES] + src
        src = [n for n in src if self.exists(n)]
        if not src:
            return None
        s = src[0]
        name = self.free_name(f"{acc}.tm{'.case' if upper else ''}")
        b = _glyph_box(self.gs, s)
        if spec["flip"] == "vertical":
            t = (1, 0, 0, -1, 0, b[1] + b[3])
        else:
            t = (-1, 0, 0, 1, b[0] + b[2], 0)
        adv = self.f["hmtx"][s][0]
        if self.is_tt:
            pen = TTGlyphPen(self.gs)
            pen.addComponent(s, t)
            g = pen.glyph()
            self._add_tt_glyph(name, g)
        else:
            self._add_cff_glyph(name, adv, [(s, t)], reverse=True)
        self.f["hmtx"].metrics[name] = (adv, round(min(b[0], b[2]) if spec["flip"] == "vertical" else b[0]))
        self.gs = self.f.getGlyphSet()
        self.synth_notes[name] = spec["what"]
        return name

    # -- размещение
    def place(self, gs, samples, ch, base, acc_name, acc):
        spec = ACCENTS[acc]
        upper = ch.isupper()
        ab = _glyph_box(gs, acc_name)
        same = [s for s in samples if s.accent == acc and s.upper == upper]
        other_case = [s for s in samples if s.accent == acc and s.upper != upper]
        if spec["top"]:
            if same:
                bottom, src = statistics.median(s.part[1] for s in same), "same"
            else:
                any_top = [s for s in samples if ACCENTS[s.accent]["top"] and s.upper == upper]
                if any_top:
                    bottom, src = statistics.median(s.part[1] for s in any_top), "other"
                else:
                    src = "metrics"
                    low = ab[1] if ab[1] >= self.xh * 0.95 else self.xh + 0.06 * self.upm
                    if upper:
                        bottom = ab[1] if ab[1] >= self.cap * 0.95 else low + (self.cap - self.xh)
                    else:
                        bottom = low
            dy = bottom - ab[1]
        else:
            pool = same or other_case
            if pool:
                dy = statistics.median(s.part[3] for s in pool) - ab[3]
                src = "same" if same else "other"
            else:
                dy, src = 0, "metrics"
        relpool = same or other_case
        strat = self.strat.get(spec["top"], CONFIG["default"])
        top = spec["top"]
        zc, zw = _anchor(_outline_points(gs, base), top, strat)
        if relpool:
            offs = []
            for smp in relpool:
                c, sc = _anchor(smp.base_pts, top, strat)
                offs.append((smp.acc_x - c) / sc)
            cx = zc + statistics.median(offs) * zw
        else:
            cx = zc
        if not relpool and self.italic:
            bb = _glyph_box(gs, base)
            acc_mid = (ab[1] + ab[3]) / 2 + dy
            cx += -math.tan(math.radians(self.italic)) * (acc_mid - (bb[1] + bb[3]) / 2)
        apts = [p for c in _contours(gs, acc_name) for p in c]
        dx = cx - _accent_x(apts, spec["top"])
        return round(dx), round(dy), src

    # -- добавление глифов
    def free_name(self, preferred, cp=None):
        names = set(self.order) | set(self.f.getGlyphOrder())
        if preferred not in names:
            return preferred
        if cp is not None and f"uni{cp:04X}" not in names:
            return f"uni{cp:04X}"
        i = 1
        while f"{preferred}.{i}" in names:
            i += 1
        return f"{preferred}.{i}"

    def _add_tt_glyph(self, name, g):
        glyf = self.f["glyf"]
        glyf.glyphs[name] = g
        self.order.append(name)
        self.f.setGlyphOrder(list(self.order))
        glyf.glyphOrder = self.f.getGlyphOrder()
        g.recalcBounds(glyf)

    def _add_cff_glyph(self, name, adv, parts, reverse=False):
        cff = self.f["CFF "].cff
        top = cff.topDictIndex[0]
        priv = top.Private
        nominal = getattr(priv, "nominalWidthX", 0)
        default = getattr(priv, "defaultWidthX", 0)
        pen = T2CharStringPen(None if adv == default else adv - nominal, self.gs)
        target = ReverseContourPen(pen) if reverse else pen
        for src, t in parts:
            self.gs[src].draw(TransformPen(target, t))
        cs = pen.getCharString(private=priv, globalSubrs=cff.GlobalSubrs)
        chs = top.CharStrings
        if chs.charStringsAreIndexed:
            chs.charStringsIndex.append(cs)
            chs.charStrings[name] = len(chs.charStringsIndex) - 1
        else:
            chs.charStrings[name] = cs
        self.order.append(name)
        top.charset = list(self.order)
        self.f.setGlyphOrder(list(self.order))

    # -- главный проход
    def run(self):
        f, r = self.f, self.r
        todo = []
        for ch, agl, base_ch, acc in LETTERS:
            if ord(ch) in self.cmap:
                r.letters.append(LetterResult(ch, "present", glyph=self.cmap[ord(ch)]))
                continue
            # глиф уже нарисован, но не подключён к Unicode
            mapped = set(self.cmap.values())
            for cand in (agl, f"uni{ord(ch):04X}"):
                if cand in self.gs and cand not in mapped and self.exists(cand):
                    self.add_cmap(ord(ch), cand)
                    r.letters.append(LetterResult(ch, "mapped", "глиф уже был в шрифте, но не был привязан к символу",
                                                  "high", cand))
                    break
            else:
                todo.append((ch, agl, base_ch, acc))
        if not todo:
            self._finish()
            return

        samples = self.samples(self.gs)
        self.strat, self.cv_err = {}, {}
        for top in (True, False):
            self.strat[top], err = self.choose_strategy(samples, top)
            if err is not None:
                self.cv_err[top] = err
                r.consistency["top" if top else "cedilla"] = round(err / self.upm * 100, 2)
        plans = []
        for ch, agl, base_ch, acc in todo:
            base = self.gname(base_ch)
            if not base or not self.exists(base):
                r.letters.append(LetterResult(ch, "skipped", f"в шрифте нет буквы «{base_ch}»"))
                continue
            acc_name, synth = self.accent_glyph(acc, ch.isupper(), samples)
            if not acc_name:
                sign = ACCENTS[acc]["sign"]
                r.letters.append(LetterResult(ch, "skipped", f"в шрифте нет знака «{sign}» ({acc}) — не из чего собрать"))
                continue
            if synth:
                samples = self.samples(self.gs)
            plans.append((ch, agl, base, acc, acc_name, synth))

        # позиции (для вариативных — во всех мастерах)
        static_offsets = self._static_composites()
        locs = self._var_locations([p[2] for p in plans] + [p[4] for p in plans] + [s.ref for s in samples])
        placements = {}
        for ch, agl, base, acc, acc_name, synth in plans:
            placements[ch] = [self.place(self.gs, samples, ch, base, acc_name, acc)]
        loc_data = []
        for loc in locs:
            gs = f.getGlyphSet(location=loc, normalized=True)
            smp = self.samples(gs)
            loc_data.append((loc, gs, smp))
            for ch, agl, base, acc, acc_name, synth in plans:
                if acc in static_offsets:
                    dx, dy, _ = placements[ch][0]
                else:
                    dx, dy, _ = self.place(gs, smp, ch, base, acc_name, acc)
                placements[ch].append((dx, dy, gs[base].width))

        for ch, (adx, ady) in self.adjust.items():  # ручная подстройка
            if ch in placements:
                placements[ch] = [(v[0] + adx, v[1] + ady) + tuple(v[2:]) for v in placements[ch]]
        for ch, agl, base, acc, acc_name, synth in plans:
            dx, dy, src = placements[ch][0]
            name = self.free_name(agl, ord(ch))
            adv = f["hmtx"][base][0]
            if self.is_tt:
                pen = TTGlyphPen(self.gs)
                pen.addComponent(base, (1, 0, 0, 1, 0, 0))
                pen.addComponent(acc_name, (1, 0, 0, 1, dx, dy))
                g = pen.glyph()
                g.components[0].flags |= 0x0200  # USE_MY_METRICS
                self._add_tt_glyph(name, g)
                lsb = g.xMin
            else:
                self._add_cff_glyph(name, adv, [(base, (1, 0, 0, 1, 0, 0)), (acc_name, (1, 0, 0, 1, dx, dy))])
                bp_b, bp_a = BoundsPen(self.gs), BoundsPen(self.gs)
                self.gs[base].draw(bp_b)
                self.gs[acc_name].draw(bp_a)
                lsb = round(min(bp_b.bounds[0], bp_a.bounds[0] + dx))
            f["hmtx"].metrics[name] = (adv, lsb)
            self.add_cmap(ord(ch), name)
            self.copy_glyph_data(base, name)
            if self.is_var and locs:
                self._add_gvar(name, adv, placements[ch], locs)
            conf = {"same": "high", "other": "medium", "metrics": "low"}[src]
            note = {"same": "по образцу похожих букв шрифта",
                    "other": "высота знака взята с других акцентов — проверьте",
                    "metrics": "по метрикам шрифта (образцов нет) — проверьте"}[src]
            if conf == "high" and self.cv_err.get(ACCENTS[acc]["top"], 0) > self.upm * CONFIG["inconsistent"]:
                conf = "medium"
                note = "в этом шрифте знаки расставлены неравномерно — проверьте положение"
            if ch in self.adjust and self.adjust[ch] != (0, 0):
                conf, note = "high", f"положение знака подправлено вручную ({self.adjust[ch][0]:+d}, {self.adjust[ch][1]:+d})"
            if synth:
                conf = "low"
                note = f"знак сделан из {self.synth_notes.get(acc_name, 'другого знака')} — проверьте"
            r.letters.append(LetterResult(ch, "added", note, conf, name))
            self.gs = f.getGlyphSet()
        self._finish()

    def _static_composites(self):
        """Акценты, чьи смещения в готовых составных буквах не меняются по осям."""
        if not (self.is_var and self.is_tt):
            return set()
        gvar = self.f["gvar"].variations
        glyf = self.f["glyf"]
        out = set()
        for acc, spec in ACCENTS.items():
            found, moving = False, False
            for ch in spec["refs_upper"] + spec["refs_lower"]:
                ref = self.gname(ch)
                if not ref or not glyf[ref].isComposite():
                    continue
                comps = glyf[ref].components
                found = True
                for tv in gvar.get(ref, []):
                    for i in range(len(comps)):
                        d = tv.coordinates[i] if i < len(tv.coordinates) else None
                        if d and (d[0] or d[1]):
                            moving = True
            if found and not moving:
                out.add(acc)
        return out

    def _var_locations(self, names):
        if not self.is_var:
            return []
        gvar = self.f["gvar"].variations
        locs = []
        for n in dict.fromkeys(names):
            for tv in gvar.get(n, []):
                peak = {ax: v[1] for ax, v in tv.axes.items() if v[1] != 0}
                if peak and peak not in locs:
                    locs.append(peak)
        return locs[:64]

    def _add_gvar(self, name, adv, values, locs):
        axes = [a.axisTag for a in self.f["fvar"].axes]
        model = VariationModel([{}] + locs, axisOrder=axes)
        dx0, dy0, _ = values[0]
        master = [Vector([dx0, dy0, adv])] + [Vector([dx, dy, w]) for dx, dy, w in values[1:]]
        deltas = model.getDeltas(master, round=round)
        tvs = []
        for support, d in zip(model.supports[1:], deltas[1:]):
            ddx, ddy, dadv = (int(v) for v in d)
            if not (ddx or ddy or dadv):
                continue
            tvs.append(TupleVariation(dict(support), [(0, 0), (ddx, ddy), (0, 0), (dadv, 0), (0, 0), (0, 0)]))
        self.f["gvar"].variations[name] = tvs

    # -- таблицы
    def add_cmap(self, cp, name):
        for t in self.f["cmap"].tables:
            if t.isUnicode() and t.format in (4, 12) and (t.format == 12 or cp <= 0xFFFF):
                t.cmap[cp] = name
        self.cmap[cp] = name

    def copy_glyph_data(self, base, new):
        f = self.f
        if "GDEF" in f and f["GDEF"].table.GlyphClassDef:
            cd = f["GDEF"].table.GlyphClassDef.classDefs
            cd[new] = cd.get(base, 1)
        if "GPOS" in f:
            self._clone_kerning(base, new)
        if "kern" in f:
            for st in f["kern"].kernTables:
                kt = getattr(st, "kernTable", None)
                if kt is None:
                    continue
                add = {}
                for (a, b), v in kt.items():
                    if a == base:
                        add[(new, b)] = v
                    if b == base:
                        add[(a, new)] = v
                    if a == base and b == base:
                        add[(new, new)] = v
                if len(kt) + len(add) <= 10900:
                    kt.update(add)
                elif not getattr(self, "_kern_warned", False):
                    self._kern_warned = True
                    self.r.warnings.append("Старая таблица kern переполнена — кернинг новых букв добавлен только в GPOS.")
        if "vmtx" in f and base in f["vmtx"].metrics:
            f["vmtx"].metrics[new] = f["vmtx"].metrics[base]
        if "hdmx" in f:
            for ppem, widths in f["hdmx"].hdmx.items():
                if base in widths:
                    widths[new] = widths[base]
        if "LTSH" in f:
            y = f["LTSH"].yPels
            y[new] = y.get(base, 1)
        for tag, mapattr in (("HVAR", "AdvWidthMap"), ("VVAR", "AdvHeightMap")):
            if tag not in f:
                continue
            t = f[tag].table
            maps = [getattr(t, mapattr, None)] + [getattr(t, a, None) for a in
                                                  ("LsbMap", "RsbMap", "TsbMap", "BsbMap", "VOrgMap")]
            maps = [m for m in maps if m is not None]
            if getattr(t, mapattr, None) is not None:
                for m in maps:
                    if base in m.mapping:
                        m.mapping[new] = m.mapping[base]
            else:  # прямая адресация по номеру глифа
                vd = t.VarStore.VarData[0]
                gid_new = f.getGlyphID(new)
                gid_base = f.getGlyphID(base)
                while len(vd.Item) < gid_new:
                    vd.Item.append([0] * vd.VarRegionCount)
                row = list(vd.Item[gid_base]) if gid_base < len(vd.Item) else [0] * vd.VarRegionCount
                if len(vd.Item) == gid_new:
                    vd.Item.append(row)
                else:
                    vd.Item[gid_new] = row
                vd.ItemCount = len(vd.Item)
                if hasattr(vd, "calculateNumShorts"):
                    vd.calculateNumShorts()

    def _clone_kerning(self, base, new):
        for lookup in self.f["GPOS"].table.LookupList.Lookup:
            for st in lookup.SubTable:
                if lookup.LookupType == 9:
                    if st.ExtensionLookupType != 2:
                        continue
                    st = st.ExtSubTable
                elif lookup.LookupType != 2:
                    continue
                if st.Format == 2:
                    if base in st.Coverage.glyphs:
                        st.Coverage.glyphs.append(new)
                    c1 = st.ClassDef1.classDefs.get(base) if st.ClassDef1 else None
                    if c1:
                        st.ClassDef1.classDefs[new] = c1
                    c2 = st.ClassDef2.classDefs.get(base) if st.ClassDef2 else None
                    if c2:
                        st.ClassDef2.classDefs[new] = c2
                elif st.Format == 1:
                    for ps in st.PairSet:
                        extra = [copy.deepcopy(p) for p in ps.PairValueRecord if p.SecondGlyph == base]
                        for p in extra:
                            p.SecondGlyph = new
                        ps.PairValueRecord.extend(extra)
                        ps.PairValueCount = len(ps.PairValueRecord)
                    if base in st.Coverage.glyphs:
                        i = st.Coverage.glyphs.index(base)
                        st.Coverage.glyphs.append(new)
                        st.PairSet.append(copy.deepcopy(st.PairSet[i]))
                        st.PairSetCount = len(st.PairSet)

    def _finish(self):
        f = self.f
        if "OS/2" in f and self.r.added:
            os2 = f["OS/2"]
            bits = set(os2.getUnicodeRanges())
            cps = [ord(l.char) for l in self.r.added]
            if any(0x80 <= c <= 0xFF for c in cps):
                bits.add(1)
            if any(0x100 <= c <= 0x17F for c in cps):
                bits.add(2)
            os2.setUnicodeRanges(bits)
            bmp = [c for c in self.cmap if c <= 0xFFFF]
            if bmp:
                os2.usFirstCharIndex = min(bmp)
                os2.usLastCharIndex = max(bmp)


# ----------------------------------------------------------------- снаружи

def _name(font, *ids):
    if "name" not in font:
        return ""
    for i in ids:
        v = font["name"].getDebugName(i)
        if v:
            return v
    return ""


def load_font(src) -> TTFont:
    try:
        if isinstance(src, (bytes, bytearray)):
            return TTFont(io.BytesIO(src))
        return TTFont(src)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "fontNumber" in msg or "ttcf" in msg.lower():
            raise FontFixError("Коллекции шрифтов (.ttc/.otc) пока не поддерживаются — разделите на отдельные файлы.")
        raise FontFixError(f"Не удалось открыть шрифт: {msg}")


def describe(font: TTFont, path="") -> Report:
    r = Report(path=path)
    r.family = _name(font, 16, 1)
    r.style = _name(font, 17, 2)
    r.copyright = _name(font, 0)
    r.license = _name(font, 13)
    r.license_url = _name(font, 14)
    r.flavor = font.flavor
    if "CFF2" in font:
        r.kind = "OpenType CFF2 (вариативный)"
    elif "CFF " in font:
        r.kind = "OpenType CFF"
    elif "fvar" in font:
        r.kind = "TrueType, вариативный"
    else:
        r.kind = "TrueType"
    if r.flavor:
        r.kind += f" · {r.flavor.upper()}"
    return r


def process(src, *, rename_suffix: str | None = None, path: str = "",
            adjust: dict | None = None) -> tuple[TTFont, Report]:
    """Добавляет недостающие туркменские буквы. Возвращает (шрифт, отчёт).

    adjust: {символ: (dx, dy)} — ручной сдвиг знака в единицах шрифта.
    """
    font = src if isinstance(src, TTFont) else load_font(src)
    if not path and isinstance(src, str):
        path = src
    r = describe(font, path)
    if "CFF2" in font:
        raise FontFixError("Вариативные OpenType-шрифты (CFF2) пока не поддерживаются. Используйте TTF-версию шрифта.")
    if "CFF " in font and hasattr(font["CFF "].cff.topDictIndex[0], "ROS"):
        raise FontFixError("CID-шрифты (обычно азиатские) не поддерживаются.")
    if "glyf" not in font and "CFF " not in font:
        raise FontFixError("В файле нет контуров (bitmap/цветной шрифт) — не поддерживается.")
    if not font.getBestCmap():
        raise FontFixError("В шрифте нет Unicode-таблицы символов (возможно, это символьный шрифт).")
    if "OS/2" in font and font["OS/2"].fsType & 0x0002:
        r.warnings.append("Флаг лицензии fsType = «Restricted»: автор ограничил использование. "
                          "Убедитесь, что лицензия разрешает изменение шрифта.")
    if any(t in font for t in ("COLR", "SVG ", "sbix", "CBDT")):
        r.warnings.append("Цветной шрифт: новые буквы будут только в обычной (одноцветной) версии.")
    _Fixer(font, r, adjust).run()
    if rename_suffix and r.added:
        rename_family(font, rename_suffix)
        r.family = _name(font, 16, 1)
    return font, r


def rename_family(font: TTFont, suffix: str):
    """Добавляет суффикс к названию семейства (чтобы ставить рядом с оригиналом)."""
    fam = _name(font, 16, 1)
    if not fam or not suffix.strip():
        return
    new = fam + suffix
    fam_ps, new_ps = fam.replace(" ", ""), new.replace(" ", "")
    for rec in font["name"].names:
        if rec.nameID in (1, 3, 4, 16, 18, 21):
            s = rec.toUnicode()
            rec.string = s.replace(fam, new, 1) if fam in s else (s + suffix if rec.nameID in (1, 16) else s)
        elif rec.nameID in (6, 20):
            rec.string = rec.toUnicode().replace(fam_ps, new_ps, 1)
        if rec.nameID == 3 and fam_ps in rec.toUnicode():
            rec.string = rec.toUnicode().replace(fam_ps, new_ps, 1)
    if "CFF " in font:
        cff = font["CFF "].cff
        top = cff.topDictIndex[0]
        cff.fontNames[0] = cff.fontNames[0].replace(fam_ps, new_ps, 1)
        for attr in ("FamilyName", "FullName"):
            if hasattr(top, attr):
                setattr(top, attr, getattr(top, attr).replace(fam, new, 1))


def output_path(src_path: str, out_dir: str | None = None, suffix: str = "-TM") -> str:
    import os
    d, fn = os.path.split(src_path)
    stem, ext = os.path.splitext(fn)
    return os.path.join(out_dir or d, f"{stem}{suffix}{ext}")


def save(font: TTFont, path: str):
    font.save(path)


def to_bytes(font: TTFont) -> bytes:
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()
