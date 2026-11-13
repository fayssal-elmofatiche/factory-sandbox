"""A tiny page of hourly day-ahead prices, served with the standard library.

Run: PORT=8000 python3 -m dayahead.web
"""

from __future__ import annotations

import html
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dayahead.orderbook import Clearing, OrderBook
from dayahead.seed import sample_book

STYLE = """
:root { --ink: #1d1f23; --muted: #5d6168; --line: #d9dce1; --surface: #ffffff;
        --panel: #f5f6f8; --accent: #1f5fbf; --negative: #b3261e; }
@media (prefers-color-scheme: dark) {
  :root { --ink: #e6e8eb; --muted: #a1a6ad; --line: #33373d; --surface: #15171a;
          --panel: #1e2125; --accent: #7aa7ff; --negative: #ff8a80; }
}
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin: 0; background: var(--surface); color: var(--ink);
       line-height: 1.45; }
main { max-width: 44rem; margin: 0 auto; padding: 2rem 1rem 3rem; }
.eyebrow { margin: 0; color: var(--accent); font-size: 0.8rem; font-weight: 600;
           letter-spacing: 0.06em; text-transform: uppercase; }
h1 { font-size: 1.6rem; margin: 0.25rem 0; }
.meta { color: var(--muted); font-size: 0.9rem; margin: 0; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr)); gap: 0.75rem;
           margin: 1.5rem 0; padding: 0; list-style: none; }
.summary li { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
              padding: 0.75rem 1rem; }
.summary .label { display: block; color: var(--muted); font-size: 0.8rem; }
.summary .value { display: block; font-size: 1.25rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.table-wrap { border: 1px solid var(--line); border-radius: 8px; overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
caption { text-align: left; padding: 0.75rem 1rem; font-weight: 600; }
th, td { padding: 0.45rem 1rem; text-align: right; white-space: nowrap; }
thead th { background: var(--panel); color: var(--muted); font-size: 0.8rem;
           font-weight: 600; border-bottom: 1px solid var(--line); }
tbody tr + tr th, tbody tr + tr td { border-top: 1px solid var(--line); }
th:first-child { text-align: left; font-weight: 600; }
.unit { font-weight: 400; }
.negative { color: var(--negative); }
.nomatch { color: var(--muted); font-style: italic; }
.empty { background: var(--panel); border: 1px dashed var(--line); border-radius: 8px; padding: 1.5rem;
         margin-top: 1.5rem; color: var(--muted); text-align: center; }
footer { margin-top: 1rem; }
@media (max-width: 30rem) {
  h1 { font-size: 1.35rem; }
  table { font-size: 0.9rem; }
  th, td { padding: 0.45rem 0.4rem; }
  .summary { grid-template-columns: 1fr 1fr; }
  .summary li:last-child { grid-column: 1 / -1; }
  .summary .value { font-size: 1.1rem; }
}
"""


def hour_label(hour: int) -> str:
    return f"{hour:02d}–{hour + 1:02d}"


def mwh(volume: float) -> str:
    return f"{volume:,.1f}"


def price_cell(c: Clearing) -> str:
    if c.price is None:
        return '<td class="nomatch">No match</td>'
    css = ' class="negative"' if c.price < 0 else ""
    return f"<td{css}>{c.price:,.2f}</td>"


def summary(hours: list[Clearing]) -> str:
    cleared = [c for c in hours if c.price is not None]
    tiles = [
        ("Hours cleared", f"{len(cleared)} of {len(hours)}"),
        ("Cleared volume", f"{mwh(sum(c.volume_mwh for c in cleared))} MWh"),
        ("Orders", f"{sum(c.bids for c in hours)} bids · {sum(c.offers for c in hours)} offers"),
    ]
    items = "".join(
        f'<li><span class="label">{label}</span><span class="value">{value}</span></li>'
        for label, value in tiles
    )
    return f'<ul class="summary">{items}</ul>'


def render_prices(book: OrderBook) -> str:
    hours = [c for c in book.results() if c.bids or c.offers]
    if hours:
        rows = "".join(
            f'<tr><th scope="row">{hour_label(c.hour)}</th>{price_cell(c)}'
            f"<td>{mwh(c.volume_mwh)}</td><td>{c.bids}</td><td>{c.offers}</td></tr>"
            for c in hours
        )
        body = (
            summary(hours)
            + '<div class="table-wrap"><table><caption>Results by delivery hour</caption><thead><tr>'
            '<th scope="col">Hour</th><th scope="col">Price <span class="unit">€/MWh</span></th>'
            '<th scope="col">Cleared <span class="unit">MWh</span></th>'
            '<th scope="col">Bids</th><th scope="col">Offers</th>'
            f"</tr></thead><tbody>{rows}</tbody></table></div>"
        )
    else:
        body = '<p class="empty">No orders yet. Results appear here once bids and offers are in.</p>'
    area = html.escape(book.area)
    day = book.day
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Day-ahead prices · {area}</title><style>{STYLE}</style></head><body><main>"
        f'<header><p class="eyebrow">Day-ahead auction · {area}</p><h1>Hourly clearing prices</h1>'
        f'<p class="meta">Delivery day <time datetime="{day.isoformat()}">'
        f'{day.strftime("%A")} {day.day} {day.strftime("%B %Y")}</time></p></header>'
        f"{body}"
        '<footer><p class="meta">One uniform price per hour, set by merit order: the cheapest offers '
        "serve the dearest bids, and the last order needed, an offer or a bid, sets the price.</p></footer>"
        "</main></body></html>"
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
