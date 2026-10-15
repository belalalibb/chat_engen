"""``python -m chat_engine serve`` — the ONE process entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chat-engine")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="serve the API")
    serve.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    sub.add_parser("describe", help="print composition facts (no secrets)")
    args = parser.parse_args(argv)

    from chat_engine.api.app import create_app

    app = create_app()
    rt = app.state.runtime
    if args.cmd == "describe":
        print(json.dumps({"upstream_sha": rt.upstream_sha, "providers": list(rt.provider_keys), "models": [m.model_key for m in rt.models.active_models()]}, indent=1))
        return 0
    import uvicorn

    print(json.dumps({"event": "chat_engine_started", "profile": rt.profile, "providers": list(rt.provider_keys), "upstream_sha": rt.upstream_sha[:7]}), flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
