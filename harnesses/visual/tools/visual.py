#!/usr/bin/env python3
"""Visual harness helper (standard library only): desktop captures and the evidence folder.

  visual.py tools                          which capture tools are installed
  visual.py capture --out FILE [--display :0] [--window NAME | --id WID] [--strip X,Y,W,H] [--dry-run]
  visual.py evidence add FILE [FILE...]    copy screenshots into .linko-work/evidence/ (git-ignored; max 12)
  visual.py evidence list

capture crops to ONE window (found with xdotool or wmctrl); with no window it takes a fixed strip (default: the
bottom 64 px of the screen). The application attaches the files of .linko-work/evidence/ to the task result in chat.
Lines that start with `STOP:` are errors: report them, do not work around them.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MAX_EVIDENCE = 12
EVIDENCE_TYPES = (".png", ".jpg", ".jpeg", ".webp")
GRABBERS = ("import", "maim", "scrot")
DEFAULT_STRIP = 64


class Stop(Exception):
    pass


def available(which=shutil.which):
    return {"finders": [t for t in ("xdotool", "wmctrl") if which(t)],
            "grabbers": [t for t in GRABBERS if which(t)]}


def parse_xdotool(text):
    """`xdotool getwindowgeometry --shell` output -> (x, y, w, h)."""
    values = dict(re.findall(r"^(\w+)=(-?\d+)$", text, re.M))
    try:
        return tuple(int(values[k]) for k in ("X", "Y", "WIDTH", "HEIGHT"))
    except KeyError as error:
        raise Stop("STOP: cannot read the window geometry from xdotool output") from error


def parse_wmctrl(text, wid=None, name=None):
    """`wmctrl -lG` output -> (x, y, w, h) of the window with that id (0x...) or whose title contains name."""
    for line in text.splitlines():
        parts = line.split(None, 7)
        if len(parts) < 7:
            continue
        title = parts[7] if len(parts) > 7 else ""
        if (wid is not None and int(parts[0], 16) == int(str(wid), 0)) or (name and name.lower() in title.lower()):
            return tuple(int(parts[i]) for i in (2, 3, 4, 5))
    raise Stop("STOP: wmctrl lists no matching window")


def clamp(geometry, screen):
    """Keep the rectangle inside the screen (a window may hang over the edge) and make sure it is not empty."""
    x, y, w, h = geometry
    sw, sh = screen
    x2, y2 = min(x + w, sw), min(y + h, sh)
    x, y = max(x, 0), max(y, 0)
    if x2 - x <= 0 or y2 - y <= 0:
        raise Stop("STOP: the capture area is outside the screen")
    return x, y, x2 - x, y2 - y


def strip(screen, height=DEFAULT_STRIP):
    """The fixed fallback: a full-width strip at the bottom of the screen."""
    sw, sh = screen
    height = min(height, sh)
    return 0, sh - height, sw, height


def parse_rect(text):
    try:
        x, y, w, h = (int(v) for v in text.split(","))
    except ValueError as error:
        raise Stop("STOP: --strip must be X,Y,W,H in pixels") from error
    return x, y, w, h


def grab_command(tool, geometry, out):
    """The argument list that captures exactly this rectangle with the given tool."""
    x, y, w, h = geometry
    if tool == "import":
        return ["import", "-window", "root", "-crop", f"{w}x{h}+{x}+{y}", "+repage", str(out)]
    if tool == "maim":
        return ["maim", "-g", f"{w}x{h}+{x}+{y}", str(out)]
    if tool == "scrot":
        return ["scrot", "-o", "-a", f"{x},{y},{w},{h}", str(out)]
    raise Stop(f"STOP: unknown capture tool {tool}")


def run(cmd, env):
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    if done.returncode:
        raise Stop(f"STOP: {cmd[0]} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout


def screen_size(env, which=shutil.which):
    if which("xdotool"):
        out = run(["xdotool", "getdisplaygeometry"], env).split()
        return int(out[0]), int(out[1])
    if which("xdpyinfo"):
        match = re.search(r"dimensions:\s+(\d+)x(\d+)", run(["xdpyinfo"], env))
        if match:
            return int(match.group(1)), int(match.group(2))
    raise Stop("STOP: cannot read the screen size (install xdotool or x11-utils)")


def find_window(args, env, which=shutil.which):
    if which("xdotool"):
        wid = args.id
        if wid is None:
            found = run(["xdotool", "search", "--onlyvisible", "--name", args.window], env).split()
            if not found:
                raise Stop(f"STOP: no visible window named {args.window!r}")
            wid = found[0]
        return parse_xdotool(run(["xdotool", "getwindowgeometry", "--shell", str(wid)], env))
    if which("wmctrl"):
        return parse_wmctrl(run(["wmctrl", "-lG"], env), wid=args.id, name=args.window)
    raise Stop("STOP: no window finder installed (xdotool or wmctrl); use --strip X,Y,W,H")


def capture(args, which=shutil.which):
    env = {**os.environ, **({"DISPLAY": args.display} if args.display else {})}
    tools = available(which)
    if not tools["grabbers"]:
        raise Stop("STOP: no screenshot tool installed (need one of import, maim, scrot)")
    if args.strip and not (args.window or args.id):
        geometry = parse_rect(args.strip)
        if not any(geometry[2:]) or geometry[2] <= 0 or geometry[3] <= 0:
            raise Stop("STOP: --strip needs a positive width and height")
        screen = None
    elif args.window or args.id:
        geometry = find_window(args, env, which)
        screen = screen_size(env, which) if which("xdotool") or which("xdpyinfo") else None
    else:
        screen = screen_size(env, which)
        geometry = strip(screen)
    if screen:
        geometry = clamp(geometry, screen)
    cmd = grab_command(tools["grabbers"][0], geometry, args.out)
    if args.dry_run:
        print(" ".join(cmd))
        return
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    run(cmd, env)
    print(f"captured {geometry[2]}x{geometry[3]} at {geometry[0]},{geometry[1]} -> {args.out}")


def evidence_dir():
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        root = Path(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else Path.cwd()
    except (OSError, subprocess.SubprocessError):
        root = Path.cwd()
    work = root / ".linko-work"
    work.mkdir(parents=True, exist_ok=True)
    if not (work / ".gitignore").exists():
        (work / ".gitignore").write_text("# Working files: never committed or synced.\n*\n")
    folder = work / "evidence"
    folder.mkdir(exist_ok=True)
    return folder


def evidence_files(folder):
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EVIDENCE_TYPES)


def evidence_add(files, folder=None):
    folder = folder or evidence_dir()
    for name in files:
        source = Path(name)
        if source.suffix.lower() not in EVIDENCE_TYPES or not source.is_file():
            raise Stop(f"STOP: {name} is not an image file ({', '.join(EVIDENCE_TYPES)})")
        if source.resolve().parent != folder.resolve():
            if len(evidence_files(folder)) >= MAX_EVIDENCE and not (folder / source.name).exists():
                raise Stop(f"STOP: already {MAX_EVIDENCE} screenshots as evidence; that is the cap")
            shutil.copyfile(source, folder / source.name)
    return evidence_files(folder)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tools")
    cap = sub.add_parser("capture")
    cap.add_argument("--out", required=True)
    cap.add_argument("--display")
    cap.add_argument("--window", help="part of the window title")
    cap.add_argument("--id", help="window id (xdotool decimal or wmctrl 0x...)")
    cap.add_argument("--strip", help="X,Y,W,H fixed rectangle (used when no window is given)")
    cap.add_argument("--dry-run", action="store_true", help="print the capture command instead of running it")
    ev = sub.add_parser("evidence")
    ev.add_argument("action", choices=("add", "list"))
    ev.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    try:
        if args.command == "tools":
            found = available()
            print(f"finders: {', '.join(found['finders']) or 'none'}; grabbers: {', '.join(found['grabbers']) or 'none'}")
        elif args.command == "capture":
            capture(args)
        else:
            files = evidence_add(args.files) if args.action == "add" else evidence_files(evidence_dir())
            print("\n".join(f"{p.name} ({p.stat().st_size} bytes)" for p in files) or "no evidence yet")
    except Stop as stop:
        print(stop, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
