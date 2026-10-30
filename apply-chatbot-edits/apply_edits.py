#!/usr/bin/env python3
"""Apply SEARCH/REPLACE edit blocks from a chatbot reply to files on disk.

Expected format (any number of blocks, several per file is fine):

    ### FILE: src/foo.py
    <<<<<<< SEARCH
    exact existing lines
    =======
    replacement lines
    >>>>>>> REPLACE

    ### DELETE: src/old.py
    ### RENAME: src/utils.py -> src/helpers/utils.py

An empty SEARCH section creates the file (or appends to it if it exists).
Steps apply in order, so edits after a RENAME use the new path.
All steps are checked first; if any fails, nothing is written.

Usage (run from the project root):
    python apply_edits.py --clipboard          # read the reply from the clipboard
    python apply_edits.py reply.md
    python apply_edits.py < reply.md
    python apply_edits.py --dry-run -c         # check only
"""
import argparse
import os
import re
import sys
from pathlib import Path

FILE_RE = re.compile(r"^\s*#{1,6}\s*FILE:\s*(.+?)\s*$")
DELETE_RE = re.compile(r"^\s*#{1,6}\s*DELETE:\s*(.+?)\s*$")
RENAME_RE = re.compile(r"^\s*#{1,6}\s*RENAME:\s*(.+?)\s*->\s*(.+?)\s*$")
SEARCH_RE = re.compile(r"^([ \t]*)<{5,9} ?SEARCH\s*$")
DIVIDER_RE = re.compile(r"^\s*={5,9}\s*$")
REPLACE_RE = re.compile(r"^\s*>{5,9} ?REPLACE\s*$")


def clean(path):
    return path.strip("`'\" ")


def parse(text):
    """Return a list of steps, in reply order:
    ("edit", line_no, path, search, replace), ("delete", line_no, path)
    or ("rename", line_no, old_path, new_path).
    """
    steps, current_file, state = [], None, None
    search, replace, start, indent = [], [], 0, ""
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        bare = line.rstrip("\n")
        if state is None:
            m = FILE_RE.match(bare)
            d = DELETE_RE.match(bare)
            r = RENAME_RE.match(bare)
            s = SEARCH_RE.match(bare)
            if m:
                current_file = clean(m.group(1))
            elif d:
                steps.append(("delete", i, clean(d.group(1))))
                current_file = None  # a following block needs its own FILE line
            elif r:
                steps.append(("rename", i, clean(r.group(1)), clean(r.group(2))))
                current_file = None
            elif s:
                if current_file is None:
                    sys.exit(f"line {i}: SEARCH block without a FILE header")
                state, search, replace, start = "search", [], [], i
                indent = s.group(1)  # if the block is indented, strip that from its lines
            # anything else (prose, code fences) is ignored
            continue
        if indent and line.startswith(indent):
            line = line[len(indent):]
            bare = line.rstrip("\n")
        if state == "search":
            if DIVIDER_RE.match(bare):
                state = "replace"
            else:
                search.append(line)
        else:  # state == "replace"
            if REPLACE_RE.match(bare):
                steps.append(("edit", start, current_file, "".join(search), "".join(replace)))
                state = None
            else:
                replace.append(line)
    if state is not None:
        sys.exit(f"line {start}: block never closed (reply truncated?)")
    return steps


def find(content, search):
    """Locate the unique match of `search` and return (start, end, how).

    Tries, in order: an exact match; a match ignoring trailing whitespace;
    a match ignoring blank lines (every non-blank line must still match exactly).
    """
    count = content.count(search)
    if count == 1:
        i = content.index(search)
        return i, i + len(search), "exact"
    if count > 1:
        raise ValueError(f"SEARCH text matches {count} places; needs more context lines")

    lines = content.splitlines(keepends=True)
    target = [l.rstrip() for l in search.splitlines()]
    n = len(target)
    hits = [k for k in range(len(lines) - n + 1)
            if [l.rstrip() for l in lines[k:k + n]] == target]
    if len(hits) > 1:
        raise ValueError(f"SEARCH text matches {len(hits)} places; needs more context lines")
    if hits:
        start = sum(len(l) for l in lines[:hits[0]])
        return start, start + sum(len(l) for l in lines[hits[0]:hits[0] + n]), "whitespace"

    # Last resort: ignore blank lines, which models often drop or add.
    nonblank = [(k, l.rstrip()) for k, l in enumerate(lines) if l.strip()]
    wanted = [t for t in target if t.strip()]
    m = len(wanted)
    hits = [j for j in range(len(nonblank) - m + 1)
            if [t for _, t in nonblank[j:j + m]] == wanted]
    if len(hits) > 1:
        raise ValueError(f"SEARCH text matches {len(hits)} places; needs more context lines")
    if not hits:
        raise ValueError("SEARCH text not found in file")
    first, last = nonblank[hits[0]][0], nonblank[hits[0] + m - 1][0]
    start = sum(len(l) for l in lines[:first])
    return start, start + sum(len(l) for l in lines[first:last + 1]), "blank-lines"


def trim_blank_edges(text):
    """Drop leading and trailing blank lines."""
    lines = text.splitlines(keepends=True)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "".join(lines)


def resolve(root, path):
    """Absolute path for `path`, or ValueError if it leaves the project folder."""
    p = (root / path).resolve()
    if root not in p.parents:
        raise ValueError(f"{path}: outside the project folder")
    # On Windows, resolve() returns an existing file's on-disk spelling;
    # keep the requested one so that case-only renames work.
    name = Path(path).name
    return p.with_name(name) if name.lower() == p.name.lower() else p


def write(p, content, crlf):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(content.replace("\n", "\r\n") if crlf else content)


def move(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)  # also handles case-only renames on Windows


def read_clipboard():
    """Read text from the system clipboard via tkinter (standard library)."""
    try:
        import tkinter
    except ImportError:
        sys.exit("--clipboard needs tkinter, which this Python lacks. Save the reply to a file instead.")
    root = tkinter.Tk()
    root.withdraw()
    try:
        return root.clipboard_get()
    except tkinter.TclError:
        sys.exit("The clipboard is empty or doesn't contain text.")
    finally:
        root.destroy()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reply", nargs="?", help="file with the chatbot reply (default: stdin)")
    ap.add_argument("-c", "--clipboard", action="store_true", help="read the reply from the clipboard")
    ap.add_argument("-n", "--dry-run", action="store_true", help="check edits, write nothing")
    args = ap.parse_args()

    if args.clipboard:
        text = read_clipboard()
    elif args.reply:
        text = Path(args.reply).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    text = text.removeprefix("﻿")  # files saved by Windows tools may start with a BOM
    steps = parse(text.replace("\r\n", "\n"))
    if not steps:
        sys.exit("No SEARCH/REPLACE blocks, DELETE or RENAME lines found.")

    root = Path.cwd().resolve()
    # files: path -> [content (None if absent), uses_crlf, on_disk, changed]
    files, moves, errors, notes = {}, [], [], []

    def load(p):
        if p not in files:
            if p.exists():
                with open(p, encoding="utf-8", newline="") as f:
                    raw = f.read()
                files[p] = [raw.replace("\r\n", "\n"), "\r\n" in raw, True, False]
            else:
                files[p] = [None, False, False, False]
        return files[p]

    for kind, line_no, path, *rest in steps:
        try:
            p = resolve(root, path)
            entry = load(p)
            if entry[0] is None and (kind != "edit" or rest[0].strip()):
                raise ValueError("file does not exist")
            if kind == "delete":
                entry[0] = None
            elif kind == "rename":
                new = resolve(root, rest[0])
                # A case-only rename (Foo.py -> foo.py) names the same file. Windows paths
                # compare equal; on macOS ask the disk, if this reply hasn't touched `new`.
                same = new == p or (new not in files and p.exists() and new.exists()
                                    and os.path.samefile(p, new))
                if not same and load(new)[0] is not None:
                    raise ValueError(f"{rest[0]} already exists")
                if entry[2]:
                    moves.append((p, new))
                del files[p]
                files[new] = entry
                if not same:
                    files[p] = [None, False, False, False]
            else:
                search, replace = rest
                content = entry[0]
                if not search.strip():  # create or append
                    if content and not content.endswith("\n"):
                        content += "\n"
                    entry[0] = (content or "") + replace
                else:
                    s, e, how = find(content, search)
                    if how == "blank-lines":
                        # the match starts and ends on non-blank lines, so the file's own
                        # surrounding blank lines stay; don't add the model's on top
                        replace = trim_blank_edges(replace)
                        notes.append(f"{path} (block at line {line_no}): matched ignoring blank lines")
                    entry[0] = content[:s] + replace + content[e:]
                entry[3] = True
        except ValueError as err:
            errors.append(f"{path} (line {line_no}): {err}")

    if errors:
        print("Nothing written. Problems:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        sys.exit(1)

    # Renames first (later writes go to the new paths), deletes last.
    rel = lambda p: p.relative_to(root)
    actions = [(f"rename  {rel(a)} -> {rel(b)}", move, a, b) for a, b in moves]
    deletes = []
    for p, (content, crlf, on_disk, changed) in files.items():
        if content is not None and changed:
            actions.append((f"{'edit  ' if on_disk else 'create'}  {rel(p)}", write, p, content, crlf))
        elif content is None and on_disk:
            deletes.append((f"delete  {rel(p)}", os.remove, p))

    for label, act, *act_args in actions + deletes:
        print(label)
        if not args.dry_run:
            try:
                act(*act_args)
            except OSError as err:
                hint = " Is the file open in another program, or read-only?" if isinstance(err, PermissionError) else ""
                sys.exit(f"FAILED: {err}.{hint}\n"
                         "The steps listed before this one were done; this one and the rest were not.")
    if notes:
        print("Check these in the diff:")
        for n in notes:
            print("  " + n)
    if args.dry_run:
        print("(dry run, nothing written)")


if __name__ == "__main__":
    main()
