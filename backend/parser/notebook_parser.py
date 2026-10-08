import ast
import io
import nbformat
import os
import re
import sys
import tokenize

# Ensure backend directory is in sys.path for robust imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

try:
    from backend.parser.ast_parser import extract_functions_from_code
except ImportError:
    from ast_parser import extract_functions_from_code

# IPython line magics (%foo), cell magics (%%foo) and shell escapes (!foo)
# are not valid Python syntax and are not preceded by executed code, so any
# line starting with them (ignoring leading whitespace) can be safely
# commented out.
_MAGIC_LINE_RE = re.compile(r"^(\s*)(%{1,2}|!)(?!=)")

# `%time <statement>` runs the statement in the notebook's own namespace
# (only timing it), so its effect -- `%time model = train()` defining
# `model` -- must survive; it's unwrapped to the bare statement.
_TIME_MAGIC_RE = re.compile(r"^(\s*)%time[ \t]+(?!-)(\S.*)$")
# `x = %time expr` assigns expr's value; `x = !ls`, `x = %sx ls`,
# `x = %timeit -o expr` capture shell/magic output. Left as written, any of
# these is a SyntaxError that dropped the whole cell (every function in it).
_ASSIGNED_MAGIC_RE = re.compile(
    r"^(\s*)([A-Za-z_][\w.]*(?:\s*,\s*[A-Za-z_][\w.]*)*\s*=)[ \t]*(%{1,2}|!)(?!=)(.*)$"
)

# IPython's "dynamic object introspection" syntax -- ``obj?``/``obj??`` for
# an object's docstring/source, or the equivalent prefix form ``?obj``/
# ``??obj`` -- is just as common in real notebooks (typed while exploring,
# then left in a cell) and just as invalid as Python syntax, but wasn't
# covered by _MAGIC_LINE_RE above. `?` never appears in valid Python syntax
# outside of a string literal, so a line that consists of *only* an
# attribute-chain expression (optionally called) plus a leading/trailing
# "?"/"??" is unambiguously an introspection query, not code -- as opposed
# to matching any line merely containing a "?" (which would wrongly also
# match, and corrupt, plain code like `msg = "wait?"`).
_INTROSPECTION_PREFIX_RE = re.compile(
    r"^(\s*)\?{1,2}\s*[A-Za-z_][A-Za-z0-9_.]*(\(\))?\s*$"
)
_INTROSPECTION_SUFFIX_RE = re.compile(
    r"^(\s*)[A-Za-z_][A-Za-z0-9_.]*(\(\))?\s*\?{1,2}\s*$"
)

# Cell magics whose own *body* -- everything after the "%%name ..." line
# itself -- is never executed as Python in the notebook's own namespace at
# all: %%writefile writes it to a file instead of running it; %%bash/%%sh/
# %%perl/%%ruby/%%script run it as a *different* language via a subprocess;
# %%html/%%HTML/%%javascript/%%js/%%latex/%%svg/%%markdown render it as
# non-Python content. Contrast with %%time/%%timeit/%%capture/%%prun/
# %%debug, deliberately excluded here -- each of those *does* execute its
# own body as ordinary Python in the notebook's own namespace (timing it,
# capturing its output, profiling it, ...), so a function defined inside
# one of those really is callable from a later cell in the real kernel,
# unlike every magic name listed below.
NON_PYTHON_BODY_CELL_MAGICS = frozenset({
    "writefile", "bash", "sh", "perl", "ruby", "script",
    "html", "HTML", "javascript", "js", "latex", "svg", "markdown",
    # `%%file` is %%writefile's alias; %%python/%%python2/%%python3/%%pypy run
    # their body in a *separate* interpreter (its definitions never reach the
    # notebook's namespace); %%sx/%%cmd/%%powershell are shells; and
    # %%sql/%%bigquery/%%R/%%julia/%%dot/%%mermaid hold another language.
    "file", "python", "python2", "python3", "pypy", "sx", "cmd", "powershell",
    "sql", "bigquery", "R", "r", "julia", "dot", "mermaid",
})

_NON_PYTHON_BODY_CELL_MAGIC_RE = re.compile(
    r"^\s*%%(" + "|".join(re.escape(name) for name in NON_PYTHON_BODY_CELL_MAGICS)
    + r")\b"
)


def detect_non_python_body_cell_magic(source):
    """The cell magic name (e.g. "writefile") if `source`'s own first
    non-blank line invokes one of NON_PYTHON_BODY_CELL_MAGICS, else None.

    Only the first non-blank line is checked -- a real Jupyter cell magic
    must be the cell's very first statement, so a "%%writefile" appearing
    later in the cell (inside a string, a comment, or simply a syntax
    error) is not one at all.
    """
    for line in source.split("\n"):

        if not line.strip():
            continue

        match = _NON_PYTHON_BODY_CELL_MAGIC_RE.match(line)

        return match.group(1) if match else None

    return None


def _lines_unsafe_for_magic_detection(source):
    """1-indexed line numbers strip_magic_commands' own per-line loop
    below must never treat as a magic/introspection candidate, no matter
    what that line's own leading/trailing characters look like: a line
    that is really a continuation of an already-open "(", "[", or "{" --
    entirely ordinary for a long arithmetic/formatting expression split
    across lines (PEP 8's own recommended "break before binary operator"
    style), e.g. "total = (\n    a\n    % b\n)" -- a line continued via a
    trailing "\\" with no enclosing bracket at all (e.g. "total = a \\\n
    % b"), or a line inside a multi-line string/f-string literal that
    merely happens to start with "%"/"!"/"?" as plain text, not code.

    Confirmed exploitable before this: _MAGIC_LINE_RE/_INTROSPECTION_*_RE
    below were applied to every physical line independently, with no
    awareness of Python's own lexical structure at all. "% b" on its own
    continuation line inside "(...)" was indistinguishable from a real
    top-level "%foo" magic and got commented out, silently changing
    "a % b" into just "a" (the entire modulo operation discarded) with
    no error anywhere -- ast.parse still succeeds on the mutilated
    source, so nothing downstream ever notices; confirmed via a real
    compiled function: `def f(a, b): return (a\n    % b)` returned `a`
    unchanged instead of the real `a % b`. A "%"/"!"/"?"-leading line
    buried inside a triple-quoted string had the identical problem --
    its own literal text, not code, silently gained a "# " prefix,
    corrupting the string's actual value. A backslash-continued line
    with no bracket involved at all had the identical problem, missed by
    the bracket-depth tracking below (which only ever sees an *explicit*
    "([{"/")]}" token, never a bare "\\" line continuation): confirmed
    via a real compiled function, `def f(a, b): total = a \\\n    % b\n
    return total` -- a perfectly ordinary way to split a long expression
    without adding parens -- returned the unmodified `a` instead of the
    real `a % b`, the exact same silent-corruption class.

    Uses the standard library tokenizer purely as a lexical scanner --
    it tolerates a raw, magic-laden cell just fine here, since tokenize
    only requires valid *tokens*, not valid *grammar*: "%matplotlib
    inline"/"!pip install x" both tokenize without error even though
    neither parses (confirmed) -- rather than a second, hand-rolled
    bracket/string-tracking pass that could itself drift out of sync
    with Python's own real lexical rules over time. The backslash-
    continuation case above is detected the identical, tokenize-only way
    (no raw source text ever re-scanned by hand for a trailing "\\"):
    tokenize emits an explicit NEWLINE or NL token at the end of every
    *ordinary* physical line (confirmed: a NEWLINE for a normal
    top-level statement, an NL for a blank/comment-only line or a
    bracket-continuation line alike) -- but for a backslash-continued
    line, tokenize emits neither, and the very next token simply starts
    on a later line with nothing marking the break at all. Tracking
    whether the previous token was one of those two line-ending types
    catches exactly that gap, with no extra assumption about *why* the
    line broke (a bracket already open would also produce this same
    "next token, later line, no line-ending token in between" shape, but
    that case is already caught by the depth check below regardless, so
    the two checks simply overlap harmlessly there).

    Falls back to protecting nothing (preserving this function's own
    previous, unprotected-but-already-established behavior exactly) if
    tokenizing the cell fails for an unrelated reason -- a genuinely
    malformed cell (an unterminated string, an unbalanced bracket at
    EOF) that is_parseable_python (ast_parser.py) will end up rejecting
    outright once this function's own result reaches it anyway.
    """
    unsafe_lines = set()
    depth = 0
    last_line_seen = None
    last_token_end_line = None
    last_token_was_line_ender = False

    try:

        for tok_type, tok_string, (start_line, _), (end_line, _), _ in (
            tokenize.generate_tokens(io.StringIO(source).readline)
        ):

            # Only sampled once per physical line, using whatever depth
            # already stood *before* this line's own tokens run -- a
            # bracket that both opens and closes within one line (e.g.
            # the "()" in "pd.DataFrame()?") must never mark that same
            # line unsafe just because depth briefly went above zero
            # partway through it.
            if start_line != last_line_seen:

                if depth > 0:
                    unsafe_lines.add(start_line)

                last_line_seen = start_line

            if (
                tok_type in (
                    tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)
                )
                and end_line > start_line
            ):
                unsafe_lines.update(range(start_line + 1, end_line + 1))

            # A backslash line continuation: unlike every ordinary line
            # break (always followed by a NEWLINE or NL token before the
            # next token starts), this token's own start_line jumps ahead
            # of the previous token's end_line with no line-ending token
            # of either kind in between.
            if (
                not last_token_was_line_ender
                and last_token_end_line is not None
                and start_line > last_token_end_line
            ):
                unsafe_lines.update(range(last_token_end_line + 1, start_line + 1))

            if tok_type not in (tokenize.INDENT, tokenize.DEDENT):
                last_token_end_line = end_line
                last_token_was_line_ender = tok_type in (
                    tokenize.NEWLINE, tokenize.NL
                )

            if tok_type == tokenize.OP:

                if tok_string in "([{":
                    depth += 1
                elif tok_string in ")]}":
                    depth -= 1

    except (tokenize.TokenError, SyntaxError, ValueError):
        return set()

    return unsafe_lines


def strip_magic_commands(source):
    """Comment out IPython magics, shell escapes, and object-introspection
    queries in notebook source.

    Real-world notebooks routinely contain lines like ``%matplotlib inline``,
    ``%%time``, ``!pip install pandas``, or ``train_model?`` (inline help/
    source lookup, via IPython's ``?``/``??`` operator). None of these are
    valid Python, so feeding a cell's raw source straight into ``ast.parse``
    (or writing it verbatim into the generated runtime module) blows up on
    almost any notebook exported from Jupyter -- and since ``ast.parse``
    parses a cell as a single unit, *any one* such line anywhere in the cell
    fails the whole cell, silently dropping every function it defines along
    with it (see is_parseable_python in ast_parser.py). Commenting the
    offending lines out instead keeps line numbers stable and preserves the
    rest of the cell as executable Python.

    A cell opening with a NON_PYTHON_BODY_CELL_MAGICS magic (see
    detect_non_python_body_cell_magic above) is handled differently: the
    *entire* cell is commented out, not just that first line. Confirmed
    exploitable before this: a "%%writefile helper.py" cell whose body
    happened to be syntactically valid Python (the overwhelmingly common
    real-world case -- %%writefile is routinely used to scaffold a .py
    module from inside a notebook) previously had only its own
    "%%writefile helper.py" line commented out, leaving the rest of the
    cell -- e.g. a `def greet(name): ...` -- completely untouched. That
    function is never actually defined in the *notebook's own* namespace
    in a real kernel at all (it's written to helper.py, never imported or
    executed there); calling it from a later cell in the real notebook
    raises NameError. This tool instead silently compiled and exposed it
    as a real, working POST /greet endpoint -- a fidelity gap between what
    the source notebook actually does and what got served, with no
    warning anywhere.
    """
    if detect_non_python_body_cell_magic(source) is not None:
        return "\n".join(
            f"# {line}" if line.strip() else line
            for line in source.split("\n")
        )

    unsafe_lines = _lines_unsafe_for_magic_detection(source)

    cleaned_lines = []
    commented_indented = []

    for line_number, line in enumerate(source.split("\n"), start=1):

        if line_number in unsafe_lines:
            cleaned_lines.append(line)
            continue

        time_match = _TIME_MAGIC_RE.match(line)
        if time_match:
            cleaned_lines.append(f"{time_match.group(1)}{time_match.group(2)}")
            continue

        assigned = _ASSIGNED_MAGIC_RE.match(line)
        if assigned:
            indent, target, prefix, rest = assigned.groups()
            timed = re.match(r"^time[ \t]+(?!-)(\S.*)$", rest)
            if prefix == "%" and timed:
                cleaned_lines.append(f"{indent}{target} {timed.group(1)}")
            else:
                cleaned_lines.append(f"{indent}# {line.strip()}")
            continue

        match = (
            _MAGIC_LINE_RE.match(line)
            or _INTROSPECTION_PREFIX_RE.match(line)
            or _INTROSPECTION_SUFFIX_RE.match(line)
        )

        if match:
            indent = match.group(1)
            cleaned_lines.append(f"{indent}# {line.strip()}")
            if indent:
                commented_indented.append(len(cleaned_lines) - 1)
        else:
            cleaned_lines.append(line)

    return _fill_emptied_blocks(cleaned_lines, commented_indented)


def _fill_emptied_blocks(cleaned_lines, commented_indented):
    """The cleaned cell, with each indented magic line that was commented
    out turned into `pass  # <magic>` when commenting left a block with no
    statement at all -- `except ImportError:` whose only body line was
    `!pip install x`, or `if GPU:` holding just `%matplotlib inline`. An
    empty block is a SyntaxError that dropped the whole cell, every
    function in it included. Lines and line numbers stay the same, and a
    cell that doesn't parse for some other reason is left as it was."""
    cleaned = "\n".join(cleaned_lines)
    if not commented_indented:
        return cleaned
    try:
        ast.parse(cleaned)
        return cleaned
    except SyntaxError:
        pass
    filled = list(cleaned_lines)
    for index in commented_indented:
        line = filled[index]
        indent = line[: len(line) - len(line.lstrip())]
        filled[index] = f"{indent}pass  {line.lstrip()}"
    candidate = "\n".join(filled)
    try:
        ast.parse(candidate)
    except SyntaxError:
        return cleaned
    return candidate


def notebook_kernel_language(notebook):
    """The notebook's own declared kernel language (e.g. "python", "R",
    "julia"), lowercased, or None if it doesn't declare one at all.

    Checked in the same two places a real Jupyter frontend does, in the
    same order of authority: "kernelspec.language" (nbformat's own
    documented field for exactly this -- present on essentially every
    notebook a real Jupyter frontend ever writes) first, falling back to
    "language_info.name" (also standard, and sometimes present even when
    kernelspec.language is missing or a custom kernel name doesn't
    itself say much, e.g. "language": "R" under a kernelspec named
    "ir"). Both are optional per the nbformat spec, and a hand-built or
    stripped-down notebook -- most of this project's own test fixtures
    among them -- commonly omits both metadata blocks entirely; this
    returns None rather than guessing in that case, since there's no
    honest way to tell "wasn't recorded" apart from "is Python" in the
    metadata's own absence.
    """
    metadata = notebook.get("metadata") or {}

    kernelspec = metadata.get("kernelspec") or {}
    language = kernelspec.get("language")

    if not language:

        language_info = metadata.get("language_info") or {}
        language = language_info.get("name")

    return language.strip().lower() if language else None


def load_notebook(notebook_path):
    with open(notebook_path, "r", encoding="utf-8") as f:
        notebook = nbformat.read(f, as_version=4)

    # A real Jupyter notebook can carry any kernel at all -- R, Julia,
    # Scala, ... -- but this tool only ever extracts *Python* functions
    # from a cell's source (extract_functions_from_code parses it with
    # ast.parse) and only ever runs them as Python inside the generated
    # runtime module. Before this, uploading/compiling a genuinely
    # non-Python notebook wasn't rejected anywhere: every one of its code
    # cells simply failed is_parseable_python (a SyntaxError from
    # ast.parse on, say, R's own `f <- function(x) x + 1` syntax) and was
    # silently dropped -- compile_notebook_to_api "succeeded" with zero
    # extracted functions, producing a working-but-endpoint-less API with
    # nothing anywhere explaining *why* it exposed nothing. Raising here
    # instead -- a plain ValueError, already part of every call site's own
    # MALFORMED_NOTEBOOK_ERRORS/CLI_USER_FACING_ERRORS handling (see
    # routes/upload.py and cli.py), so this needs no new exception
    # handling of its own anywhere -- reports the real, specific reason up
    # front, at the exact same "validate before doing anything else" point
    # this project's own MALFORMED_NOTEBOOK_ERRORS docstring already
    # establishes for a malformed (not-even-valid-JSON) notebook.
    #
    # Silently permissive (no language declared at all) rather than
    # rejecting: a notebook's own kernelspec/language_info are both
    # optional per the nbformat spec, and assuming Python in their
    # absence preserves this function's previous behavior exactly for
    # every notebook that doesn't declare a language either way --
    # including, not incidentally, most of this project's own test
    # fixtures (nbformat.v4.new_notebook() sets neither by default).
    language = notebook_kernel_language(notebook)

    if language and language not in ("python", "python3", "python2"):

        raise ValueError(
            f"This notebook's kernel language is '{language}', not "
            "Python -- notebook-to-api only compiles Python notebooks "
            "into an API."
        )

    return notebook


# Colab and Kaggle notebooks read their uploads through the sandbox's own
# absolute folders (`pd.read_csv("/content/sales.csv")`,
# "/kaggle/input/titanic/train.csv"), which never exist in the compiled app
# -- so the read always failed and the file was never shipped. Each prefix
# is mapped to the notebook's own directory, where the file is expected to
# sit after downloading the notebook with its data.
SANDBOX_PATH_PREFIXES = (
    "/content/drive/MyDrive/",
    "/content/drive/My Drive/",
    "/content/",
    "/kaggle/input/",
    "/kaggle/working/",
)


def sandbox_relative_path(path):
    """`path` with a Colab/Kaggle sandbox prefix (SANDBOX_PATH_PREFIXES)
    replaced by a notebook-relative path, else None. The Drive mount point
    itself (`drive.mount("/content/drive")`) is left alone."""
    for prefix in SANDBOX_PATH_PREFIXES:
        if path.startswith(prefix):
            relative = path[len(prefix):].lstrip("/")
            if not relative or relative == "drive" or relative.startswith("drive/"):
                return None
            if ".." in relative.split("/"):
                return None
            return relative
    return None


def relocate_sandbox_paths(source):
    """`source` with each plain string literal naming a sandbox path (see
    sandbox_relative_path) rewritten to its notebook-relative form, keeping
    the literal's own prefix and quotes. f-strings, bytes, multi-line
    literals and untokenizable cells come back unchanged."""
    if "/content/" not in source and "/kaggle/" not in source:
        return source
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source

    edits = []
    for token in tokens:
        if token.type != tokenize.STRING or token.start[0] != token.end[0]:
            continue
        match = re.match(r"^([rRuU]?)('|\")(.*)\2$", token.string, re.DOTALL)
        if not match or match.group(3).startswith(match.group(2) * 2):
            continue
        prefix, quote, body = match.groups()
        if "\\" in body and not prefix.lower() == "r":
            continue
        relative = sandbox_relative_path(body)
        if relative is None or quote in relative:
            continue
        edits.append((token.start, token.end, f"{prefix}{quote}{relative}{quote}"))

    if not edits:
        return source
    lines = source.splitlines(keepends=True)
    for (row, start), (_, end), text in reversed(edits):
        line = lines[row - 1]
        lines[row - 1] = line[:start] + text + line[end:]
    return "".join(lines)


def extract_code_cells(notebook):
    code_cells = []

    for cell in notebook.cells:
        if cell.cell_type == "code":
            code_cells.append(relocate_sandbox_paths(strip_magic_commands(cell.source)))

    return code_cells


def extract_functions_from_notebook(notebook_path):
    notebook = load_notebook(notebook_path)
    code_cells = extract_code_cells(notebook)
    
    all_functions = []
    for code in code_cells:
        funcs = extract_functions_from_code(code)
        all_functions.extend(funcs)
    return all_functions


if __name__ == "__main__":
    # Resolve the path to sample.ipynb relative to this script to make it run from anywhere
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sample_path = os.path.join(script_dir, "../../notebooks/sample.ipynb")

    print(f"Loading notebook from: {os.path.abspath(sample_path)}")
    notebook = load_notebook(sample_path)
    code_cells = extract_code_cells(notebook)

    for idx, code in enumerate(code_cells):
        print(f"\n--- CODE CELL {idx + 1} ---\n")
        print(code)

    print("\n--- EXTRACTED FUNCTIONS ---")
    funcs = extract_functions_from_notebook(sample_path)
    for func in funcs:
        print(func)

