#!/usr/bin/env python3
import argparse
import re
from datetime import datetime
from pathlib import Path

SAFE = re.compile(r"[^a-z0-9\-_.]+")


def slugify(name: str) -> str:
    name = name.lower().strip()
    name = name.replace(" ", "-")
    name = re.sub(r"_+", "-", name)
    name = SAFE.sub("-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 1
    while True:
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def main():
    parser = argparse.ArgumentParser(description="Normalize and clean filenames in a folder.")
    parser.add_argument("--path", required=True, help="Folder to process")
    parser.add_argument(
        "--date-prefix", action="store_true", help="Prefix filenames with today's date YYYYMMDD-"
    )
    parser.add_argument("--dry-run", action="store_true", help="Only display planned changes")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"{root} is not a valid directory")

    prefix = datetime.today().strftime("%Y%m%d-") if args.date_prefix else ""
    changes = []
    for p in root.iterdir():
        if p.is_file():
            new_name = slugify(p.stem) + p.suffix.lower()
            new_path = p.with_name(prefix + new_name)
            if new_path.name != p.name:
                new_path = unique_path(new_path)
                changes.append((p, new_path))

    for src, dst in changes:
        if args.dry_run:
            print(f"[DRY] {src.name} -> {dst.name}")
        else:
            src.rename(dst)
            print(f"Renamed: {src.name} -> {dst.name}")


if __name__ == "__main__":
    main()
