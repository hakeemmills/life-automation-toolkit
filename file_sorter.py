#!/usr/bin/env python3
import argparse, os, shutil
from pathlib import Path
from collections import defaultdict

COMMON_MAP = {
    ".pdf": "Documents/pdf",
    ".doc": "Documents/word",
    ".docx": "Documents/word",
    ".xls": "Documents/excel",
    ".xlsx": "Documents/excel",
    ".csv": "Documents/csv",
    ".jpg": "Pictures/jpg",
    ".jpeg": "Pictures/jpg",
    ".png": "Pictures/png",
    ".gif": "Pictures/gif",
    ".mp4": "Videos/mp4",
    ".mov": "Videos/mov",
    ".mp3": "Audio/mp3",
    ".wav": "Audio/wav",
    ".zip": "Archives/zip",
    ".7z": "Archives/7z",
    ".rar": "Archives/rar",
    ".txt": "Documents/txt",
    ".md": "Documents/md",
}

def plan_moves(root: Path, custom_map=None):
    mapping = {**COMMON_MAP, **(custom_map or {})}
    moves = []
    for p in root.iterdir():
        if p.is_file():
            ext = p.suffix.lower()
            target_folder = mapping.get(ext, f"Other{ext or '_noext'}")
            moves.append((p, root / target_folder / p.name))
    return moves

def main():
    parser = argparse.ArgumentParser(description="Sort files in a folder by extension into categorized subfolders.")
    parser.add_argument("--path", required=True, help="Folder to organize")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be moved")
    parser.add_argument("--no-empty-dirs", action="store_true", help="Leave empty dirs (default removes them)")
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"{root} is not a valid directory")

    moves = plan_moves(root)
    if args.dry_run:
        for src, dst in moves:
            print(f"[DRY] {src.name} -> {dst}")
        return

    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
            print(f"Moved: {src.name} -> {dst}")
        except Exception as e:
            print(f"Failed to move {src} -> {dst}: {e}")

    if not args.no_empty_dirs:
        # remove empty subdirectories in root (one level deep)
        for p in root.iterdir():
            if p.is_dir():
                try:
                    next(p.iterdir())
                except StopIteration:
                    try:
                        p.rmdir()
                        print(f"Removed empty dir: {p}")
                    except Exception:
                        pass

if __name__ == "__main__":
    main()
