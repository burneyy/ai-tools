#!/usr/bin/env python3
"""Apply SEARCH/REPLACE edit blocks from a chatbot reply to files on disk.

Expected format (any number of blocks, several per file is fine):

    ### FILE: src/foo.py
    <<<<<<< SEARCH
    exact existing lines
    =======
    replacement lines
    >>>>>>> REPLACE

An empty SEARCH section creates the file (or appends to it if it exists).
All edits are checked first; if any block fails, nothing is written.

Usage (run from the project root):
    python apply_edits.py --clipboard          # read the reply from the clipboard
    python apply_edits.py reply.md
    python apply_edits.py < reply.md
    python apply_edits.py --dry-run -c         # check only
"""
import argparse
import re
import sys
from pathlib import Path

FILE_RE = re.compile(r"^\s*#{1,6}\s*FILE:\s*(.+?)\s*$")
SEARCH_RE = re.compile(r"^([ \t]*)<{5,9} ?SEARCH\s*$")
DIVIDER_RE = re.compile(r"^\s*={5,9}\s*$")
REPLACE_RE = re.compile(r"^\s*>{5,9} ?REPLACE\s*$")


def parse(text):
    """Return a list of (path, search, replace, line_no) tuples."""
    edits, current_file, state = [], None, None
    search, replace, start, indent = [], [], 0, ""
    for i, line in enumerate(text.splitlines(keepends=True), 1):
        bare = line.rstrip("\n")
        if state is None:
            m = FILE_RE.match(bare)
            s = SEARCH_RE.match(bare)
            if m:
                current_file = m.group(1).strip("`'\" ")
            elif s:
                if current_file is None:
                    sys.exit(f"line {i}: SEARCH block before any FILE header")
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
                edits.append((current_file, "".join(search), "".join(replace), start))
                state = None
            else:
                replace.append(line)
    if state is not None:
        sys.exit(f"line {start}: block never closed (reply truncated?)")
    return edits


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
    text = text.removeprefix("\ufeff")  # files saved by Windows tools may start with a BOM
    edits = parse(text.replace("\r\n", "\n"))
    if not edits:
        sys.exit("No SEARCH/REPLACE blocks found.")

    root = Path.cwd().resolve()
    files, errors, notes = {}, [], []  # files: path -> [content, uses_crlf, is_new]

    for path, search, replace, line_no in edits:
        p = (root / path).resolve()
        if root not in p.parents:
            errors.append(f"{path}: outside the project folder")
            continue
        if p not in files:
            if p.exists():
                with open(p, encoding="utf-8", newline="") as f:
                    raw = f.read()
                files[p] = [raw.replace("\r\n", "\n"), "\r\n" in raw, False]
            else:
                files[p] = [None, False, True]

        content = files[p][0]
        try:
            if not search.strip():  # create or append
                if content and not content.endswith("\n"):
                    content += "\n"
                files[p][0] = (content or "") + replace
            elif content is None:
                raise ValueError("file does not exist")
            else:
                s, e, how = find(content, search)
                if how == "blank-lines":
                    # the match starts and ends on non-blank lines, so the file's own
                    # surrounding blank lines stay; don't add the model's on top
                    replace = trim_blank_edges(replace)
                    notes.append(f"{path} (block at line {line_no}): matched ignoring blank lines")
                files[p][0] = content[:s] + replace + content[e:]
        except ValueError as err:
            errors.append(f"{path} (block at line {line_no}): {err}")

    if errors:
        print("Nothing written. Problems:", file=sys.stderr)
        for e in errors:
            print("  " + e, file=sys.stderr)
        sys.exit(1)

    for p, (content, crlf, is_new) in files.items():
        print(f"{'create' if is_new else 'edit  '}  {p.relative_to(root)}")
        if not args.dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(content.replace("\n", "\r\n") if crlf else content)
    if notes:
        print("Check these in the diff:")
        for n in notes:
            print("  " + n)
    if args.dry_run:
        print("(dry run, nothing written)")


if __name__ == "__main__":
    main()
