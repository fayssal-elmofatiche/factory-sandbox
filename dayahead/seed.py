"""Sample data for the dev server: one day of bids and offers in one bidding zone."""

from datetime import date

from dayahead.orderbook import Order, OrderBook

# What the city utility buys per delivery hour, in MWh: low at night, peaks in the morning and evening.
LOAD = [420, 400, 390, 385, 390, 420, 500, 580, 620, 610, 600, 590,
        580, 570, 570, 580, 610, 660, 690, 670, 610, 540, 480, 440]


def sample_book() -> OrderBook:
    book = OrderBook("DE-LU", date(2026, 11, 13))
    for hour, load in enumerate(LOAD):
        orders = [
            ("wind", "Nordwind GmbH", "offer", 250, -5.0),
            ("lignite", "Revier Kraftwerke", "offer", 200, 62.0),
            ("ccgt", "Rhein Gas", "offer", 150, 95.0),
            ("peaker", "Rhein Gas", "offer", 120, 180.0),
            ("city", "Stadtwerke Musterstadt", "bid", load, 500.0),
            ("smelter", "Alu Werk", "bid", 90, 80.0),
        ]
        if 8 <= hour <= 16:
            orders.append(("solar", "Sonnenfeld Solar", "offer", 180, 0.0))
        for name, participant, side, volume, price in orders:
            book.add_order(Order(f"{name}-{hour:02d}", participant, side, hour, volume, price))
    return book
