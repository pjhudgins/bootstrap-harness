"""Small shared schema/argument helpers; independent of transports and capabilities."""


def args_only(arguments, allowed, required=()):
    if not isinstance(arguments, dict) or set(arguments) - set(allowed) or set(required) - set(arguments):
        raise ValueError("Unexpected or missing tool arguments.")
    return arguments


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer from {low} to {high}.")
    return value


def tool(name, description, properties, required, handler):
    return ({"type": "function", "name": name, "description": description,
             "inputSchema": {"type": "object", "properties": properties,
                             "required": required, "additionalProperties": False}}, handler)
