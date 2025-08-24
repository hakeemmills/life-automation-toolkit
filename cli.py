#!/usr/bin/env python3
import argparse, subprocess, sys

def run_sub(cmd):
    return subprocess.call([sys.executable] + cmd)

def main():
    parser = argparse.ArgumentParser(description="Life Automation Toolkit CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    w = sub.add_parser("weather", help="Send rain alert if threshold exceeded")
    w.add_argument("--city", required=True)
    w.add_argument("--country", required=True)
    w.add_argument("--units", choices=["metric","imperial","standard"], default="metric")
    w.add_argument("--threshold", type=float, default=0.2)

    s = sub.add_parser("sort", help="Sort files by extension")
    s.add_argument("--path", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-empty-dirs", action="store_true")

    r = sub.add_parser("rename", help="Normalize filenames")
    r.add_argument("--path", required=True)
    r.add_argument("--date-prefix", action="store_true")
    r.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if args.command == "weather":
        cmd = ["weather_alert.py", "--city", args.city, "--country", args.country, "--units", args.units, "--threshold", str(args.threshold)]
        sys.exit(run_sub(cmd))
    elif args.command == "sort":
        cmd = ["file_sorter.py", "--path", args.path] + (["--dry-run"] if args.dry_run else []) + (["--no-empty-dirs"] if args.no_empty_dirs else [])
        sys.exit(run_sub(cmd))
    elif args.command == "rename":
        cmd = ["media_renamer.py", "--path", args.path] + (["--date-prefix"] if args.date_prefix else []) + (["--dry-run"] if args.dry_run else [])
        sys.exit(run_sub(cmd))

if __name__ == "__main__":
    main()
