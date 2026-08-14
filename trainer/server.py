"""The tactics trainer web app (M7) — stdlib HTTP server, zero engine work
at request time beyond reconstructing the stored position.

Usage:
  uv run python -m trainer.server [--puzzles data/puzzles_v5.jsonl]
                                  [--state data/trainer_state.json]
                                  [--port 8321]
then open http://localhost:8321
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .play import PlayService
from .service import TrainerService

_STATIC = Path(__file__).parent / "static"


def make_handler(service: TrainerService, play: PlayService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet
            pass

        def _json(self, obj, status=200):
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                body = (_STATIC / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/next":
                self._json(service.next_puzzle())
            elif self.path == "/api/play/new":
                self._json(play.new_game())
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            try:
                req = json.loads(self.rfile.read(length))
                if self.path == "/api/submit":
                    road = req.get("road_codec_id")
                    result = service.submit(
                        req["puzzle_id"], int(req["codec_id"]),
                        int(road) if road is not None else None,
                    )
                elif self.path == "/api/play/act":
                    result = play.act(
                        req["session"],
                        codec_id=req.get("codec_id"),
                        discard=req.get("discard"),
                    )
                else:
                    self._json({"error": "not found"}, 404)
                    return
                self._json(result)
            except KeyError as exc:
                self._json({"error": f"bad request: {exc}"}, 400)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": repr(exc)}, 500)

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--puzzles", default="data/puzzles_v5.jsonl")
    ap.add_argument("--state", default="data/trainer_state.json")
    ap.add_argument("--port", type=int, default=8321)
    ap.add_argument("--bot", default="checkpoints/gen7.pt",
                    help="checkpoint for play-vs-bot mode")
    ap.add_argument("--bot-sims", type=int, default=160)
    args = ap.parse_args()

    service = TrainerService(args.puzzles, args.state)
    play = PlayService(net_path=args.bot, sims=args.bot_sims)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(service, play))
    print(f"catan tactics trainer: {len(service.puzzles)} puzzles loaded")
    print(f"play mode bot: {args.bot} (s={args.bot_sims})")
    print(f"open http://localhost:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
