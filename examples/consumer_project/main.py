"""Independent consumer: multi-turn chat + stream + OpenAI-compatible call via the SDK."""

from __future__ import annotations

import os
import sys


def check_r1_core_name_collision() -> None:
    """R1: the ``core`` package must be the upstream one, not a stray local dir."""
    import core

    path = os.path.dirname(core.__file__)
    assert os.path.isfile(os.path.join(path, "contracts", "errors.py")), f"wrong 'core' at {path}"
    print(f"[R1] core resolved to upstream package: {path}")


def main() -> int:
    from chat_engine_client import ChatEngine

    base = os.environ.get("CHAT_ENGINE_URL", "http://localhost:8080")
    with ChatEngine(base) as ce:
        h = ce.health()
        print(
            "[health]", h["status"], "upstream", h["upstream_sha"][:7], "providers", h["providers"]
        )
        proj = ce.create_project("consumer demo", system_instructions="Answer briefly.")
        r1 = ce.chat("What is chat_engine?", project_id=proj["id"])
        print("[turn 1]", r1.provider_key, r1.model_key, "->", r1.text[:80])
        r2 = ce.chat("Say that again in one line.", conversation_id=r1.conversation_id)
        print("[turn 2]", r2.turn.seq, "->", r2.text[:80])
        print("[stream]", end=" ")
        for ev in ce.chat_stream("Stream a short reply.", conversation_id=r1.conversation_id):
            if ev.type == "message_delta":
                print(ev.delta, end="", flush=True)
            elif ev.terminal:
                print(f"  <{ev.type}>")
        turns = ce.turns(r1.conversation_id)
        print("[turns]", len(turns), [t["status"] for t in turns])
        c = ce.completions("local-echo-1", [{"role": "user", "content": "ping"}])
        print("[openai-compat]", c.id, c.finish_reason, (c.content or "")[:60])
    return 0


if __name__ == "__main__":
    check_r1_core_name_collision()
    sys.exit(main())
