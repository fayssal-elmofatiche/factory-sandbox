from datetime import date

from evently.registry import Event, Registry
from evently.web import render_events


def test_page_lists_events_with_counts():
    r = Registry()
    r.add_event(Event("e1", "Workshop", date(2026, 12, 1), capacity=10))
    r.register("e1", "a@x.com")
    page = render_events(r)
    assert "Workshop" in page
    assert "1 of 10 registered" in page


def test_titles_are_escaped():
    r = Registry()
    r.add_event(Event("e1", "<script>alert(1)</script>", date(2026, 12, 1), capacity=1))
    page = render_events(r)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_empty_state():
    assert "No events yet." in render_events(Registry())
