from __future__ import annotations

import argparse
from pathlib import Path

from dos_text_shell import DosTextShell


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _PROJECT_ROOT / "run" / "gwbasic.sh"


class GwBasicTextShell(DosTextShell):
    def __init__(self, *, archive_path: str, runtime_dir: str, home_dir: str, file_path: str | None) -> None:
        super().__init__(
            shell_name="GW-BASIC",
            runner_path=_RUNNER,
            archive_path=archive_path,
            runtime_dir=runtime_dir,
            home_dir=home_dir,
            file_path=file_path,
            prompt="",
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GW-BASIC text-only interactive shell")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--file")
    args = parser.parse_args(argv)

    shell = GwBasicTextShell(
        archive_path=args.archive,
        runtime_dir=args.runtime,
        home_dir=args.home,
        file_path=args.file,
    )
    return shell.run()


if __name__ == "__main__":
    raise SystemExit(main())
