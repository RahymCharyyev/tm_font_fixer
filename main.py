"""Точка входа: без аргументов — окно, с --cli/--selftest/--version — консоль."""
import sys

CLI_FLAGS = ("--cli", "--selftest", "--version", "-h", "--help")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-psn_")]  # macOS Finder
    if any(a in CLI_FLAGS for a in args):
        from tmfontfixer.cli import main as cli_main
        return cli_main(args)
    from tmfontfixer.gui import run
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
