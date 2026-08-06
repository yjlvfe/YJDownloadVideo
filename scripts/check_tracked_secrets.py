from __future__ import annotations

import pathlib
import re
import subprocess
import sys


PATTERNS = {
    "telegram-bot-token": re.compile(rb"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "aws-access-key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}


def tracked_paths() -> list[pathlib.Path]:
    raw_paths = subprocess.check_output(["git", "ls-files", "-z"]).split(b"\0")
    return [
        pathlib.Path(raw.decode("utf-8", errors="surrogateescape"))
        for raw in raw_paths
        if raw
    ]


def main() -> int:
    ignored = {
        pathlib.Path(".github/workflows/vercel-media-flow.yml"),
        pathlib.Path("scripts/check_tracked_secrets.py"),
    }
    detected: set[tuple[str, str]] = set()

    for path in tracked_paths():
        if path in ignored:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for category, pattern in PATTERNS.items():
            if pattern.search(data):
                detected.add((category, path.as_posix()))

    if detected:
        for category, path in sorted(detected):
            print(f"Potential {category} value detected in tracked file: {path}")
        print("Values and matching lines are intentionally not displayed.")
        return 1

    print("No secret-shaped values detected in tracked text files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
