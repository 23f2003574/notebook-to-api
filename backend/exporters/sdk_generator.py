import json
import re
from pathlib import Path


# Client methods generate_python_sdk/generate_typescript_sdk always emit
# themselves -- get_task/wait_for_task (task polling) and
# list_tasks/delete_task/delete_completed_tasks/delete_failed_tasks (task
# management) -- independent of _build_method_names' per-path loop below.
# Unlike the built-in server-side routes these call (RESERVED_INFRASTRUCTURE_
# NAMES in api_generator.py), the *client* method names below have no
# equivalent guard stopping a notebook function from being named exactly
# one of them: "wait_for_task" or "list_tasks" compiles into the server
# fine (it isn't a reserved identifier there), but would silently shadow
# this exact hardcoded client method at class-body evaluation time --
# confirmed: a notebook exposing a `wait_for_task` endpoint produced two
# `def wait_for_task(...)` methods, with the second (the notebook's own,
# taking a `payload: dict`) overwriting the real polling helper, breaking
# every *other* background endpoint's *_and_wait companion, which calls
# self.wait_for_task(...) internally.
PYTHON_RESERVED_CLIENT_METHOD_NAMES = frozenset({
    "get_task", "wait_for_task", "list_tasks", "delete_task",
    "delete_completed_tasks", "delete_failed_tasks",
    "health", "ready", "info", "config", "metrics", "uptime",
    "auth_status", "auth_info", "auth_validate",
    # Confirmed exploitable: base_url/api_key/timeout are the client's
    # own __init__-set *instance attributes* (self.base_url, self.api_key,
    # self.timeout -- every other method here reads them for exactly
    # that reason), not just names an unrelated method happened to share.
    # A notebook path sanitizing to one of these (e.g. "/base_url")
    # compiled into a same-named client *method* fine -- but an instance
    # attribute set in __init__ shadows a class-level method of the same
    # name on attribute lookup, so `self.base_url` from then on resolves
    # to the string, not the method: calling client.base_url(...) fails
    # with "'str' object is not callable", and nothing about the method
    # definition itself signals why.
    "base_url", "api_key", "timeout", "max_retries", "backoff_factor",
})

# Same hazard as PYTHON_RESERVED_CLIENT_METHOD_NAMES above, for the
# TypeScript client's own hardcoded method names -- method names aren't
# case-converted from the notebook function/path they came from (see
# _method_name_from_path below), so a notebook function named e.g.
# "waitForTask" would collide with this client's own waitForTask exactly
# the same way "wait_for_task" collides with the Python client's.
TYPESCRIPT_RESERVED_CLIENT_METHOD_NAMES = frozenset({
    "getTask", "waitForTask", "listTasks", "deleteTask",
    "deleteCompletedTasks", "deleteFailedTasks",
    "health", "ready", "info", "config", "metrics", "uptime",
    "authStatus", "authInfo", "authValidate",
    # Same hazard as PYTHON_RESERVED_CLIENT_METHOD_NAMES's base_url/
    # api_key/timeout above: baseUrl/apiKey/timeoutMs are this client's
    # own private instance fields (this.baseUrl, this.apiKey,
    # this.timeoutMs), and a class can't declare a field and a method
    # under the same identifier -- "Duplicate identifier" at TypeScript
    # compile time, not just a same-named method colliding.
    "baseUrl", "apiKey", "timeoutMs", "maxRetries", "backoffFactor",
    # "request" was, confirmed, already missing here even before this
    # retry/backoff addition: `private async request(path, payload)` is
    # this client's own hardcoded POST helper every per-function endpoint
    # routes through, but nothing stopped a notebook path sanitizing to
    # "request" (e.g. "/request") from generating a same-named *public*
    # method -- a duplicate identifier, "Duplicate identifier" at
    # TypeScript compile time, the exact hazard this whole reserved-name
    # set otherwise exists to prevent. requestWithRetry/retryDelayMs/sleep
    # are this same retry addition's own new private helpers, reserved for
    # the identical reason.
    "request", "requestWithRetry", "retryDelayMs", "sleep",
})


def _method_name_from_path(path: str) -> str:
    """Convert an API path into a valid Python identifier.

    Notebook-derived endpoints are always single-segment (e.g.
    '/train_model'), but a real compiled app also exposes built-in
    multi-segment paths such as '/tasks/cleanup' and '/tasks/reset'. A
    naive `lstrip('/').replace('-', '_')` leaves the '/' in place, which
    produces an invalid method definition like `def tasks/cleanup(...)`.
    Replacing every run of non-identifier characters (slashes, hyphens,
    path-parameter braces) with a single underscore keeps names readable
    and always syntactically valid.
    """
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", path.strip("/")).strip("_")

    if not name:
        name = "root"
    elif name[0].isdigit():
        name = f"_{name}"

    return name


def _pascal_case(name):
    """PascalCase identifier for `name` (a method name from
    _build_method_names, e.g. "train_model" or "tasks_cleanup_2") -- used
    by generate_typescript_sdk to build each function's own uniquely-named
    {Pascal}Request/{Pascal}Response TypeScript interfaces below, instead
    of the bare Record<string, unknown>/any every method's own payload/
    return type used to be typed as regardless of what the notebook
    function actually expects or returns.
    """
    return "".join(part[:1].upper() + part[1:] for part in name.split("_") if part)


# Bare TypeScript equivalents for a Python annotation this tool has no
# other way to learn the real shape of -- see
# _python_type_to_typescript below for what these back.
_PYTHON_SCALAR_TO_TS = {
    "int": "number", "float": "number", "complex": "number",
    "str": "string", "bool": "boolean", "bytes": "string",
    "None": "null", "NoneType": "null",
    "Any": "unknown", "object": "unknown", "dict": "Record<string, unknown>",
}


def _bracket_inner(type_str, open_bracket_index):
    """The content strictly between the "[" at `open_bracket_index` and
    its own matching "]" in `type_str`, respecting nested brackets (so
    "List[Dict[str, int]]"'s outer brackets correctly capture the whole
    "Dict[str, int]" inner segment, not just up to its first "]").
    """
    depth = 0

    for index in range(open_bracket_index, len(type_str)):

        if type_str[index] == "[":
            depth += 1
        elif type_str[index] == "]":
            depth -= 1
            if depth == 0:
                return type_str[open_bracket_index + 1:index]

    return type_str[open_bracket_index + 1:]


def _split_top_level(text, separator=","):
    """Split `text` on `separator`, ignoring one that's nested inside a
    "[...]" -- e.g. splitting "str, Dict[str, int]" on "," must yield
    ["str", "Dict[str, int]"], not a spurious three-way split on the
    comma that's actually part of the nested Dict's own arguments.
    """
    parts = []
    depth = 0
    current = []

    for char in text:

        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1

        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)

    parts.append("".join(current))

    return [part.strip() for part in parts]


def _as_typescript_array_element(ts_type):
    """`ts_type` wrapped as a TypeScript array element type -- parenthesized
    when it's itself a union ("number | null"), since "number | null[]"
    parses as "number | (null[])" in TypeScript, not the intended
    "(number | null)[]".
    """
    return f"({ts_type})[]" if " | " in ts_type else f"{ts_type}[]"


def _python_type_to_typescript(type_str):
    """The closest TypeScript type for `type_str` (a raw, `ast.unparse`d
    Python annotation, e.g. "int", "List[str]", "Optional[int]" -- the
    same shape arg["type"]/return_type already carry throughout this
    codebase), or "unknown" for anything not recognized below.

    Before this, every generated TypeScript method's own payload
    parameter and return value were typed as a bare Record<string,
    unknown>/any regardless of what the notebook function actually
    expects or returns -- throwing away the single biggest practical
    advantage of generating a *TypeScript* client over a plain JS one
    (compile-time type checking, IDE autocomplete) for every field of
    every function this tool compiles.

    Deliberately bounded, not a general Python-type-system-to-TypeScript
    translator: an unrecognized name (a notebook-defined class/Enum,
    a typing construct not handled below) falls back to "unknown" --
    always a valid, safe TypeScript type, just not a specific one --
    rather than guessing at a type this tool has no real way to know
    from a bare annotation string alone. The identical "bounded, falls
    back to something safely generic rather than guessing" contract
    normalize_type_annotation (backend/parser/ast_parser.py) already
    follows for a related but distinct purpose (simplifying an
    annotation for an example value, not translating it to another
    language's type system).
    """
    if not type_str:
        return "unknown"

    type_str = type_str.strip()

    if type_str in _PYTHON_SCALAR_TO_TS:
        return _PYTHON_SCALAR_TO_TS[type_str]

    for prefix in ("List[", "list[", "Set[", "set[", "FrozenSet[", "frozenset["):
        if type_str.startswith(prefix):
            inner = _bracket_inner(type_str, len(prefix) - 1)
            return _as_typescript_array_element(_python_type_to_typescript(inner))

    for prefix in ("Tuple[", "tuple["):
        if type_str.startswith(prefix):
            return "unknown[]"

    for prefix in ("Dict[", "dict["):
        if type_str.startswith(prefix):
            inner = _bracket_inner(type_str, len(prefix) - 1)
            parts = _split_top_level(inner)
            value_type = (
                _python_type_to_typescript(parts[1]) if len(parts) == 2 else "unknown"
            )
            return f"Record<string, {value_type}>"

    if type_str.startswith("Optional["):
        inner = _bracket_inner(type_str, len("Optional") )
        return f"{_python_type_to_typescript(inner)} | null"

    if type_str.startswith("Union["):
        inner = _bracket_inner(type_str, len("Union"))
        mapped = [_python_type_to_typescript(part) for part in _split_top_level(inner)]
        return " | ".join(dict.fromkeys(mapped))

    if type_str.startswith("Annotated["):
        inner = _bracket_inner(type_str, len("Annotated") )
        first_arg = _split_top_level(inner)[0]
        return _python_type_to_typescript(first_arg)

    if "|" in type_str:
        # A top-level PEP 604 union (e.g. "int | None") -- not one nested
        # inside a generic's own arguments, which _split_top_level's own
        # bracket-depth tracking already keeps out of this split.
        parts = _split_top_level(type_str, "|")
        if len(parts) > 1:
            mapped = [_python_type_to_typescript(part) for part in parts]
            return " | ".join(dict.fromkeys(mapped))

    return "unknown"


_JSON_SCHEMA_TYPE_TO_TS = {
    "integer": "number", "number": "number", "string": "string",
    "boolean": "boolean", "null": "null",
}


def _json_schema_type_to_typescript(prop_schema):
    """The closest TypeScript type for `prop_schema` (one property's own
    JSON-schema object from an OpenAPI request body model, e.g.
    {"type": "integer"} or {"anyOf": [{"type": "string"}, {"type":
    "null"}]} for an Optional[str] field) -- used to type each generated
    method's own {Pascal}Request interface fields (see
    _typescript_request_interface below) directly from the exact schema
    Pydantic itself validates a real request against, rather than
    re-deriving it from a raw Python annotation string a second time
    (which _python_type_to_typescript above does instead, for the
    response side, where no such schema exists at all -- see
    generate_fastapi_code's own "x-notebook-to-api-return-type").
    """
    if not isinstance(prop_schema, dict):
        return "unknown"

    if "anyOf" in prop_schema:
        mapped = [
            _json_schema_type_to_typescript(sub) for sub in prop_schema["anyOf"]
        ]
        return " | ".join(dict.fromkeys(mapped))

    schema_type = prop_schema.get("type")

    if schema_type == "array":
        item_type = _json_schema_type_to_typescript(prop_schema.get("items", {}))
        return _as_typescript_array_element(item_type)

    if schema_type == "object":
        return "Record<string, unknown>"

    return _JSON_SCHEMA_TYPE_TO_TS.get(schema_type, "unknown")


def _request_body_schema(openapi_schema, methods):
    """The {"properties", "required"} JSON-schema object backing
    `methods`'s own POST operation request body (see
    _json_schema_type_to_typescript above), resolved from its own
    "$ref" against `openapi_schema`'s "components"/"schemas" -- or None
    if it has no request body, or an unrecognized/missing $ref (a
    schema this tool didn't itself generate).
    """
    post = methods.get("post") or {}

    ref = (
        post.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
        .get("$ref")
    )

    if not ref:
        return None

    schema_name = ref.rsplit("/", 1)[-1]

    return openapi_schema.get("components", {}).get("schemas", {}).get(schema_name)


def _typescript_request_interface(interface_name, request_schema):
    """TypeScript source lines for `interface_name`, one field per
    property in `request_schema` (see _request_body_schema above),
    typed via _json_schema_type_to_typescript. A property not in
    `request_schema`'s own "required" list (the notebook function's own
    parameter had a default) is marked optional ("?:") -- matching the
    exact same "a field with a default isn't required in the JSON body"
    contract the generated server side's own Pydantic model already
    enforces.

    Falls back to a single untyped index signature when `request_schema`
    is None or carries no properties at all (a zero-parameter function,
    whose real request model has no fields to type) -- an empty
    TypeScript interface body is valid but pointless busywork for a
    caller to look at.
    """
    lines = [f"export interface {interface_name} {{"]

    properties = (request_schema or {}).get("properties") or {}
    required = set((request_schema or {}).get("required") or [])

    if not properties:
        lines.append("  [key: string]: unknown;")
    else:
        for prop_name, prop_schema in properties.items():
            optional = "" if prop_name in required else "?"
            ts_type = _json_schema_type_to_typescript(prop_schema)
            lines.append(f"  {prop_name}{optional}: {ts_type};")

    lines.append("}")

    return lines


def _typescript_response_interface(interface_name, return_type):
    """TypeScript source lines for a synchronous endpoint's own
    {Pascal}Response interface -- {"result": ...}, exactly the shape its
    own generated `return {"result": result}` (api_generator.py) always
    sends, typed via _python_type_to_typescript's own mapping of
    "x-notebook-to-api-return-type".
    """
    return [
        f"export interface {interface_name} {{",
        f"  result: {_python_type_to_typescript(return_type)};",
        "}",
    ]


def _typescript_task_submission_interface(interface_name):
    """TypeScript source lines for a background endpoint's own
    {Pascal}Response interface -- {"task_id": ..., "status":
    "processing"}, exactly the shape its own generated `return
    {"task_id": task_id, "status": "processing"}` (api_generator.py)
    always sends immediately (never the notebook function's own eventual
    result -- see _typescript_task_result_interface below for that).
    """
    return [
        f"export interface {interface_name} {{",
        "  task_id: string;",
        '  status: "processing";',
        "}",
    ]


def _typescript_task_result_interface(interface_name, return_type):
    """TypeScript source lines for a background endpoint's own
    {Pascal}TaskResult interface -- the finished task record its own
    *_and_wait companion (via waitForTask) eventually resolves to: either
    {"status": "completed", "result": ...} or {"status": "failed",
    "error": ...} (see _run_background_task, api_generator.py). Not a
    strict discriminated union keyed on "status" -- both "result" and
    "error" are simply optional, which is looser than the real either/or
    contract but avoids the extra complexity of a literal-typed union
    for a shape a caller is expected to branch on by checking
    `.status === "completed"` at runtime regardless of how precisely
    this interface types it.
    """
    return [
        f"export interface {interface_name} {{",
        "  task_id?: string;",
        "  status: string;",
        f"  result?: {_python_type_to_typescript(return_type)};",
        "  error?: string;",
        "}",
    ]


# Python annotations this codebase's own raw, `ast.unparse`d type
# strings (arg["type"]/return_type) already use verbatim -- see
# _python_type_to_safe_python_annotation below for why these pass
# through unchanged while everything else doesn't.
_PYTHON_SAFE_SCALARS = {
    "int", "float", "str", "bool", "bytes", "complex", "None", "Any",
}


def _python_type_to_safe_python_annotation(type_str):
    """The closest *safe* Python type annotation for `type_str` (a raw,
    `ast.unparse`d Python annotation, e.g. "int", "List[str]",
    "Optional[int]" -- the same shape arg["type"]/return_type already
    carry throughout this codebase), or "Any" for anything not
    recognized below.

    Unlike _python_type_to_typescript's own identical-looking mapping
    (which translates *into* TypeScript), the input here is already
    valid Python syntax -- so this is closer to a validating pass-through
    than a translation. That distinction matters for exactly one case:
    a bare, unrecognized identifier (a notebook-defined class or Enum,
    e.g. "Priority") *is* syntactically valid Python and would compile
    fine as a literal type annotation -- but the standalone SDK client
    file being generated here has no import for it at all, so writing it
    through unchanged would raise a bare NameError the moment the
    generated client module is loaded, not just a type-checker warning.
    Falling back to "Any" instead -- always defined, always valid --
    keeps the generated file importable regardless of what a notebook's
    own return type annotation actually names, at the cost of losing
    precision for exactly that one case. The identical "bounded, falls
    back to something safely generic rather than guessing" contract
    _python_type_to_typescript and normalize_type_annotation (backend/
    parser/ast_parser.py) already follow for their own related purposes.
    """
    if not type_str:
        return "Any"

    type_str = type_str.strip()

    if type_str in _PYTHON_SAFE_SCALARS:
        return type_str

    if type_str in ("dict", "Dict"):
        return "Dict[str, Any]"

    if type_str in ("list", "List"):
        return "List[Any]"

    for prefix in ("List[", "list[", "Set[", "set[", "FrozenSet[", "frozenset["):
        if type_str.startswith(prefix):
            inner = _bracket_inner(type_str, len(prefix) - 1)
            return f"List[{_python_type_to_safe_python_annotation(inner)}]"

    for prefix in ("Tuple[", "tuple["):
        if type_str.startswith(prefix):
            return "List[Any]"

    for prefix in ("Dict[", "dict["):
        if type_str.startswith(prefix):
            inner = _bracket_inner(type_str, len(prefix) - 1)
            parts = _split_top_level(inner)
            value_type = (
                _python_type_to_safe_python_annotation(parts[1])
                if len(parts) == 2 else "Any"
            )
            return f"Dict[str, {value_type}]"

    if type_str.startswith("Optional["):
        inner = _bracket_inner(type_str, len("Optional"))
        return f"Optional[{_python_type_to_safe_python_annotation(inner)}]"

    if type_str.startswith("Union["):
        inner = _bracket_inner(type_str, len("Union"))
        mapped = [
            _python_type_to_safe_python_annotation(part)
            for part in _split_top_level(inner)
        ]
        return _join_python_union(mapped)

    if type_str.startswith("Annotated["):
        inner = _bracket_inner(type_str, len("Annotated"))
        first_arg = _split_top_level(inner)[0]
        return _python_type_to_safe_python_annotation(first_arg)

    if "|" in type_str:
        # A top-level PEP 604 union -- not one nested inside a generic's
        # own arguments, which _split_top_level's own bracket-depth
        # tracking already keeps out of this split.
        parts = _split_top_level(type_str, "|")
        if len(parts) > 1:
            mapped = [
                _python_type_to_safe_python_annotation(part) for part in parts
            ]
            return _join_python_union(mapped)

    # An unrecognized bare identifier -- see this function's own
    # docstring for why that must never pass through unchanged.
    return "Any"


def _join_python_union(mapped_types):
    """Deduplicated Optional[T]/Union[...] source for `mapped_types` (each
    already `_python_type_to_safe_python_annotation`-mapped) -- "None"
    paired with exactly one other type collapses to Optional[T], matching
    how a human would actually write that instead of Union[T, None].
    """
    deduped = list(dict.fromkeys(mapped_types))

    if len(deduped) == 1:
        return deduped[0]

    if len(deduped) == 2 and "None" in deduped:
        other = deduped[0] if deduped[1] == "None" else deduped[1]
        return f"Optional[{other}]"

    return f"Union[{', '.join(deduped)}]"


_JSON_SCHEMA_TYPE_TO_PYTHON = {
    "integer": "int", "number": "float", "string": "str", "boolean": "bool",
    "null": "None",
}


def _json_schema_type_to_python(prop_schema):
    """The closest Python type annotation for `prop_schema` (one
    property's own JSON-schema object from an OpenAPI request body
    model) -- the Python-client mirror of
    _json_schema_type_to_typescript above; see its own docstring for why
    this reads the real Pydantic-validated schema rather than
    re-deriving a type from a raw annotation string.
    """
    if not isinstance(prop_schema, dict):
        return "Any"

    if "anyOf" in prop_schema:
        mapped = [
            _json_schema_type_to_python(sub) for sub in prop_schema["anyOf"]
        ]
        return _join_python_union(mapped)

    schema_type = prop_schema.get("type")

    if schema_type == "array":
        return f"List[{_json_schema_type_to_python(prop_schema.get('items', {}))}]"

    if schema_type == "object":
        return "Dict[str, Any]"

    return _JSON_SCHEMA_TYPE_TO_PYTHON.get(schema_type, "Any")


def _python_typeddict_lines(class_name, required_fields, optional_fields):
    """Python source lines defining `class_name` as a typing.TypedDict,
    one field per entry in `required_fields`/`optional_fields` (each
    {name: python_type_annotation}).

    A TypedDict can't mark individual fields required/optional inline
    the way a TypeScript interface's own "?:" can -- `total=` is a
    whole-class setting. When both required and optional fields are
    present, this splits into a required base class plus a `total=False`
    subclass adding the optional ones, the standard, dependency-free
    (works on Python 3.8+, no typing_extensions.NotRequired needed)
    pattern for mixing the two in one TypedDict. When only one kind is
    present, a single class is emitted directly instead -- the split's
    entire purpose is representing a genuine mix, so it would just be
    unnecessary indirection here.
    """
    if not required_fields and not optional_fields:
        return [f"class {class_name}(TypedDict, total=False):", "    pass", ""]

    if required_fields and optional_fields:
        base_name = f"_{class_name}Base"
        lines = [f"class {base_name}(TypedDict):"]
        for name, ptype in required_fields.items():
            lines.append(f"    {name}: {ptype}")
        lines.append("")
        lines.append(f"class {class_name}({base_name}, total=False):")
        for name, ptype in optional_fields.items():
            lines.append(f"    {name}: {ptype}")
        lines.append("")
        return lines

    if required_fields:
        lines = [f"class {class_name}(TypedDict):"]
        for name, ptype in required_fields.items():
            lines.append(f"    {name}: {ptype}")
        lines.append("")
        return lines

    lines = [f"class {class_name}(TypedDict, total=False):"]
    for name, ptype in optional_fields.items():
        lines.append(f"    {name}: {ptype}")
    lines.append("")
    return lines


def _python_request_typeddict_lines(class_name, request_schema):
    """Python source lines for `class_name`, a TypedDict typing the exact
    request body `request_schema` (see _request_body_schema above)
    describes -- the Python-client mirror of
    _typescript_request_interface above. A field not in
    `request_schema`'s own "required" list (the notebook function's own
    parameter had a default) is optional, matching the exact same "a
    field with a default isn't required in the JSON body" contract the
    generated server side's own Pydantic model already enforces.
    """
    properties = (request_schema or {}).get("properties") or {}
    required = set((request_schema or {}).get("required") or [])

    required_fields = {
        name: _json_schema_type_to_python(prop)
        for name, prop in properties.items() if name in required
    }
    optional_fields = {
        name: _json_schema_type_to_python(prop)
        for name, prop in properties.items() if name not in required
    }

    return _python_typeddict_lines(class_name, required_fields, optional_fields)


def _is_background_path(methods):
    """Whether `methods` (a path's {"post": {...}, ...} operations dict
    from an OpenAPI schema this tool generated) is a background/task_id
    endpoint, per the "x-notebook-to-api-async" marker
    generate_fastapi_code already stamps onto its POST operation (see
    api_generator.py) -- the same flag POST /api/compile's own
    "endpoints" field and inspect_notebook_data's "endpoints" field
    already key off of, reused here instead of re-deriving it from
    LONG_RUNNING_KEYWORDS a third time.
    """
    return bool((methods.get("post") or {}).get("x-notebook-to-api-async"))


def _build_wait_method_names(method_names, paths):
    """A collision-free companion method name for each background path in
    `method_names`, submitting the task and blocking until it finishes.

    Before this, a background endpoint's generated method (e.g.
    train_model) looked identical to a synchronous one: it returned
    {"task_id": ..., "status": "processing"} immediately, with nothing in
    the generated client actually connecting that to get_task/
    wait_for_task -- a caller had to already know, from reading the
    OpenAPI docs separately, which methods needed polling at all.

    Derived from each path's own already-assigned method name with an
    "_and_wait" suffix, disambiguated against every name already in use
    (not just other "_and_wait" names) the same way _build_method_names
    disambiguates the base names themselves: a real notebook function
    could easily be named e.g. "train_model_and_wait" and collide with
    the synthesized companion name for "/train_model".
    """
    used_names = set(method_names.values())
    wait_method_names = {}

    for path, methods in paths.items():

        if not _is_background_path(methods):
            continue

        base_name = f"{method_names[path]}_and_wait"
        candidate = base_name
        suffix = 2

        while candidate in used_names:
            candidate = f"{base_name}_{suffix}"
            suffix += 1

        used_names.add(candidate)
        wait_method_names[path] = candidate

    return wait_method_names


def _load_openapi_schema(openapi_path):
    """Read and JSON-decode the OpenAPI schema at `openapi_path`.

    Both generate_python_sdk and generate_typescript_sdk read their input
    this same way, but previously did it inline as a bare `json.load(f)`
    with no handling of its own: a caller pointing --openapi (or POST
    /api/export-sdk) at a file that isn't valid JSON crashed with
    json.JSONDecodeError's raw, low-level message ("Expecting value: line
    1 column 1 (char 0)") -- no indication of *why*, even though the most
    likely real-world cause is one this tool itself creates: POST
    /api/export-openapi and the CLI's own `export-openapi --format yaml`
    write a YAML file this function was never able to read, so a caller
    who exported YAML and then pointed export-sdk at that exact file (a
    completely reasonable thing to try, since both commands read/write
    the same GENERATED_DIR by default) got a confusing crash instead of
    being told export-sdk only reads JSON schemas.
    """
    with open(openapi_path, "r", encoding="utf-8") as f:
        raw = f.read()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        hint = (
            " This looks like a YAML export (export-openapi --format "
            "yaml) -- export-sdk only reads JSON schemas; re-export with "
            "--format json (the default) first."
            if Path(openapi_path).suffix.lower() in (".yaml", ".yml")
            else ""
        )
        raise ValueError(
            f"'{openapi_path}' is not a valid OpenAPI JSON schema: {exc}.{hint}"
        ) from exc


def _operation_description(methods):
    """The description text FastAPI's OpenAPI schema already carries for
    this path's POST operation -- generate_fastapi_code always sets one
    (api_generator.py): either a notebook function's own docstring, when
    it wrote one, or this tool's own auto-generated fallback sentence.
    Before this, that text was read by nothing at all -- every generated
    client method's docstring/JSDoc comment was pure hardcoded boilerplate
    ("Call the `/path` endpoint with JSON payload.") with zero connection
    to what the endpoint actually does, even though the schema being read
    right here already carries real documentation for it.

    Returns "" (not None) when absent, so callers can build doc text with
    a plain truthiness check rather than an extra None check -- absent
    only for an OpenAPI schema this tool didn't generate itself
    (export-sdk accepts any --openapi file, not just one this tool
    produced).
    """
    return ((methods.get("post") or {}).get("description") or "").strip()


def _python_method_docstring(description, static_text):
    """Combine `description` (see _operation_description) with a client
    method's own static explanatory text into one docstring statement,
    repr()'d rather than embedded as a hand-written triple-quoted literal
    like this client's other, fully static docstrings (get_task,
    wait_for_task, ...): description is arbitrary, notebook-author-
    controlled text (or this tool's own auto-generated fallback, which
    already includes each parameter's bare name) that can legitimately
    contain a double quote, a triple-quote sequence, or a backslash --
    embedding it directly into a \"\"\"...\"\"\" literal would let that
    content close the docstring early and corrupt the rest of the
    generated client into a SyntaxError, the exact hazard already fixed
    for the compiled app itself (see e91b1fa).
    """
    doc = f"{description}\n\n{static_text}" if description else static_text
    return repr(doc)


def _jsdoc_lines(description, static_text_lines, indent="  "):
    """Build a `/** ... */` JSDoc comment block's lines, combining
    `description` (see _operation_description) with a method's own static
    explanatory text (`static_text_lines`: already-wrapped lines with no
    leading ` * ` prefix or `*/` terminator of their own).

    Unlike Python's repr() for _python_method_docstring above, JS/TS block
    comments have no escape mechanism at all: a literal "*/" anywhere
    inside `description` would close the comment early, corrupting
    whatever source follows it -- potentially even swallowing the method
    signature the comment was meant to document. Neutralized by inserting
    a space into any such sequence before it's ever embedded, the same
    defensive substitution doc-comment generators for user-controlled
    text commonly use, since there's no syntactically "safe" encoding of
    an arbitrary string inside a JSDoc block the way repr() provides for
    a Python string literal.
    """
    text_lines = []

    if description:
        text_lines.extend(description.replace("*/", "* /").split("\n"))
        text_lines.append("")

    text_lines.extend(static_text_lines)

    lines = [f"{indent}/**"]

    for line in text_lines:
        safe_line = line.replace("*/", "* /")
        lines.append(f"{indent} * {safe_line}".rstrip())

    lines.append(f"{indent} */")

    return lines


def _build_method_names(paths, reserved_names=frozenset()):
    """Map each POST path to a collision-free client method name.

    Two different paths can sanitize to the same identifier -- e.g. a
    notebook function literally named "tasks_cleanup" (path
    "/tasks_cleanup") collides with the always-present built-in
    "/tasks/cleanup" route, since both reduce to "tasks_cleanup" once
    slashes become underscores. Confirmed before this fix: the second
    `def`/method definition silently shadowed the first at class-body
    evaluation time (Python) or produced a duplicate-method TypeScript
    compile error, either way permanently hiding one endpoint from the
    generated SDK with no error surfaced anywhere.

    `reserved_names` (PYTHON_RESERVED_CLIENT_METHOD_NAMES or
    TYPESCRIPT_RESERVED_CLIENT_METHOD_NAMES, passed in by
    generate_python_sdk/generate_typescript_sdk) seeds the same
    disambiguation for the client's own hardcoded methods (get_task,
    wait_for_task, list_tasks, ...), which live outside this per-path
    loop entirely and so can't be discovered by scanning `paths` alone --
    without seeding, a path colliding with one of *those* names shadowed
    them instead of being renamed the same way two colliding paths
    already are.
    """
    used_names = set(reserved_names)
    method_names = {}

    for path, methods in paths.items():
        if not methods.get("post"):
            continue

        base_name = _method_name_from_path(path)
        candidate = base_name
        suffix = 2

        while candidate in used_names:
            candidate = f"{base_name}_{suffix}"
            suffix += 1

        used_names.add(candidate)
        method_names[path] = candidate

    return method_names


def generate_python_sdk(
    openapi_path: str = "generated/openapi.json",
    output_path: str = "generated/sdk/python_client.py",
):
    """Generate a minimal Python SDK client from a FastAPI OpenAPI schema.

    The generated client contains a ``NotebookAPIClient`` class with a method for each
    POST endpoint defined in the OpenAPI spec. Each method performs a ``requests.post``
    call to the corresponding endpoint and returns ``response.json()``.

    Long-running notebook functions (see LONG_RUNNING_KEYWORDS in
    api_generator.py) don't return their result directly -- their endpoint
    enqueues a background task and immediately returns
    ``{"task_id": ..., "status": "processing"}``. Before get_task/
    wait_for_task existed, a caller of the generated client had no way to
    actually retrieve that result short of hand-writing their own polling
    loop against GET /tasks/{task_id}, even though the client already
    knows the base_url and api_key needed to do it.

    Also always includes health/ready/info/metrics/uptime/auth_status/
    auth_info/auth_validate -- every compiled app's own built-in GET
    routes for liveness/readiness, service metadata, request metrics, and
    auth configuration (see RESERVED_INFRASTRUCTURE_NAMES in
    api_generator.py, which guarantees they exist and can never be
    shadowed by a notebook function). Like get_task/list_tasks above,
    these are hardcoded rather than derived from the OpenAPI paths loop
    below, which only ever emits a method for a POST path.
    """
    # Load OpenAPI schema
    schema = _load_openapi_schema(openapi_path)

    paths = schema.get("paths", {})
    method_names = _build_method_names(paths, PYTHON_RESERVED_CLIENT_METHOD_NAMES)
    wait_method_names = _build_wait_method_names(method_names, paths)
    # Prepare client code lines
    lines = []
    lines.append("import os")
    lines.append("import time")
    lines.append("import requests")
    # Confirmed missing before this feature: every generated method's
    # own payload parameter was typed as a bare dict, with no return
    # annotation at all -- throwing away everything mypy/pyright/an IDE
    # could otherwise tell a caller about a typo'd field name or a wrong
    # type, the exact same gap Commit #7 already closed for the
    # TypeScript client. Unconditionally imported (whether or not this
    # particular schema ends up using all of them) rather than tracked
    # precisely per-schema -- an unused import is harmless in a generated
    # file nothing lints, and tracking it exactly would add real
    # complexity for no functional benefit.
    lines.append(
        "from typing import Any, Dict, List, Literal, Optional, TypedDict, Union"
    )
    lines.append("")
    # Every {Pascal}Request/{Pascal}Response/{Pascal}TaskResult TypedDict
    # the per-path loop below builds is collected into typeddict_lines
    # (kept separate from `lines`, the class body being built below) and
    # spliced in at class_declaration_index, right before the class
    # itself -- they must be defined *before* NotebookAPIClient, since a
    # method's own `payload: TrainModelRequest` annotation is evaluated
    # eagerly, at `def` time, when this module loads; the per-path loop
    # that discovers what to generate for each one doesn't run until
    # after the class declaration line below is already appended.
    typeddict_lines = []
    class_declaration_index = len(lines)
    lines.append("class NotebookAPIClient:")
    # Every non-2xx status a real deployment can return for reasons that
    # have nothing to do with the caller's own request being wrong --
    # NOTEBOOK_API_RATE_LIMIT_PER_MINUTE (429), or a rolling deploy/
    # restart briefly returning 502/503/504. wait_for_task already treats
    # exactly these as transient while polling; _request below extends
    # the identical judgment to every *other* call this client makes.
    lines.append("    _TRANSIENT_STATUS_CODES = (429, 502, 503, 504)")
    lines.append("")
    lines.append(
        "    def __init__(self, base_url: str, api_key: str = None, "
        "timeout: float = 30.0, max_retries: int = 3, "
        "backoff_factor: float = 0.5):"
    )
    lines.append("        self.base_url = base_url.rstrip('/')")
    lines.append("        # Generated endpoints require the same X-API-Key header the")
    lines.append("        # generated app itself defaults to (see NOTEBOOK_API_KEY in")
    lines.append("        # api_generator.py) so the client works out of the box locally.")
    lines.append("        self.api_key = api_key or os.getenv(")
    lines.append("            'NOTEBOOK_API_KEY', 'notebook-to-api-dev-key'")
    lines.append("        )")
    lines.append("        # requests has no default socket timeout of its own -- a call")
    lines.append("        # with none set can hang indefinitely on a server that accepts")
    lines.append("        # the connection but never responds (a stalled deploy target, a")
    lines.append("        # network partition, ...), with nothing on the client side to")
    lines.append("        # ever give up. wait_for_task's own `timeout` bounds its polling")
    lines.append("        # *loop*, but each individual request it (and every other method")
    lines.append("        # here) makes had no bound of its own until now.")
    lines.append("        self.timeout = timeout")
    # Confirmed missing before this: only wait_for_task's own polling loop
    # ever retried a transient failure -- every other call this client
    # makes (submitting a notebook function, list_tasks/delete_task/...,
    # health/ready/info/config/metrics/uptime/auth_*) raised immediately
    # on a single 429/502/503/504 or connection error, even though the
    # generated server side already goes out of its way to report exactly
    # when a caller should retry (X-RateLimit-Reset, Retry-After -- see
    # _enforce_rate_limit in api_generator.py). max_retries=0 disables
    # this entirely, preserving the previous immediate-raise behavior.
    lines.append("        self.max_retries = max_retries")
    lines.append("        self.backoff_factor = backoff_factor")
    lines.append("")
    lines.append("    def _retry_delay(self, response, attempt):")
    lines.append(
        '        """Seconds to wait before retrying: honors a 429\'s own '
        "Retry-After"
    )
    lines.append(
        "        header when present (the generated server always sends "
        "one -- see"
    )
    lines.append(
        "        _enforce_rate_limit in api_generator.py), else "
        'exponential backoff."""'
    )
    lines.append("        if response is not None:")
    lines.append('            retry_after = response.headers.get("Retry-After")')
    lines.append("            if retry_after is not None:")
    lines.append("                try:")
    lines.append("                    return max(0.0, float(retry_after))")
    lines.append("                except ValueError:")
    lines.append("                    pass")
    lines.append("        return self.backoff_factor * (2 ** attempt)")
    lines.append("")
    lines.append("    def _request(self, request_fn):")
    lines.append(
        '        """Run `request_fn` (a zero-argument callable making one '
        'HTTP call --'
    )
    lines.append(
        "        e.g. `lambda: requests.get(url, ...)` -- and returning "
        "its raw"
    )
    lines.append(
        "        response, before raise_for_status()), retrying it on a "
        "transient"
    )
    lines.append(
        "        failure (a _TRANSIENT_STATUS_CODES response, or a "
        "connection-level"
    )
    lines.append(
        "        error carrying no response at all) up to self.max_retries "
        "times."
    )
    lines.append(
        '        Any other failure (a genuine 401/404/...) still raises '
        'immediately,'
    )
    lines.append(
        '        exactly as before this existed."""'
    )
    lines.append("        attempt = 0")
    lines.append("        while True:")
    lines.append("            try:")
    lines.append("                response = request_fn()")
    lines.append("                response.raise_for_status()")
    lines.append("            except Exception as exc:")
    lines.append("                response = getattr(exc, 'response', None)")
    lines.append("                status_code = getattr(response, 'status_code', None)")
    lines.append(
        "                if attempt >= self.max_retries or ("
    )
    lines.append(
        "                    response is not None and "
        "status_code not in self._TRANSIENT_STATUS_CODES"
    )
    lines.append("                ):")
    lines.append("                    raise")
    lines.append("                time.sleep(self._retry_delay(response, attempt))")
    lines.append("                attempt += 1")
    lines.append("                continue")
    lines.append("            return response.json()")
    lines.append("")
    lines.append("    def get_task(self, task_id: str) -> dict:")
    lines.append('        """Fetch the current status/result of a background task."""')
    lines.append("        response = requests.get(")
    lines.append(f'            f"{{self.base_url}}/tasks/{{task_id}}",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        )")
    lines.append("        response.raise_for_status()")
    lines.append("        return response.json()")
    lines.append("")
    lines.append(
        "    def wait_for_task(self, task_id: str, poll_interval: float = 1.0, "
        "timeout: float = 60.0) -> dict:"
    )
    lines.append(
        '        """Poll get_task(task_id) until its status leaves '
        '"processing", returning the finished task record. Raises '
        'TimeoutError if `timeout` seconds pass first.'
    )
    lines.append("")
    lines.append(
        "        A transient failure while polling -- a network-level "
        "error (no"
    )
    lines.append(
        "        `response` at all), or a 429/502/503/504 response -- "
        "exactly what"
    )
    lines.append(
        "        NOTEBOOK_API_RATE_LIMIT_PER_MINUTE or a rolling deploy "
        "would"
    )
    lines.append(
        "        produce mid-poll -- is retried the same as an "
        "ordinary still-"
    )
    lines.append(
        '        processing task, rather than aborting the whole wait. '
        "Any other"
    )
    lines.append(
        "        error (a genuine 401/404/...) still raises immediately."
    )
    lines.append('        """')
    lines.append("        deadline = time.time() + timeout")
    # Only these -- not every non-2xx status -- are retried: each is
    # specifically a "this will very likely succeed if you just ask
    # again shortly" signal (rate limiting, an overloaded/restarting
    # upstream), unlike e.g. 404/401/400 (asking again changes nothing).
    lines.append("        _TRANSIENT_STATUS_CODES = (429, 502, 503, 504)")
    lines.append("        while True:")
    lines.append("            try:")
    lines.append("                task = self.get_task(task_id)")
    lines.append("            except Exception as exc:")
    lines.append(
        "                # get_task's own failure always comes from "
        "requests.Response.raise_for_status()"
    )
    lines.append(
        "                # (an HTTPError carrying the real response "
        "on `.response`) or a connection-level"
    )
    lines.append(
        "                # failure (ConnectionError/Timeout, which "
        "carries no `.response` at all) -- either"
    )
    lines.append(
        "                # way, `.response` (or its absence) is "
        "enough to tell a transient failure from a"
    )
    lines.append(
        "                # real one without needing to import/"
        "reference requests.exceptions directly."
    )
    lines.append("                response = getattr(exc, 'response', None)")
    lines.append("                status_code = getattr(response, 'status_code', None)")
    lines.append(
        "                if response is not None and status_code "
        "not in _TRANSIENT_STATUS_CODES:"
    )
    lines.append("                    raise")
    lines.append("                if time.time() >= deadline:")
    lines.append("                    raise TimeoutError(")
    lines.append(
        '                        f"Task {task_id} did not complete within '
        '{timeout} seconds"'
    )
    lines.append("                    ) from exc")
    lines.append("                time.sleep(poll_interval)")
    lines.append("                continue")
    lines.append("            if task.get('status') != 'processing':")
    lines.append("                return task")
    lines.append("            if time.time() >= deadline:")
    lines.append("                raise TimeoutError(")
    lines.append(
        '                    f"Task {task_id} did not complete within '
        '{timeout} seconds"'
    )
    lines.append("                )")
    lines.append("            time.sleep(poll_interval)")
    lines.append("")
    # list_tasks/delete_task/delete_completed_tasks/delete_failed_tasks are,
    # like get_task/wait_for_task above, hardcoded rather than derived from
    # the per-path loop below: every compiled app guarantees these exact
    # routes (see RESERVED_INFRASTRUCTURE_NAMES in api_generator.py, which
    # blocks a notebook function from ever redefining one of them), but
    # that loop only emits a method for POST paths, so a caller who wanted
    # to see what's still running, or clear out finished tasks, had no way
    # to do it through the generated client at all -- only get_task, for a
    # single already-known task_id. This is the same gap wait_for_task
    # closed for polling a single task, now closed for the rest of a
    # background task's lifecycle.
    # status/limit/offset mirror the generated server's own GET /tasks
    # query params (see list_tasks in api_generator.py) -- before this,
    # the only way to filter to e.g. just the failed tasks, or page
    # through a long-running deployment's history, was to bypass this
    # client entirely and hit the raw HTTP endpoint by hand, since this
    # method sent no query string at all no matter how it was called.
    # Each omitted (the default) preserves list_tasks()'s previous
    # behavior exactly: every task, unfiltered, first page.
    lines.append("    def list_tasks(")
    lines.append(
        "        self, status: str = None, limit: int = None, "
        "offset: int = None,"
    )
    lines.append("    ) -> dict:")
    lines.append(
        '        """List background tasks, with a status-count summary.'
    )
    lines.append("")
    lines.append(
        "        status/limit/offset mirror the generated server's own "
        "GET /tasks"
    )
    lines.append(
        '        query params -- each omitted (the default) returns '
        "every task,"
    )
    lines.append('        unfiltered, first page."""')
    lines.append("        params = {}")
    lines.append("        if status is not None:")
    lines.append('            params["status"] = status')
    lines.append("        if limit is not None:")
    lines.append('            params["limit"] = limit')
    lines.append("        if offset is not None:")
    lines.append('            params["offset"] = offset')
    lines.append("        return self._request(lambda: requests.get(")
    lines.append('            f"{self.base_url}/tasks",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            params=params,")
    lines.append("            timeout=self.timeout,")
    lines.append("        ))")
    lines.append("")
    lines.append("    def delete_task(self, task_id: str) -> dict:")
    lines.append('        """Delete a single background task by id."""')
    lines.append("        return self._request(lambda: requests.delete(")
    lines.append('            f"{self.base_url}/tasks/{task_id}",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        ))")
    lines.append("")
    lines.append("    def delete_completed_tasks(self) -> dict:")
    lines.append('        """Delete every task with status \'completed\'."""')
    lines.append("        return self._request(lambda: requests.delete(")
    lines.append('            f"{self.base_url}/tasks/completed",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        ))")
    lines.append("")
    lines.append("    def delete_failed_tasks(self) -> dict:")
    lines.append('        """Delete every task with status \'failed\'."""')
    lines.append("        return self._request(lambda: requests.delete(")
    lines.append('            f"{self.base_url}/tasks/failed",')
    lines.append('            headers={"X-API-Key": self.api_key},')
    lines.append("            timeout=self.timeout,")
    lines.append("        ))")
    lines.append("")
    # health/ready/info/config/metrics/uptime/auth_status/auth_info/
    # auth_validate are, like get_task/list_tasks/... above, hardcoded
    # rather than derived from the per-path loop below: every compiled app
    # guarantees these exact GET routes too (see
    # RESERVED_INFRASTRUCTURE_NAMES in api_generator.py, which blocks a
    # notebook function from ever redefining any of them), but that loop
    # only ever emits a method for POST paths. A caller wanting a
    # liveness/readiness probe, service info, request metrics, or auth
    # configuration through the generated client itself -- e.g. to back a
    # monitoring dashboard, or confirm the client's own api_key will
    # actually be accepted before calling a real notebook endpoint with
    # it -- previously had no way to do that short of hand-writing the
    # exact same requests.get call get_task already demonstrates this
    # client knows how to make. "config" (GET /config, added alongside
    # this same comment) closes the identical gap for the app's own
    # actual runtime limits (MAX_REQUEST_BODY_BYTES, RATE_LIMIT_PER_MINUTE,
    # ...) -- confirmed missing here even though the server-side endpoint
    # itself already existed, the exact same class of drift
    # RESERVED_INFRASTRUCTURE_NAMES above already guards the *server*
    # side against, just never itself caught on this, the *client* side.
    for infra_method_name, infra_path in (
        ("health", "/health"),
        ("ready", "/ready"),
        ("info", "/info"),
        ("config", "/config"),
        ("metrics", "/metrics"),
        ("uptime", "/uptime"),
        ("auth_status", "/auth/status"),
        ("auth_info", "/auth/info"),
        ("auth_validate", "/auth/validate"),
    ):
        lines.append(f"    def {infra_method_name}(self) -> dict:")
        lines.append(f'        """GET {infra_path}."""')
        lines.append("        return self._request(lambda: requests.get(")
        lines.append(f'            f"{{self.base_url}}{infra_path}",')
        lines.append('            headers={"X-API-Key": self.api_key},')
        lines.append("            timeout=self.timeout,")
        lines.append("        ))")
        lines.append("")
    for path, method_name in method_names.items():
        is_background = _is_background_path(paths[path])
        description = _operation_description(paths[path])
        pascal_name = _pascal_case(method_name)
        request_class = f"{pascal_name}Request"
        response_class = f"{pascal_name}Response"
        return_type = (paths[path].get("post") or {}).get(
            "x-notebook-to-api-return-type"
        )

        typeddict_lines.extend(
            _python_request_typeddict_lines(
                request_class, _request_body_schema(schema, paths[path])
            )
        )

        if is_background:
            typeddict_lines.extend(
                _python_typeddict_lines(
                    response_class,
                    {"task_id": "str", "status": 'Literal["processing"]'},
                    {},
                )
            )
        else:
            typeddict_lines.extend(
                _python_typeddict_lines(
                    response_class,
                    {"result": _python_type_to_safe_python_annotation(return_type)},
                    {},
                )
            )

        # Determine parameter schema (simple request body expecting JSON)
        # Confirmed missing before this feature: the generated server side
        # has accepted an optional ?callback_url= on every background
        # endpoint since it was added (POSTing the finished task's own
        # result there instead of requiring the caller to poll
        # get_task/wait_for_task), and the OpenAPI schema itself already
        # documents it as a real query parameter on that operation -- but
        # neither generated client ever gained any way to actually reach
        # it, short of a caller bypassing this client entirely and
        # building the raw HTTP request by hand. A synchronous endpoint's
        # own method signature is left untouched -- callback_url is a
        # background-only capability the generated server itself never
        # even reads for one.
        if is_background:
            lines.append(
                f"    def {method_name}(self, payload: {request_class}, "
                f"callback_url: str = None) -> {response_class}:"
            )
        else:
            lines.append(
                f"    def {method_name}(self, payload: {request_class}) "
                f"-> {response_class}:"
            )
        if is_background:
            wait_name = wait_method_names[path]
            static_doc = (
                f"Enqueue the `{path}` background task with JSON payload.\n\n"
                'Returns {"task_id": ..., "status": "processing"} '
                "immediately -- not the real result. Call get_task(task_id)/"
                "wait_for_task(task_id) yourself, or use "
                f"{wait_name}(...) to submit and block until the real "
                "result is ready in one call.\n\n"
                "`callback_url`, if given, is POSTed the finished task's "
                "own record ({\"task_id\", \"status\", \"result\"/"
                "\"error\"}) once it completes or fails -- see the "
                "generated server's own ?callback_url= for what it "
                "actually delivers and when."
            )
        else:
            static_doc = f"Call the `{path}` endpoint with JSON payload."
        lines.append(
            f"        {_python_method_docstring(description, static_doc)}"
        )
        lines.append("        return self._request(lambda: requests.post(")
        lines.append(f'            f"{{self.base_url}}{path}",')
        lines.append(f'            json=payload,')
        lines.append(f'            headers={{"X-API-Key": self.api_key}},')
        lines.append(f'            timeout=self.timeout,')
        if is_background:
            lines.append(
                "            params={'callback_url': callback_url} "
                "if callback_url else None,"
            )
        lines.append("        ))")
        lines.append("")

        if is_background:
            wait_name = wait_method_names[path]
            task_result_class = f"{pascal_name}TaskResult"
            typeddict_lines.extend(
                _python_typeddict_lines(
                    task_result_class,
                    {},
                    {
                        "task_id": "str",
                        "status": "str",
                        "result": _python_type_to_safe_python_annotation(
                            return_type
                        ),
                        "error": "str",
                    },
                )
            )
            lines.append(
                f"    def {wait_name}(self, payload: {request_class}, "
                "callback_url: str = None, "
                "poll_interval: float = 1.0, timeout: float = 60.0) -> "
                f"{task_result_class}:"
            )
            and_wait_static_doc = (
                f"Submit `{path}` and block until the background task "
                "finishes, returning its finished task record (see "
                "wait_for_task)."
            )
            lines.append(
                f"        {_python_method_docstring(description, and_wait_static_doc)}"
            )
            lines.append(
                f"        submitted = self.{method_name}(payload, callback_url)"
            )
            lines.append(
                "        return self.wait_for_task("
                "submitted['task_id'], poll_interval, timeout)"
            )
            lines.append("")
    # Splice every TypedDict collected above in just before the class
    # itself -- see class_declaration_index's own docstring above for
    # why they must land there, not simply appended to the end of the
    # file.
    lines[class_declaration_index:class_declaration_index] = (
        typeddict_lines + [""]
    )
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Python SDK generated at {output_path}")


def generate_typescript_sdk(
    openapi_path: str = "generated/openapi.json",
    output_path: str = "generated/sdk/typescript_client.ts",
):
    """Generate a minimal TypeScript SDK client from a FastAPI OpenAPI schema.

    Mirrors :func:`generate_python_sdk`: the generated client contains a
    ``NotebookAPIClient`` class with a method for each POST endpoint defined
    in the OpenAPI spec. Each method performs a ``fetch`` call to the
    corresponding endpoint and returns the parsed JSON response.
    """
    # Load OpenAPI schema
    schema = _load_openapi_schema(openapi_path)

    paths = schema.get("paths", {})
    method_names = _build_method_names(
        paths, TYPESCRIPT_RESERVED_CLIENT_METHOD_NAMES
    )
    wait_method_names = _build_wait_method_names(method_names, paths)
    # Prepare client code lines
    lines = []
    lines.append("export interface NotebookAPIClientOptions {")
    lines.append("  apiKey?: string;")
    lines.append("  timeoutMs?: number;")
    lines.append("  maxRetries?: number;")
    lines.append("  backoffFactor?: number;")
    lines.append("}")
    lines.append("")
    lines.append("export class NotebookAPIClient {")
    lines.append("  private baseUrl: string;")
    lines.append("  private apiKey: string;")
    lines.append("  private timeoutMs: number;")
    lines.append("  private maxRetries: number;")
    lines.append("  private backoffFactor: number;")
    lines.append("  private static readonly TRANSIENT_STATUSES = new Set([429, 502, 503, 504]);")
    lines.append("")
    lines.append(
        "  constructor(baseUrl: string, options: NotebookAPIClientOptions = {}) {"
    )
    lines.append('    this.baseUrl = baseUrl.replace(/\\/+$/, "");')
    # Generated endpoints require the same X-API-Key header the generated
    # app itself defaults to (see NOTEBOOK_API_KEY in api_generator.py) so
    # the client works out of the box locally, matching the Python client.
    lines.append(
        '    this.apiKey = options.apiKey ?? "notebook-to-api-dev-key";'
    )
    # fetch() has no default timeout of its own -- a call with no signal
    # can hang indefinitely on a server that accepts the connection but
    # never responds (a stalled deploy target, a network partition, ...),
    # with nothing on the client side to ever give up. waitForTask's own
    # timeoutMs bounds its polling *loop*, but each individual request it
    # (and every other method here) makes had no bound of its own until
    # now -- matches the Python client's identical `timeout` addition.
    lines.append("    this.timeoutMs = options.timeoutMs ?? 30000;")
    # Confirmed missing before this: only waitForTask's own polling loop
    # ever retried a transient failure -- every other call this client
    # makes (a per-function POST, listTasks/deleteTask/..., health/ready/
    # info/config/metrics/uptime/auth*) threw immediately on a single
    # 429/502/503/504 or network error, even though the generated server
    # side already reports exactly when a caller should retry
    # (X-RateLimit-Reset, Retry-After -- see _enforce_rate_limit in
    # api_generator.py). maxRetries: 0 disables this entirely, preserving
    # the previous immediate-throw behavior -- matches the Python
    # client's identical max_retries/backoff_factor addition.
    lines.append("    this.maxRetries = options.maxRetries ?? 3;")
    lines.append("    this.backoffFactor = options.backoffFactor ?? 0.5;")
    lines.append("  }")
    lines.append("")
    lines.append(
        "  private retryDelayMs(response: Response | null, "
        "attempt: number): number {"
    )
    lines.append("    if (response && response.headers) {")
    lines.append('      const retryAfter = response.headers.get("Retry-After");')
    lines.append("      if (retryAfter !== null) {")
    lines.append("        const seconds = Number(retryAfter);")
    lines.append("        if (!Number.isNaN(seconds)) {")
    lines.append("          return Math.max(0, seconds * 1000);")
    lines.append("        }")
    lines.append("      }")
    lines.append("    }")
    lines.append(
        "    return this.backoffFactor * 1000 * Math.pow(2, attempt);"
    )
    lines.append("  }")
    lines.append("")
    lines.append(
        "  private async requestWithRetry(path: string, "
        "fn: () => Promise<Response>): Promise<any> {"
    )
    lines.append("    let attempt = 0;")
    lines.append("    while (true) {")
    lines.append("      let response: Response;")
    lines.append("      try {")
    lines.append("        response = await fn();")
    lines.append("      } catch (err) {")
    lines.append("        if (attempt >= this.maxRetries) {")
    lines.append("          throw err;")
    lines.append("        }")
    lines.append(
        "        await this.sleep(this.retryDelayMs(null, attempt));"
    )
    lines.append("        attempt++;")
    lines.append("        continue;")
    lines.append("      }")
    lines.append("      if (!response.ok) {")
    lines.append(
        "        if ("
    )
    lines.append(
        "          NotebookAPIClient.TRANSIENT_STATUSES.has(response.status) "
        "&&"
    )
    lines.append("          attempt < this.maxRetries")
    lines.append("        ) {")
    lines.append(
        "          await this.sleep(this.retryDelayMs(response, "
        "attempt));"
    )
    lines.append("          attempt++;")
    lines.append("          continue;")
    lines.append("        }")
    # `.status` attached to the thrown Error (not just embedded in its
    # message) lets a caller distinguish e.g. a 401 from a 429 without
    # regex-parsing the message string -- mirrors getTask's own identical
    # `.status` attachment (added so waitForTask could tell a transient
    # failure from a real one), extended here to the shared retry helper
    # every generated method other than getTask/waitForTask routes
    # through (waitForTask already has its own transient-retry loop
    # around getTask -- see its own docstring above -- so neither is
    # touched here, avoiding two independent retry loops nested inside
    # one another).
    lines.append(
        "        const error: any = new Error(`Request to ${path} failed "
        "with status ${response.status}`);"
    )
    lines.append("        error.status = response.status;")
    lines.append("        throw error;")
    lines.append("      }")
    lines.append("      return response.json();")
    lines.append("    }")
    lines.append("  }")
    lines.append("")
    lines.append(
        "  private sleep(ms: number): Promise<void> {"
    )
    lines.append(
        "    return new Promise((resolve) => setTimeout(resolve, ms));"
    )
    lines.append("  }")
    lines.append("")
    # `callbackUrl` (optional -- only ever passed by a background
    # endpoint's own generated method below) becomes a "?callback_url="
    # query param on the request URL, via URLSearchParams the same way
    # listTasks already builds its own query string -- confirmed missing
    # before this feature: the generated server side has accepted this
    # exact query param on every background endpoint since it was added
    # (POSTing the finished task's own result there instead of requiring
    # the caller to poll getTask/waitForTask), and the OpenAPI schema
    # itself already documents it as a real parameter on that operation,
    # but neither generated client ever gained any way to actually reach
    # it, short of a caller bypassing this client entirely and building
    # the raw HTTP request by hand.
    lines.append(
        "  private async request(path: string, payload: unknown, "
        "callbackUrl?: string): Promise<any> {"
    )
    lines.append("    let url = path;")
    lines.append("    if (callbackUrl) {")
    lines.append(
        '      url = `${path}?${new URLSearchParams({ '
        'callback_url: callbackUrl }).toString()}`;'
    )
    lines.append("    }")
    lines.append(
        "    return this.requestWithRetry(url, () => fetch(`${this.baseUrl}${url}`, {"
    )
    lines.append('      method: "POST",')
    lines.append("      headers: {")
    lines.append('        "Content-Type": "application/json",')
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      body: JSON.stringify(payload),")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    }));")
    lines.append("  }")
    lines.append("")
    lines.append("  async getTask(taskId: string): Promise<any> {")
    lines.append(
        "    const response = await fetch(`${this.baseUrl}/tasks/${taskId}`, {"
    )
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    });")
    lines.append("    if (!response.ok) {")
    # `.status` attached to the thrown Error (not just embedded in its
    # message) is what lets waitForTask below tell a transient failure
    # (429/502/503/504) from a real one without re-parsing the message
    # string it throws.
    lines.append(
        "      const error: any = new Error(`Request to /tasks/${taskId} "
        "failed with status ${response.status}`);"
    )
    lines.append("      error.status = response.status;")
    lines.append("      throw error;")
    lines.append("    }")
    lines.append("    return response.json();")
    lines.append("  }")
    lines.append("")
    lines.append(
        "  async waitForTask(taskId: string, options: { pollIntervalMs?: "
        "number; timeoutMs?: number } = {}): Promise<any> {"
    )
    # A transient failure while polling -- a network-level error (fetch
    # itself throwing, e.g. a connection reset, or AbortSignal.timeout
    # firing on a single stalled request -- neither carries a `.status`
    # at all), or a 429/502/503/504 response -- exactly what
    # NOTEBOOK_API_RATE_LIMIT_PER_MINUTE or a rolling deploy would produce
    # mid-poll -- is retried the same as an ordinary still-processing
    # task, rather than aborting the whole wait outright. Any other error
    # (a genuine 401/404/...) still throws immediately. Mirrors
    # generate_python_sdk's identical wait_for_task fix.
    lines.append("    const pollIntervalMs = options.pollIntervalMs ?? 1000;")
    lines.append("    const timeoutMs = options.timeoutMs ?? 60000;")
    lines.append("    const deadline = Date.now() + timeoutMs;")
    lines.append("    const transientStatuses = new Set([429, 502, 503, 504]);")
    lines.append("    while (true) {")
    lines.append("      let task: any;")
    lines.append("      try {")
    lines.append("        task = await this.getTask(taskId);")
    lines.append("      } catch (err: any) {")
    lines.append(
        "        if (err.status !== undefined && "
        "!transientStatuses.has(err.status)) {"
    )
    lines.append("          throw err;")
    lines.append("        }")
    lines.append("        if (Date.now() >= deadline) {")
    lines.append(
        "          throw new Error(`Task ${taskId} did not complete "
        "within ${timeoutMs}ms`);"
    )
    lines.append("        }")
    lines.append(
        "        await new Promise((resolve) => setTimeout(resolve, "
        "pollIntervalMs));"
    )
    lines.append("        continue;")
    lines.append("      }")
    lines.append('      if (task.status !== "processing") {')
    lines.append("        return task;")
    lines.append("      }")
    lines.append("      if (Date.now() >= deadline) {")
    lines.append(
        "        throw new Error(`Task ${taskId} did not complete within "
        "${timeoutMs}ms`);"
    )
    lines.append("      }")
    lines.append(
        "      await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));"
    )
    lines.append("    }")
    lines.append("  }")
    lines.append("")
    # Mirrors generate_python_sdk's list_tasks/delete_task/
    # delete_completed_tasks/delete_failed_tasks: hardcoded rather than
    # derived from the per-path loop below (which only emits a method for
    # POST paths), since every compiled app guarantees these exact routes
    # (see RESERVED_INFRASTRUCTURE_NAMES in api_generator.py). Closes the
    # same gap for the rest of a background task's lifecycle that
    # waitForTask already closed for polling a single known task.
    # status/limit/offset mirror the generated server's own GET /tasks
    # query params (see list_tasks in api_generator.py) -- same gap as
    # generate_python_sdk's identical listTasks() addition: before this,
    # this method sent no query string at all no matter how it was
    # called, so filtering/paginating GET /tasks meant bypassing the
    # generated client entirely. Every option left undefined (the
    # default) preserves listTasks()'s previous behavior exactly.
    lines.append(
        "  async listTasks(options: { status?: string; limit?: number; "
        "offset?: number } = {}): Promise<any> {"
    )
    lines.append("    const params = new URLSearchParams();")
    lines.append(
        '    if (options.status !== undefined) params.set("status", '
        "options.status);"
    )
    lines.append(
        '    if (options.limit !== undefined) params.set("limit", '
        "String(options.limit));"
    )
    lines.append(
        '    if (options.offset !== undefined) params.set("offset", '
        "String(options.offset));"
    )
    lines.append("    const query = params.toString();")
    lines.append('    const path = `/tasks${query ? `?${query}` : ""}`;')
    lines.append(
        "    return this.requestWithRetry(path, () => fetch(`${this.baseUrl}${path}`, {"
    )
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    }));")
    lines.append("  }")
    lines.append("")
    lines.append("  async deleteTask(taskId: string): Promise<any> {")
    lines.append('    const path = `/tasks/${taskId}`;')
    lines.append(
        "    return this.requestWithRetry(path, () => fetch(`${this.baseUrl}${path}`, {"
    )
    lines.append('      method: "DELETE",')
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    }));")
    lines.append("  }")
    lines.append("")
    lines.append("  async deleteCompletedTasks(): Promise<any> {")
    lines.append(
        '    return this.requestWithRetry("/tasks/completed", () => '
        "fetch(`${this.baseUrl}/tasks/completed`, {"
    )
    lines.append('      method: "DELETE",')
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    }));")
    lines.append("  }")
    lines.append("")
    lines.append("  async deleteFailedTasks(): Promise<any> {")
    lines.append(
        '    return this.requestWithRetry("/tasks/failed", () => '
        "fetch(`${this.baseUrl}/tasks/failed`, {"
    )
    lines.append('      method: "DELETE",')
    lines.append("      headers: {")
    lines.append('        "X-API-Key": this.apiKey,')
    lines.append("      },")
    lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
    lines.append("    }));")
    lines.append("  }")
    # Mirrors generate_python_sdk's health/ready/info/config/metrics/
    # uptime/auth_status/auth_info/auth_validate above: hardcoded rather
    # than derived from the per-path loop below (which only emits a
    # method for POST paths), since every compiled app guarantees these
    # exact GET routes (see RESERVED_INFRASTRUCTURE_NAMES in
    # api_generator.py). "config" mirrors the identical gap just closed
    # on the Python client -- GET /config already existed server-side but
    # had no client method of its own here either.
    for infra_method_name, infra_path in (
        ("health", "/health"),
        ("ready", "/ready"),
        ("info", "/info"),
        ("config", "/config"),
        ("metrics", "/metrics"),
        ("uptime", "/uptime"),
        ("authStatus", "/auth/status"),
        ("authInfo", "/auth/info"),
        ("authValidate", "/auth/validate"),
    ):
        lines.append("")
        lines.append(f"  async {infra_method_name}(): Promise<any> {{")
        lines.append(
            f'    return this.requestWithRetry("{infra_path}", () => '
            f"fetch(`${{this.baseUrl}}{infra_path}`, {{"
        )
        lines.append("      headers: {")
        lines.append('        "X-API-Key": this.apiKey,')
        lines.append("      },")
        lines.append("      signal: AbortSignal.timeout(this.timeoutMs),")
        lines.append("    }));")
        lines.append("  }")
    # Collected separately from `lines` (the class body being built
    # above) and appended after the class closes, below -- TypeScript
    # type declarations are hoisted within a module, so declaration
    # order relative to the class doesn't matter, but keeping the class
    # itself uninterrupted (rather than threading interface declarations
    # in in between its own methods) keeps the class readable as one
    # block, matching how NotebookAPIClientOptions is already declared
    # once, up front, rather than repeated per-method.
    interface_lines = []

    for path, method_name in method_names.items():
        is_background = _is_background_path(paths[path])
        description = _operation_description(paths[path])
        # Confirmed missing before this feature: every generated
        # method's own payload parameter and return value were typed as
        # a bare Record<string, unknown>/any regardless of what the
        # notebook function actually expects or returns -- throwing away
        # the single biggest practical reason to generate a *TypeScript*
        # client over a plain JS one (compile-time type checking, IDE
        # autocomplete) for every function this tool compiles. Request
        # fields are typed straight from the real Pydantic-validated
        # JSON schema (_json_schema_type_to_typescript); the response
        # side has no such schema to read (api_generator.py's own
        # declared 200 response is deliberately {}), so it's typed from
        # "x-notebook-to-api-return-type" instead, an out-of-band
        # extension field generate_fastapi_code stamps onto the OpenAPI
        # operation for exactly this (see its own docstring).
        pascal_name = _pascal_case(method_name)
        request_interface = f"{pascal_name}Request"
        response_interface = f"{pascal_name}Response"
        return_type = (paths[path].get("post") or {}).get(
            "x-notebook-to-api-return-type"
        )

        interface_lines.extend(
            _typescript_request_interface(
                request_interface, _request_body_schema(schema, paths[path])
            )
        )
        interface_lines.append("")

        if is_background:
            interface_lines.extend(
                _typescript_task_submission_interface(response_interface)
            )
        else:
            interface_lines.extend(
                _typescript_response_interface(response_interface, return_type)
            )
        interface_lines.append("")

        lines.append("")
        if is_background:
            wait_name = wait_method_names[path]
            static_text_lines = [
                f"Enqueues `{path}` with JSON payload and returns",
                '{ task_id, status: "processing" } immediately -- not the '
                "real result.",
                f"Call getTask(taskId)/waitForTask(taskId) yourself, or "
                f"use {wait_name}(...)",
                "to submit and wait for the real result in one call.",
                "",
                "`callbackUrl`, if given, is POSTed the finished task's own",
                'record ({ task_id, status, result/error }) once it '
                "completes or fails --",
                "see the generated server's own ?callback_url= for what "
                "it actually delivers and when.",
            ]
        else:
            static_text_lines = [f"Calls the `{path}` endpoint with JSON payload."]
        lines.extend(_jsdoc_lines(description, static_text_lines))
        if is_background:
            lines.append(
                f"  async {method_name}(payload: {request_interface}, "
                f"callbackUrl?: string): Promise<{response_interface}> {{"
            )
            lines.append(
                f'    return this.request("{path}", payload, callbackUrl);'
            )
        else:
            lines.append(
                f"  async {method_name}(payload: {request_interface}): "
                f"Promise<{response_interface}> {{"
            )
            lines.append(f'    return this.request("{path}", payload);')
        lines.append("  }")

        if is_background:
            wait_name = wait_method_names[path]
            task_result_interface = f"{pascal_name}TaskResult"
            interface_lines.extend(
                _typescript_task_result_interface(task_result_interface, return_type)
            )
            interface_lines.append("")
            lines.append("")
            and_wait_static_text_lines = [
                f"Submits `{path}` and blocks until the background task "
                "finishes, returning its finished task record (see "
                "waitForTask).",
            ]
            lines.extend(_jsdoc_lines(description, and_wait_static_text_lines))
            lines.append(
                f"  async {wait_name}(payload: {request_interface}, "
                "callbackUrl?: string, "
                "options: { pollIntervalMs?: number; timeoutMs?: number } = "
                f"{{}}): Promise<{task_result_interface}> {{"
            )
            lines.append(
                f"    const submitted = await this.{method_name}(payload, callbackUrl);"
            )
            lines.append(
                "    return this.waitForTask(submitted.task_id, options);"
            )
            lines.append("  }")
    lines.append("}")
    lines.append("")
    lines.extend(interface_lines)
    # Write to file
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"TypeScript SDK generated at {output_path}")
