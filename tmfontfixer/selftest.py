"""Самотест: синтетические шрифты (TrueType и CFF) с известной геометрией.

Используется в CI и командой `--selftest` у собранного приложения.
"""
from __future__ import annotations

import io

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

from . import core

CAP, XH = 700, 500
CAP_SHIFT = CAP - XH  # акценты нарисованы на высоте строчных


def _rect(pen, x0, y0, x1, y1):
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()


def _shapes():
    """name -> (функция рисования, ширина)"""
    s = {}
    for ch in "ACNOSUYZ":
        s[ch] = (lambda p: (_rect(p, 50, 0, 150, CAP), _rect(p, 450, 0, 550, CAP), _rect(p, 150, 300, 450, 400)), 600)
    for ch in "acnosuyz":
        s[ch] = (lambda p: (_rect(p, 50, 0, 140, XH), _rect(p, 360, 0, 450, XH), _rect(p, 140, 200, 360, 290)), 500)
    s["space"] = (lambda p: None, 250)
    s[".notdef"] = (lambda p: _rect(p, 50, 0, 450, CAP), 500)
    s["dieresis"] = (lambda p: (_rect(p, 0, 560, 60, 620), _rect(p, 120, 560, 180, 620)), 180)
    s["caron"] = (lambda p: (p.moveTo((0, 640)), p.lineTo((90, 560)), p.lineTo((180, 640)),
                             p.lineTo((150, 640)), p.lineTo((90, 590)), p.lineTo((30, 640)), p.closePath()), 180)
    s["acute"] = (lambda p: (p.moveTo((40, 560)), p.lineTo((140, 650)), p.lineTo((180, 630)),
                             p.lineTo((70, 550)), p.closePath()), 180)
    s["cedilla"] = (lambda p: (_rect(p, 0, -60, 40, 0), _rect(p, 0, -200, 140, -140), _rect(p, 100, -140, 140, -60)), 140)
    return s


CMAP = {**{ord(c): c for c in "ACNOSUYZacnosuyz"}, 0x20: "space", 0xA8: "dieresis", 0x2C7: "caron",
        0xB4: "acute", 0xB8: "cedilla"}
# готовые буквы-образцы: (символ, база, знак, dx, dy)
REFS = [("Ä", "A", "dieresis", 210, CAP_SHIFT), ("ä", "a", "dieresis", 160, 0),
        ("Š", "S", "caron", 210, CAP_SHIFT), ("š", "s", "caron", 160, 0),
        ("Ç", "C", "cedilla", 280, 0), ("ç", "c", "cedilla", 230, 0),
        ("Á", "A", "acute", 210, CAP_SHIFT), ("á", "a", "acute", 160, 0)]


def build(kind="tt") -> bytes:
    shapes = _shapes()
    order = [".notdef", "space"] + list("ACNOSUYZacnosuyz") + ["dieresis", "caron", "acute", "cedilla"]
    names = {ch: f"ref_{ord(ch):04X}" for ch, *_ in REFS}
    order += list(names.values())
    fb = FontBuilder(1000, isTTF=(kind == "tt"))
    fb.setupGlyphOrder(order)
    cmap = dict(CMAP)
    cmap.update({ord(ch): names[ch] for ch, *_ in REFS})
    fb.setupCharacterMap(cmap)
    metrics = {}
    if kind == "tt":
        glyphs = {}
        for n, (fn, w) in shapes.items():
            pen = TTGlyphPen(None)
            fn(pen)
            glyphs[n] = pen.glyph()
            metrics[n] = w
        for ch, base, acc, dx, dy in REFS:
            pen = TTGlyphPen(glyphs)
            pen.addComponent(base, (1, 0, 0, 1, 0, 0))
            pen.addComponent(acc, (1, 0, 0, 1, dx, dy))
            glyphs[names[ch]] = pen.glyph()
            metrics[names[ch]] = shapes[base][1]
        fb.setupGlyf(glyphs)
    else:
        charstrings = {}
        for n, (fn, w) in shapes.items():
            pen = T2CharStringPen(w, None)
            fn(pen)
            charstrings[n] = pen.getCharString()
            metrics[n] = w
        for ch, base, acc, dx, dy in REFS:
            pen = T2CharStringPen(shapes[base][1], None)
            shapes[base][0](pen)
            shapes[acc][0](TransformPen(pen, (1, 0, 0, 1, dx, dy)))
            charstrings[names[ch]] = pen.getCharString()
            metrics[names[ch]] = shapes[base][1]
        fb.setupCFF("TMSelfTest-Regular", {"FullName": "TM SelfTest"}, charstrings, {})
    fb.setupHorizontalMetrics({n: (w, 0) for n, w in metrics.items()})
    fb.setupHorizontalHeader(ascent=900, descent=-250)
    fb.setupNameTable({"familyName": "TM SelfTest", "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=900, sTypoDescender=-250, usWinAscent=900, usWinDescent=250,
                sxHeight=XH, sCapHeight=CAP)
    fb.setupPost()
    if kind == "tt":
        f = fb.font
        for g in f["glyf"].glyphs.values():
            g.recalcBounds(f["glyf"])
        f["hmtx"].metrics = {n: (w, f["glyf"][n].xMin if hasattr(f["glyf"][n], "xMin") else 0)
                             for n, (w, _) in f["hmtx"].metrics.items()}
    buf = io.BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _accent_box(font, ch, acc):
    cm = font.getBestCmap()
    base = cm[ord(core.unicodedata.normalize("NFD", ch)[0])]
    found = core._accent_part(font.getGlyphSet(), cm[ord(ch)], base, core.ACCENTS[acc]["top"], 10)
    return found[0] if found else None


def run(verbose=print) -> bool:
    ok = True
    for kind in ("tt", "cff"):
        data = build(kind)
        font, rep = core.process(data)
        font = TTFont(io.BytesIO(core.to_bytes(font)))
        cm = font.getBestCmap()
        missing = [c for c in core.TURKMEN_CHARS if ord(c) not in cm]
        checks = []
        # гачек у Ň/ň на той же высоте, что у Š/š; седиль у Ş на той же высоте, что у Ç
        for new, ref in (("Ň", "Š"), ("ň", "š"), ("Ş", "Ç"), ("ş", "ç"), ("Ö", "Ä"), ("ý", "á")):
            acc = [l for l in core.LETTERS if l[0] == new][0][3]
            a, b = _accent_box(font, new, acc), _accent_box(font, ref, acc)
            top = core.ACCENTS[acc]["top"]
            good = a is not None and b is not None and (abs(a[1] - b[1]) <= 1 if top else abs(a[3] - b[3]) <= 1)
            checks.append((new, ref, good))
        # горизонталь: на симметричной базе знак по центру
        n_box = _accent_box(font, "Ň", "caron")
        centered = n_box is not None and abs((n_box[0] + n_box[2]) / 2 - 300) <= 2
        k_ok = not missing and all(g for *_, g in checks) and centered and not rep.skipped
        ok &= k_ok
        if verbose:
            verbose(f"[{kind}] added={len(rep.added)} missing={''.join(missing) or '-'} "
                    f"heights={'OK' if all(g for *_, g in checks) else checks} centered={centered} -> "
                    f"{'OK' if k_ok else 'FAIL'}")
    return ok
