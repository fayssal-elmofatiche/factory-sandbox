from datetime import date

from dayahead.orderbook import Order, OrderBook
from dayahead.web import render_prices


def book_with(*orders: Order, area: str = "DE-LU") -> OrderBook:
    book = OrderBook(area, date(2026, 11, 13))
    for order in orders:
        book.add_order(order)
    return book


def test_page_lists_hourly_price_volume_and_order_counts():
    book = book_with(Order("o", "a", "offer", 7, 100, 42.5), Order("b", "b", "bid", 7, 80, 60.0))
    page = render_prices(book)
    assert '<th scope="row">07–08</th><td>42.50</td><td>80.0</td><td>1</td><td>1</td>' in page


def test_header_names_area_and_delivery_day():
    page = render_prices(book_with(Order("o", "a", "offer", 7, 100, 42.5)))
    assert "Day-ahead auction · DE-LU" in page
    assert '<time datetime="2026-11-13">Friday 13 November 2026</time>' in page


def test_summary_counts_cleared_hours_volume_and_orders():
    book = book_with(
        Order("o7", "a", "offer", 7, 1000, 42.5),
        Order("b7", "b", "bid", 7, 1200, 60.0),
        Order("o8", "a", "offer", 8, 100, 60.0),
        Order("b8", "b", "bid", 8, 100, 40.0),
    )
    page = render_prices(book)
    assert '<span class="label">Hours cleared</span><span class="value">1 of 2</span>' in page
    assert '<span class="value">1,000.0 MWh</span>' in page
    assert '<span class="value">2 bids · 2 offers</span>' in page


def test_hours_without_orders_are_left_out():
    page = render_prices(book_with(Order("o", "a", "offer", 7, 100, 42.5)))
    assert "07–08" in page
    assert "08–09" not in page


def test_unmatched_hour_says_no_match():
    book = book_with(Order("o", "a", "offer", 7, 100, 60.0), Order("b", "b", "bid", 7, 80, 40.0))
    assert '<td class="nomatch">No match</td>' in render_prices(book)


def test_negative_price_is_marked():
    book = book_with(Order("o", "a", "offer", 7, 100, -12.0), Order("b", "b", "bid", 7, 80, 10.0))
    assert '<td class="negative">-12.00</td>' in render_prices(book)


def test_area_is_escaped():
    page = render_prices(book_with(area="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_participants_never_reach_the_page():
    page = render_prices(book_with(Order("o", "Secret Trader", "offer", 7, 100, 42.5)))
    assert "secret trader" not in page.lower()


def test_empty_state():
    page = render_prices(book_with())
    assert "No orders yet." in page
    assert "<table" not in page
