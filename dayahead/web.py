"""A tiny page of hourly day-ahead prices, served with the standard library.

Run: PORT=8000 python3 -m dayahead.web
"""

from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dayahead.orderbook import OrderBook
from dayahead.seed import sample_book

STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 40rem;
       padding: 0 1rem; color: #1d1f23; }
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.meta { color: #5d6168; font-size: 0.9rem; }
table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
th, td { padding: 0.4rem 1rem; border-bottom: 1px solid #d9dce1; text-align: right; }
th:first-child, td:first-child { text-align: left; }
th { font-weight: 600; }
"""


def hour_label(hour: int) -> str:
    return f"{hour:02d}–{hour + 1:02d}"


def render_prices(book: OrderBook) -> str:
    rows = []
    for c in book.results():
        if not c.bids and not c.offers:
            continue
        price = f"{c.price:.2f}" if c.price is not None else '<span class="meta">No match</span>'
        rows.append(
            f"<tr><td>{hour_label(c.hour)}</td><td>{price}</td><td>{c.volume_mwh:.1f}</td></tr>"
        )
    if rows:
        body = (
            "<table><thead><tr><th>Hour</th><th>Price (€/MWh)</th><th>Cleared (MWh)</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    else:
        body = "<p>No orders yet.</p>"
    area = html.escape(book.area)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Day-ahead prices</title><style>{STYLE}</style></head>"
        f"<body><h1>Day-ahead prices · {area}</h1>"
        f'<div class="meta">Delivery day {book.day.isoformat()}</div>{body}</body></html>'
    )


def make_handler(book: OrderBook) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            if self.path != "/":
                self.send_error(404)
                return
            page = render_prices(book).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    return Handler


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(sample_book()))
    print(f"dayahead on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
