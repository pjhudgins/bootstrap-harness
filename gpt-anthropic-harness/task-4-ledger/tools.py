"""Agent-facing schemas and Python dispatch; identity is never model supplied."""
import json

from filesystem import ReadOnlyFiles
from ledger import AGENT_AUTHOR, AGENT_TAGS, NIMOI
from policy import add_numbers

SERVER = "nimoi"


def schema(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


SCHEMAS = {
    "add": schema({"a": {"type": "number"}, "b": {"type": "number"}}, ("a", "b")),
    "fs_list": schema({"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}),
    "fs_read": schema({"path": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}}, ("path",)),
    "ledger_read": schema({"name": {"type": "string"}, "tag": {"type": "string"}, "offset": {"type": "integer"},
                           "limit": {"type": "integer"}, "body_offset": {"type": "integer"}}),
    "ledger_write": schema({"name": {"type": "string"}, "body": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string", "enum": sorted(AGENT_TAGS)}},
                            "prev": {"type": "string"}}, ("name", "body", "tags")),
}
DESCRIPTIONS = {
    "add": "Add two finite numbers using Python.",
    "fs_list": "List readable direct children of a NIMOI-relative directory. Sorted lexically; paginate with offset/limit (max 100). Protected paths omitted. No writes.",
    "fs_read": "Read NIMOI-relative UTF-8 text, up to 2MB per file and 30k characters per reply. Lines are 1-based; max_lines <=300. Follow next_line for remaining text. No writes or execution.",
    "ledger_read": "Read this chat's ledger. Without name, list entry names/ids/tags (optional tag filter, offset/limit <=50). With name, return current body/author/id/tags. Large bodies return a JSON-text slice; follow next_body_offset using body_offset.",
    "ledger_write": "Create or revise your ledger note under pilot/. Tags must be pilot.note, pilot.observation or pilot.question. For updates, first read and pass that current id as prev. Fixed author agent.claude.test-pilot. Cannot change harness entries, identity, protected tags, or delete history.",
}
FULL_NAMES = frozenset(f"mcp__{SERVER}__{name}" for name in SCHEMAS)


def validate(operation, args):
    if operation not in SCHEMAS or not isinstance(args, dict):
        raise ValueError("Unknown tool or malformed arguments.")
    spec = SCHEMAS[operation]
    if set(args) - set(spec["properties"]) or set(spec["required"]) - set(args):
        raise ValueError("Unexpected or missing tool arguments.")
    types = {"string": (str,), "integer": (int,), "number": (int, float), "array": (list,)}
    for key, value in args.items():
        if type(value) not in types[spec["properties"][key]["type"]]:
            raise ValueError("Tool argument type mismatch.")
    if operation == "add":
        add_numbers(args)


def allowed(name, args):
    if name not in FULL_NAMES:
        return False
    try:
        validate(name.split("__")[-1], args)
    except ValueError:
        return False
    return True


class ToolService:
    def __init__(self, log, root=NIMOI):
        self.log = log
        self.files = ReadOnlyFiles(root, log.clean)

    def invoke(self, operation, args):
        self.log.check()
        validate(operation, args)
        if operation == "add":
            return {"sum": add_numbers(args)}
        if operation == "fs_list":
            return self.files.list(**args)
        if operation == "fs_read":
            return self.files.read(**args)
        if operation == "ledger_read":
            return self.log.read(**args)
        return self.log.agent_write(**args)

    def build_server(self, state, get_turn):
        from claude_agent_sdk import create_sdk_mcp_server, tool
        registered = []
        for operation, input_schema in SCHEMAS.items():
            async def call(args, operation=operation):
                self.log.write("python_tool_start", turn=get_turn(), tool=operation, arguments=args)
                try:
                    result = self.log.clean(self.invoke(operation, args))
                except (ValueError, OSError) as exc:
                    # LedgerFailed is deliberately not caught or logged again here.
                    error = self.log.clean(str(exc))
                    self.log.write("python_tool_error", turn=get_turn(), tool=operation, error=error)
                    state.activity("Tool refused", summary=f"{operation}: {error}")
                    return {"content": [{"type": "text", "text": error}], "isError": True}
                self.log.write("python_tool_result", turn=get_turn(), tool=operation, result=result)
                if operation == "add":
                    summary = f"{args['a']} + {args['b']} = {result['sum']}"
                elif operation == "ledger_write":
                    summary = f"{result['name']} · {result['id']} · {AGENT_AUTHOR}"
                elif operation == "fs_read":
                    summary = f"{result['path']} · line {result['start_line']}"
                else:
                    summary = args.get("name", args.get("path", "current ledger"))
                state.activity(operation.replace("_", " "), summary=summary)
                return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
            registered.append(tool(operation, DESCRIPTIONS[operation], input_schema)(call))
        return create_sdk_mcp_server(name=SERVER, version="0.1.0", tools=registered)
