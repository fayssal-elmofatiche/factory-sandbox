from datetime import date

import pytest

from evently.registry import Event, RegistrationError, Registry


@pytest.fixture
def registry() -> Registry:
    r = Registry()
    r.add_event(Event("e1", "Workshop", date(2026, 12, 1), capacity=2))
    return r


def test_registers_until_full_then_waitlists(registry):
    assert registry.register("e1", "a@x.com") == "confirmed"
    assert registry.register("e1", "b@x.com") == "confirmed"
    assert registry.register("e1", "c@x.com") == "waitlisted"
    assert registry.event("e1").waitlist == ["c@x.com"]


def test_email_is_normalised_and_duplicates_refused(registry):
    registry.register("e1", "  A@X.com ")
    with pytest.raises(RegistrationError):
        registry.register("e1", "a@x.com")


def test_rejects_non_email(registry):
    with pytest.raises(RegistrationError):
        registry.register("e1", "not-an-email")


def test_cancel_promotes_first_on_waitlist(registry):
    for email in ["a@x.com", "b@x.com", "c@x.com", "d@x.com"]:
        registry.register("e1", email)
    assert registry.cancel("e1", "a@x.com") == "c@x.com"
    assert registry.event("e1").confirmed == ["b@x.com", "c@x.com"]
    assert registry.event("e1").waitlist == ["d@x.com"]


def test_cancel_from_waitlist_promotes_nobody(registry):
    for email in ["a@x.com", "b@x.com", "c@x.com"]:
        registry.register("e1", email)
    assert registry.cancel("e1", "c@x.com") is None
    assert registry.event("e1").waitlist == []


def test_unknown_event_and_bad_capacity():
    r = Registry()
    with pytest.raises(RegistrationError):
        r.event("nope")
    with pytest.raises(RegistrationError):
        r.add_event(Event("e0", "Empty", date(2026, 1, 1), capacity=0))


def test_events_sorted_by_day():
    r = Registry()
    r.add_event(Event("late", "Late", date(2026, 12, 1), capacity=5))
    r.add_event(Event("early", "Early", date(2026, 11, 1), capacity=5))
    assert [e.id for e in r.events()] == ["early", "late"]


@pytest.fixture
def full_registry(registry) -> Registry:
    for email in ["a@x.com", "b@x.com", "c@x.com", "d@x.com", "e@x.com"]:
        registry.register("e1", email)
    return registry


def test_waitlist_position_counts_from_one(full_registry):
    assert full_registry.waitlist_position("e1", "c@x.com") == 1
    assert full_registry.waitlist_position("e1", "d@x.com") == 2
    assert full_registry.waitlist_position("e1", "e@x.com") == 3


def test_waitlist_position_is_none_when_confirmed(full_registry):
    assert full_registry.waitlist_position("e1", "a@x.com") is None


def test_waitlist_position_refuses_unregistered_email(full_registry):
    with pytest.raises(RegistrationError, match="not registered"):
        full_registry.waitlist_position("e1", "nobody@x.com")


def test_waitlist_position_refuses_unknown_event(full_registry):
    with pytest.raises(RegistrationError):
        full_registry.waitlist_position("nope", "a@x.com")


def test_waitlist_position_normalises_email(full_registry):
    assert full_registry.waitlist_position("e1", "  C@X.com ") == 1


def test_waitlist_position_leaves_lists_unchanged(full_registry):
    event = full_registry.event("e1")
    confirmed, waitlist = list(event.confirmed), list(event.waitlist)
    full_registry.waitlist_position("e1", "d@x.com")
    full_registry.waitlist_position("e1", "a@x.com")
    assert event.confirmed == confirmed
    assert event.waitlist == waitlist


def test_waitlist_position_moves_up_after_promotion(full_registry):
    full_registry.cancel("e1", "a@x.com")
    assert full_registry.waitlist_position("e1", "c@x.com") is None
    assert full_registry.waitlist_position("e1", "d@x.com") == 1
    assert full_registry.waitlist_position("e1", "e@x.com") == 2


def test_waitlist_position_moves_up_after_waitlisted_cancel(full_registry):
    full_registry.cancel("e1", "c@x.com")
    assert full_registry.waitlist_position("e1", "d@x.com") == 1
    assert full_registry.waitlist_position("e1", "e@x.com") == 2
