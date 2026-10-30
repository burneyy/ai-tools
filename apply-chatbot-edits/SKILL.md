---
name: search-replace-edits
description: Output code changes as SEARCH/REPLACE blocks that a script applies automatically to the user's files. Use this skill whenever the user pastes a codebase, a codebase dump or source files and asks to implement a feature, fix a bug, refactor or otherwise change code, even if they don't mention the format, and whenever they mention SEARCH/REPLACE blocks, apply_edits or applying edits with a script. Don't answer such requests with full files or diffs.
---

# SEARCH/REPLACE edits

The user doesn't edit their files by hand. They copy your reply and run a script (`apply_edits.py`) that finds each SEARCH text in the named file and swaps in the REPLACE text. Because a program applies the edits, small deviations matter: if any block can't be found, or matches more than one place, the script writes nothing at all and the user has to come back to you. Getting the SEARCH text exactly right is the core of the job.

## Workflow

1. Read the task and the relevant code. If the task is ambiguous, or a file you need isn't in what you were given, ask instead of guessing. Never write SEARCH text for code you haven't seen.
2. Give a short plan: which files you'll change or create, and why.
3. Output all changes in one code block, in the format below.
4. After the code block, list anything the user must do by hand: new dependencies, config changes, migrations.

## Output format

Put ALL changes, for all files, in ONE code block, so the user can copy everything with a single click. Inside it, start each change with a FILE line giving the path relative to the project root, followed by a SEARCH/REPLACE block:

````
```
### FILE: src/utils/math.py
<<<<<<< SEARCH
def add(a, b):
    return a + b
=======
def add(a, b):
    """Return the sum of a and b."""
    return a + b
>>>>>>> REPLACE

### FILE: src/utils/strings.py
<<<<<<< SEARCH
=======
def shout(s):
    return s.upper()
>>>>>>> REPLACE
```
````

Put nothing else inside that code block: the plan goes before it and notes go after it. Write a FILE line before every block, even when several blocks in a row change the same file. A block without its own FILE line goes to the last file named, so a forgotten header can send an edit to the wrong file.

If any of the changed code itself contains ``` lines (Markdown files, docstrings with code samples), open and close the code block with ```` (four backticks) instead. Otherwise the chat interface ends the block early and splits the reply.

## Writing the SEARCH text

- **Copy character for character** from the current file, including indentation, comments, quotes and trailing commas. Never paraphrase, shorten with `...`, or fix typos inside SEARCH; fixes belong in REPLACE.
- **Copy blank lines too.** SEARCH must contain every blank line between its first and last line in the file. Blank lines are easy to drop when reproducing code. The script tolerates missing ones as a last resort, but it flags the edit and may remove blank lines from the user's file.
- **Start and end each SEARCH on a non-blank line.**
- **Make it unique.** Include just enough surrounding lines, usually two or three, for the text to match exactly one place. Watch out for lines that repeat in a file, such as `return None`, closing braces, decorators or common imports.
- **Keep blocks small.** Several small blocks per file are better than one large block: every extra line is another chance for a copying mistake. Don't include unchanged lines beyond the context needed for uniqueness.

## Several blocks for the same file

Blocks, DELETE and RENAME lines are applied in order, each on the result of the previous ones. A later SEARCH must therefore match the text as it looks after the earlier edits. The simplest way to get this right is to order blocks from the top of the file to the bottom and never let two blocks touch the same lines.

## Special cases

- **New file:** leave SEARCH empty and put the full content in REPLACE. On an existing file this appends to the end instead, so use it only for files that don't exist yet.
- **Deleting code:** leave REPLACE empty.
- **Deleting a file:** put `### DELETE: path/to/file.py` on its own line inside the code block, with no SEARCH/REPLACE block after it. The file must exist.
- **Renaming or moving a file:** put `### RENAME: old/path.py -> new/path.py` on its own line. Never recreate the file under the new name with its full content. Put the RENAME before any edits to that file, and give those edits a FILE line with the new path. The new path must not exist yet. Imports and other references in other files still need ordinary blocks.
- **Paths:** relative to the project root, with forward slashes, exactly as they appear in the codebase you were given.

## Avoid

- Full file contents, unified diffs or line numbers.
- Changes unrelated to the task. Match the existing code style.
