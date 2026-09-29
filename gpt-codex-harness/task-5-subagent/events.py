"""Translate Codex wire messages once; consumers see small semantic events."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    kind: str
    data: dict
    actor: str = 'main'


def normalize(kind, data, actor):
    if kind in {'user_message', 'python_tool', 'restriction_caveat', 'rate_limits_unavailable'}:
        return Event(kind, data, actor)
    if kind in {'rate_limits', 'rate_limits_before', 'rate_limits_after'}:
        return Event('rate_limits', data, actor)
    if kind not in {'receive', 'shutdown_receive'} or not isinstance(data, dict):
        return None
    params = data.get('params') or {}
    method = data.get('method')
    if method == 'item/agentMessage/delta':
        return Event('message_delta', {'id': params['itemId'], 'text': params['delta']}, actor)
    if method == 'item/completed' and params.get('item', {}).get('type') == 'agentMessage':
        item = params['item']
        return Event('message_completed', {'id': item['id'], 'text': item['text'],
                     'phase': item.get('phase'), 'complete': True}, actor)
    if method == 'thread/tokenUsage/updated':
        return Event('usage', params['tokenUsage'], actor)
    if method == 'account/rateLimits/updated':
        return Event('rate_limits', params, actor)
    return None


class MessageRecorder:
    """Replace known protocol text slots with references; never inspect tool arguments."""
    def __init__(self, store):
        self.store = store
        self.turn_users = {}

    def transform(self, kind, data, human_id, actor="main", agent_author=None, instruction=None):
        """Transform known message slots only, never arbitrary tool arguments."""
        if not isinstance(data, dict):
            return
        if kind == "user_message":
            ref = self.store._text(data["text"], "human", ("ui", data["id"]))
            self.store._message(ref, "human", actor=actor, source=kind, message_id=data["id"])
            data["text"] = ref["text"]
            return
        if kind not in ("send", "receive", "shutdown_receive"):
            return
        method = data.get("method")
        params = data.get("params") or {}
        thread_id = params.get("threadId")
        turn_id = params.get("turnId")
        turn = params.get("turn") or (data.get("result") or {}).get("turn")
        if isinstance(turn, dict):
            turn_id = turn.get("id", turn_id)
            if human_id is not None:
                self.turn_users.setdefault((actor, thread_id, turn_id), human_id)
        human_id = self.turn_users.get((actor, thread_id, turn_id), human_id)

        def human_text(text, fallback):
            if instruction is not None:
                if text != instruction["body"]:
                    raise ValueError("Child instruction echo differs from its pinned body.")
                return {"text": f"[[{instruction['name']}]]", "text_id": instruction["id"], "author": instruction["author"]}
            key = ("ui", human_id) if human_id is not None else fallback
            return self.store._text(text, "human", key)

        def item_links(item, complete):
            if not isinstance(item, dict):
                return
            item_id = item.get("id")
            # This journal belongs to one thread. RPC snapshots may omit its
            # thread id; the turn/item identity still denotes the same message.
            key = (actor, "item", turn_id, item_id)
            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                if item["text"] or complete:
                    ref = self.store._text(item["text"], "agent", key, author=agent_author)
                    if complete:
                        self.store._message(ref, "agent", actor=actor, source=method or "rpc-result", item_id=item_id,
                                      thread_id=thread_id, turn_id=turn_id, phase=item.get("phase"))
                    item["text"] = ref["text"]
            elif item.get("type") == "userMessage":
                for index, content in enumerate(item.get("content", [])):
                    if content.get("type") == "text" and isinstance(content.get("text"), str):
                        ref = human_text(content["text"], (*key, index))
                        if complete:
                            self.store._message(ref, "instruction" if instruction else "human", actor=actor, source=method or "rpc-result", item_id=item_id,
                                          thread_id=thread_id, turn_id=turn_id)
                        content["text"] = ref["text"]

        if kind == "send" and method == "turn/start":
            for index, content in enumerate(params.get("input", [])):
                if content.get("type") == "text" and isinstance(content.get("text"), str):
                    ref = human_text(content["text"], ("request", data.get("id"), index))
                    content["text"] = ref["text"]
        elif kind in ("receive", "shutdown_receive"):
            if method == "item/agentMessage/delta" and isinstance(params.get("delta"), str):
                # Keep exact fragments even if the turn ends without a completed item.
                params["delta"] = self.store.fragment(params, actor, agent_author)
            if method in ("item/started", "item/completed"):
                item_links(params.get("item"), method == "item/completed")
            if method == 'rawResponseItem/completed':
                item = params.get('item') or {}
                if item.get('type') == 'message' and item.get('role') == 'assistant':
                    blocks = [b for b in item.get('content', [])
                              if b.get('type') in ('output_text', 'text') and isinstance(b.get('text'), str)]
                    for index, block in enumerate(blocks):
                        key = (actor, 'item', turn_id, item.get('id'))
                        if len(blocks) != 1:
                            key += (index,)
                        block['text'] = self.store._text(block['text'], 'agent', key, author=agent_author)['text']
            if isinstance(turn, dict):
                for item in turn.get("items", []):
                    item_links(item, turn.get("status") == "completed")

