"""A tiny events page served with the standard library.

Run: PORT=8000 python3 -m evently.web
"""

from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from evently.registry import Registry
from evently.seed import sample_registry

STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 40rem;
       padding: 0 1rem; color: #1d1f23; }
h1 { font-size: 1.5rem; }
ul { list-style: none; padding: 0; }
li { border: 1px solid #d9dce1; border-radius: 8px; padding: 0.75rem 1rem; margin-bottom: 0.75rem; }
.title { font-weight: 600; }
.meta { color: #5d6168; font-size: 0.9rem; }
"""


def render_events(registry: Registry) -> str:
    items = []
    for event in registry.events():
        items.append(
            "<li>"
            f'<div class="title">{html.escape(event.title)}</div>'
            f'<div class="meta">{event.day.isoformat()} · '
            f"{event.seats_taken} of {event.capacity} registered</div>"
            "</li>"
        )
    body = "\n".join(items) or "<li>No events yet.</li>"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Events</title><style>{STYLE}</style></head>"
        f"<body><h1>Upcoming events</h1><ul>{body}</ul></body></html>"
    )


def make_handler(registry: Registry) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            if self.path != "/":
                self.send_error(404)
                return
            page = render_events(registry).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    return Handler


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(sample_registry()))
    print(f"evently on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
