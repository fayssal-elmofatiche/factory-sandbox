"""A day-ahead power order book: bids and offers per delivery hour, cleared by merit order."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

SIDES = ("bid", "offer")
PRICE_FLOOR = -500.0  # EUR/MWh, the lowest limit price an order may carry
PRICE_CAP = 4000.0  # EUR/MWh, the highest


@dataclass(frozen=True)
class Order:
    id: str
    participant: str
    side: str  # "bid" buys power, "offer" sells it
    hour: int  # delivery hour, 0 = 00:00-01:00 ... 23 = 23:00-24:00
    volume_mwh: float
    price: float  # limit price in EUR/MWh


@dataclass(frozen=True)
class Clearing:
    hour: int
    price: float | None  # None when no bid and offer match
    volume_mwh: float
    bids: int
    offers: int


class OrderError(ValueError):
    pass


class OrderBook:
    def __init__(self, area: str, day: date) -> None:
        self.area = area
        self.day = day
        self._orders: dict[str, Order] = {}

    def add_order(self, order: Order) -> Order:
        """Validate and store an order. Returns it with the participant normalised."""
        participant = order.participant.strip().lower()
        if not order.id:
            raise OrderError("order id must not be empty")
        if order.id in self._orders:
            raise OrderError(f"order {order.id!r} already exists")
        if not participant:
            raise OrderError("participant must not be empty")
        if order.side not in SIDES:
            raise OrderError(f"side must be 'bid' or 'offer', not {order.side!r}")
        if isinstance(order.hour, bool) or not isinstance(order.hour, int) or not 0 <= order.hour <= 23:
            raise OrderError(f"hour must be 0 to 23, not {order.hour!r}")
        if not order.volume_mwh > 0:
            raise OrderError("volume must be more than 0 MWh")
        if not PRICE_FLOOR <= order.price <= PRICE_CAP:
            raise OrderError(f"price must be between {PRICE_FLOOR:g} and {PRICE_CAP:g} EUR/MWh")
        stored = replace(order, participant=participant)
        self._orders[order.id] = stored
        return stored

    def order(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise OrderError(f"unknown order {order_id!r}") from None

    def cancel(self, order_id: str) -> Order:
        """Withdraw an order before the auction. Returns the withdrawn order."""
        order = self.order(order_id)
        del self._orders[order_id]
        return order

    def orders(self, hour: int) -> list[Order]:
        """The orders for one delivery hour, in the order they were placed."""
        return [o for o in self._orders.values() if o.hour == hour]

    def clear(self, hour: int) -> Clearing:
        """Clear one hour by merit order.

        Offers are stacked cheapest first and bids dearest first. The cleared
        volume is the most that trades at any single price, and the clearing
        price is the lowest price at which that volume trades.
        """
        orders = self.orders(hour)
        bids = [o for o in orders if o.side == "bid"]
        offers = [o for o in orders if o.side == "offer"]
        best_price: float | None = None
        best_volume = 0.0
        for price in sorted({o.price for o in orders}):
            demand = sum(o.volume_mwh for o in bids if o.price >= price)
            supply = sum(o.volume_mwh for o in offers if o.price <= price)
            volume = min(demand, supply)
            if volume > best_volume:
                best_price, best_volume = price, volume
        return Clearing(hour, best_price, best_volume, len(bids), len(offers))

    def results(self) -> list[Clearing]:
        """The clearing of every delivery hour of the day, 0 to 23."""
        return [self.clear(hour) for hour in range(24)]

    def merit_order_rank(self, order_id: str) -> int | None:
        """Return an offer's 1-based place in its hour's merit order, or None for a bid.

        1 is the cheapest offer. Offers at the same price keep the order they were placed in.
        """
        order = self.order(order_id)
        if order.side == "bid":
            return None
        offers = sorted((o for o in self.orders(order.hour) if o.side == "offer"), key=lambda o: o.price)
        return [o.id for o in offers].index(order_id) + 1
