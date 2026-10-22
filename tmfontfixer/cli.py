"""Командная строка: пакетная обработка и самотест (без Qt)."""
from __future__ import annotations

import argparse
import os
import sys

from . import APP_NAME, __version__, core

FONT_EXT = (".ttf", ".otf", ".woff", ".woff2")


def _print(*a):
    if sys.stdout is not None:  # у оконной сборки на Windows stdout нет
        try:
            print(*a)
        except Exception:  # noqa: BLE001
            pass


def collect(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(FONT_EXT)]
        else:
            out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tmfontfixer", description=f"{APP_NAME}: добавляет туркменские буквы в шрифты")
    ap.add_argument("--cli", action="store_true", help="режим командной строки")
    ap.add_argument("--selftest", action="store_true", help="проверка работоспособности")
    ap.add_argument("--version", action="store_true")
    ap.add_argument("-o", "--out-dir", help="папка для результата (по умолчанию рядом с оригиналом)")
    ap.add_argument("--rename", metavar="SUFFIX", help="добавить суффикс к имени семейства, напр. ' TM'")
    ap.add_argument("--suffix", default="-TM", help="суффикс имени файла (по умолчанию -TM)")
    ap.add_argument("fonts", nargs="*")
    a = ap.parse_args(argv)
    if a.version:
        _print(f"{APP_NAME} {__version__}")
        return 0
    if a.selftest:
        from . import selftest
        ok = selftest.run(_print)
        _print("SELFTEST", "OK" if ok else "FAILED")
        return 0 if ok else 1
    files = collect(a.fonts)
    if not files:
        ap.print_help()
        return 2
    rc = 0
    for p in files:
        try:
            font, rep = core.process(p, rename_suffix=a.rename)
        except core.FontFixError as e:
            _print(f"✕ {p}: {e}")
            rc = 1
            continue
        if not rep.added:
            _print(f"✓ {p}: все туркменские буквы уже есть")
            continue
        if a.out_dir:
            os.makedirs(a.out_dir, exist_ok=True)
        out = core.output_path(p, a.out_dir, a.suffix)
        core.save(font, out)
        added = "".join(l.char for l in rep.added)
        _print(f"+ {p} → {out}: добавлено {added}")
        for l in rep.needs_check:
            _print(f"    ! {l.char}: {l.note}")
        for l in rep.skipped:
            _print(f"    – {l.char}: {l.note}")
        for w in rep.warnings:
            _print(f"    ⚠ {w}")
    return rc
