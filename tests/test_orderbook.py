from datetime import date

import pytest

from dayahead.orderbook import Clearing, Order, OrderBook, OrderError
from dayahead.seed import sample_book


@pytest.fixture
def book() -> OrderBook:
    return OrderBook("DE-LU", date(2026, 11, 13))


def add(book: OrderBook, order_id: str, side: str, volume: float, price: float, hour: int = 8) -> Order:
    return book.add_order(Order(order_id, "Trader", side, hour, volume, price))


def test_add_order_normalises_participant(book):
    stored = book.add_order(Order("o1", "  Rhein GAS ", "offer", 8, 100, 50.0))
    assert stored.participant == "rhein gas"
    assert book.order("o1").participant == "rhein gas"


@pytest.mark.parametrize(
    "order",
    [
        Order("", "t", "bid", 8, 10, 50.0),
        Order("o1", "   ", "bid", 8, 10, 50.0),
        Order("o1", "t", "buy", 8, 10, 50.0),
        Order("o1", "t", "bid", 24, 10, 50.0),
        Order("o1", "t", "bid", -1, 10, 50.0),
        Order("o1", "t", "bid", True, 10, 50.0),
        Order("o1", "t", "bid", 8, 0, 50.0),
        Order("o1", "t", "bid", 8, -5, 50.0),
        Order("o1", "t", "bid", 8, 10, -500.01),
        Order("o1", "t", "bid", 8, 10, 4000.01),
    ],
)
def test_add_order_rejects_invalid_orders(book, order):
    with pytest.raises(OrderError):
        book.add_order(order)
    assert book.orders(8) == []


def test_price_limits_are_inclusive(book):
    add(book, "floor", "offer", 10, -500.0)
    add(book, "cap", "bid", 10, 4000.0)
    assert len(book.orders(8)) == 2


def test_duplicate_id_refused(book):
    add(book, "o1", "bid", 10, 50.0)
    with pytest.raises(OrderError, match="already exists"):
        add(book, "o1", "offer", 10, 50.0)


def test_unknown_order(book):
    with pytest.raises(OrderError, match="unknown order"):
        book.order("nope")
    with pytest.raises(OrderError):
        book.cancel("nope")


def test_cancel_removes_order(book):
    add(book, "o1", "bid", 10, 50.0)
    assert book.cancel("o1").id == "o1"
    assert book.orders(8) == []


def test_orders_are_per_hour_in_placement_order(book):
    add(book, "b", "bid", 10, 50.0)
    add(book, "a", "offer", 10, 20.0)
    add(book, "other-hour", "bid", 10, 50.0, hour=9)
    assert [o.id for o in book.orders(8)] == ["b", "a"]


def test_marginal_offer_sets_the_price(book):
    add(book, "cheap", "offer", 100, 20.0)
    add(book, "dear", "offer", 100, 50.0)
    add(book, "demand", "bid", 150, 80.0)
    c = book.clear(8)
    assert (c.price, c.volume_mwh, c.bids, c.offers) == (50.0, 150, 1, 2)


def test_marginal_bid_sets_the_price_when_supply_runs_out(book):
    add(book, "supply", "offer", 100, 20.0)
    add(book, "low", "bid", 100, 30.0)
    add(book, "high", "bid", 50, 80.0)
    c = book.clear(8)
    assert (c.price, c.volume_mwh) == (30.0, 100)  # "low" is cut to 50 MWh, so it sets the price


def test_expensive_offer_drops_out_when_bids_stop(book):
    add(book, "cheap", "offer", 100, 20.0)
    add(book, "dear", "offer", 100, 90.0)
    add(book, "demand", "bid", 300, 60.0)
    c = book.clear(8)
    assert (c.price, c.volume_mwh) == (60.0, 100)


def test_equal_supply_and_demand_clear_at_the_offer_price(book):
    add(book, "supply", "offer", 100, 20.0)
    add(book, "demand", "bid", 100, 80.0)
    c = book.clear(8)
    assert (c.price, c.volume_mwh) == (20.0, 100)


def test_only_orders_at_the_clearing_price_are_cut():
    """Bids above the price and offers below it trade in full, in every hour of the sample day."""
    book = sample_book()
    for c in book.results():
        orders = book.orders(c.hour)
        bids_above = sum(o.volume_mwh for o in orders if o.side == "bid" and o.price > c.price)
        offers_below = sum(o.volume_mwh for o in orders if o.side == "offer" and o.price < c.price)
        bids_at = sum(o.volume_mwh for o in orders if o.side == "bid" and o.price == c.price)
        offers_at = sum(o.volume_mwh for o in orders if o.side == "offer" and o.price == c.price)
        assert bids_above <= c.volume_mwh <= bids_above + bids_at
        assert offers_below <= c.volume_mwh <= offers_below + offers_at


def test_negative_prices_clear(book):
    add(book, "wind", "offer", 100, -10.0)
    add(book, "demand", "bid", 80, -5.0)
    c = book.clear(8)
    assert (c.price, c.volume_mwh) == (-10.0, 80)


def test_no_match_when_bids_are_below_offers(book):
    add(book, "offer", "offer", 100, 60.0)
    add(book, "bid", "bid", 100, 40.0)
    c = book.clear(8)
    assert (c.price, c.volume_mwh) == (None, 0.0)


def test_one_sided_and_empty_hours_do_not_clear(book):
    add(book, "bid", "bid", 100, 40.0)
    assert book.clear(8) == Clearing(8, None, 0.0, 1, 0)
    assert book.clear(9).price is None and book.clear(9).bids == 0


def test_cancelled_order_no_longer_clears(book):
    add(book, "offer", "offer", 100, 20.0)
    add(book, "bid", "bid", 100, 40.0)
    book.cancel("bid")
    assert book.clear(8).price is None


def test_results_cover_every_hour(book):
    add(book, "offer", "offer", 100, 20.0, hour=23)
    add(book, "bid", "bid", 100, 40.0, hour=23)
    results = book.results()
    assert [c.hour for c in results] == list(range(24))
    assert results[23].price == 20.0


def test_merit_order_rank_counts_from_cheapest(book):
    add(book, "dear", "offer", 10, 90.0)
    add(book, "cheap", "offer", 10, 10.0)
    add(book, "mid", "offer", 10, 50.0)
    add(book, "other-hour", "offer", 10, 1.0, hour=9)
    assert [book.merit_order_rank(i) for i in ["cheap", "mid", "dear"]] == [1, 2, 3]
    assert book.merit_order_rank("other-hour") == 1


def test_merit_order_rank_keeps_placement_order_on_equal_prices(book):
    add(book, "first", "offer", 10, 50.0)
    add(book, "second", "offer", 10, 50.0)
    assert book.merit_order_rank("first") == 1
    assert book.merit_order_rank("second") == 2


def test_merit_order_rank_is_none_for_bids_and_refuses_unknown(book):
    add(book, "bid", "bid", 10, 50.0)
    assert book.merit_order_rank("bid") is None
    with pytest.raises(OrderError):
        book.merit_order_rank("nope")


def test_sample_book_clears_every_hour():
    results = sample_book().results()
    assert all(c.price is not None and c.volume_mwh > 0 for c in results)
    assert results[3].price == 80.0  # night: wind and lignite sell out, the smelter's bid is marginal
    assert results[18].price == 180.0  # evening peak: the peaker runs
