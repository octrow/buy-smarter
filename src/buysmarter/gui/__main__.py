"""``python -m buysmarter.gui`` and ``buysmarter-gui``; the frozen desktop app starts here too."""

import sys


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        # Imported first and alone: it points the store at a temp dir before Settings is read.
        from buysmarter.gui.selftest import self_test

        return self_test()
    from buysmarter.gui.app import main as run_app

    return run_app()


if __name__ == "__main__":
    sys.exit(main())
