"""Регрессионный тест на реальных шрифтах.

Берём шрифты, где туркменские буквы нарисованы дизайнером, удаляем их
(subset), восстанавливаем программой и сравниваем положение диакритики
с оригиналом. Ошибка выражается в единицах UPM и в % от кегля.

python3 tests/compare_real_fonts.py font1.ttf font2.otf ... [--scenario minimal|all]
"""
import io
import os
import sys
import json
import logging
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fontTools import subset  # noqa: E402
from fontTools.ttLib import TTFont  # noqa: E402
from tmfontfixer import core  # noqa: E402

logging.disable(logging.WARNING)
SCENARIOS = {"minimal": "ŇňŞş", "all": core.TURKMEN_CHARS}


def strip(path, chars):
    f = TTFont(path)
    keep = [cp for cp in f.getBestCmap() if chr(cp) not in chars]
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.glyph_names = True
    opts.notdef_outline = True
    opts.hinting = True
    opts.legacy_kern = True
    opts.drop_tables = []
    s = subset.Subsetter(opts)
    s.populate(unicodes=keep)
    s.subset(f)
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


def accent_box(font, ch):
    gs = font.getGlyphSet()
    cm = font.getBestCmap()
    g, base = cm[ord(ch)], cm[ord(core.unicodedata.normalize("NFD", ch)[0])]
    top = core.ACCENTS[[l for l in core.LETTERS if l[0] == ch][0][3]]["top"]
    found = core._accent_part(gs, g, base, top, font["head"].unitsPerEm * 0.01)
    return found[0] if found else None


def run(args):
    path, scen = args
    if os.environ.get("TMFF_CONFIG"):
        cfg = json.loads(os.environ["TMFF_CONFIG"])
        if "default" in cfg:
            cfg["default"] = tuple(cfg["default"])
        core.CONFIG.update(cfg)
    chars = SCENARIOS[scen]
    try:
        orig = TTFont(path)
        stripped = strip(path, chars)
        fixed, rep = core.process(stripped)
        fixed = TTFont(io.BytesIO(core.to_bytes(fixed)))  # round-trip через бинарник
        upm = orig["head"].unitsPerEm
        res = []
        for l in rep.letters:
            if l.status == "present":
                continue
            if l.status == "skipped":
                res.append((l.char, None, None, l.note))
                continue
            a, b = accent_box(orig, l.char), accent_box(fixed, l.char)
            if not a or not b:
                res.append((l.char, None, None, "не удалось измерить"))
                continue
            dx = ((b[0] + b[2]) - (a[0] + a[2])) / 2 / upm * 100
            top = core.ACCENTS[[x for x in core.LETTERS if x[0] == l.char][0][3]]["top"]
            dy = ((b[1] - a[1]) if top else (b[3] - a[3])) / upm * 100
            res.append((l.char, round(dx, 2), round(dy, 2), l.confidence))
        return path, res, None
    except core.FontFixError as e:
        return path, [], "unsupported: " + str(e)
    except Exception as e:  # noqa: BLE001
        import traceback
        return path, [], "CRASH: " + repr(e) + traceback.format_exc()[-600:]


if __name__ == "__main__":
    scen = "minimal"
    if "--scenario" in sys.argv:
        i = sys.argv.index("--scenario")
        scen = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    paths = sys.argv[1:]
    with Pool() as pool:
        results = pool.map(run, [(p, scen) for p in paths])
    print(json.dumps(results, ensure_ascii=False))
