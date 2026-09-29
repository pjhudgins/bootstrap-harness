"""SDK message normalization; storage remains independent of SDK types."""
from ledger import AGENT_AUTHOR


def record_sdk_message(log, turn, direction, message, agent_author=AGENT_AUTHOR):
    """Extract display text; retain SDK/tool/usage structure as harness evidence."""
    with log.lock:
        log.check()
        record = log.clean(message)
        refs = []
        known = log.turn_text.setdefault(turn, {})
        kind = record.get("_type") if isinstance(record, dict) else None
        if kind == "AssistantMessage":
            # SDK authentication/error text is runtime-authored, not model prose.
            role = "runtime" if record.get("error") else "assistant"
            for block in record.get("content", []):
                if block.get("_type") == "TextBlock":
                    text = block["text"]
                    ref = log._message_text(text, role, agent_author)
                    known[(role, text)] = ref
                    block["text"] = ref["link"]
                    refs.append(ref)
        elif kind == "UserMessage":
            # SDK UserMessage also carries tool results; do not label those human.
            content = record.get("content")
            if isinstance(content, str) and ("user", content) in known:
                ref = known[("user", content)]
                record["content"] = ref["link"]
                refs.append(ref)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("_type") == "TextBlock":
                        ref = known.get(("user", block.get("text")))
                        if ref:
                            block["text"] = ref["link"]
                            refs.append(ref)
        elif kind == "ResultMessage" and isinstance(record.get("result"), str):
            # Reuse the emitted text when the SDK repeats it in its final result.
            text = record["result"]
            ref = known.get(("assistant", text)) or known.get(("runtime", text))
            if ref:
                record["result"] = ref["link"]
                refs.append(ref)
        log.write("message", turn=turn, actor=agent_author, direction=direction, message=record, text_entries=refs)
        if kind == "ResultMessage":
            log.turn_text.pop(turn, None)

