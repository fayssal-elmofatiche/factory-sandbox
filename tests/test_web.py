from datetime import date

from dayahead.orderbook import Order, OrderBook
from dayahead.web import render_prices


def book_with(*orders: Order, area: str = "DE-LU") -> OrderBook:
    book = OrderBook(area, date(2026, 11, 13))
    for order in orders:
        book.add_order(order)
    return book


def test_page_lists_hourly_price_and_volume():
    book = book_with(Order("o", "a", "offer", 7, 100, 42.5), Order("b", "b", "bid", 7, 80, 60.0))
    page = render_prices(book)
    assert "<td>07–08</td><td>42.50</td><td>80.0</td>" in page
    assert "2026-11-13" in page and "DE-LU" in page


def test_hours_without_orders_are_left_out():
    page = render_prices(book_with(Order("o", "a", "offer", 7, 100, 42.5)))
    assert "07–08" in page
    assert "08–09" not in page


def test_unmatched_hour_says_no_match():
    book = book_with(Order("o", "a", "offer", 7, 100, 60.0), Order("b", "b", "bid", 7, 80, 40.0))
    assert "No match" in render_prices(book)


def test_area_is_escaped():
    page = render_prices(book_with(area="<script>alert(1)</script>"))
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_participants_never_reach_the_page():
    page = render_prices(book_with(Order("o", "Secret Trader", "offer", 7, 100, 42.5)))
    assert "secret trader" not in page.lower()


def test_empty_state():
    assert "No orders yet." in render_prices(book_with())
