"""Sample data for the dev server."""

from datetime import date

from evently.registry import Event, Registry


def sample_registry() -> Registry:
    registry = Registry()
    registry.add_event(Event("intro-workshop", "Intro to testing workshop", date(2026, 12, 3), capacity=2))
    registry.add_event(Event("meetup", "Community meetup", date(2026, 11, 25), capacity=40))
    for email in ["ada@example.com", "grace@example.com", "linus@example.com"]:
        registry.register("intro-workshop", email)
    registry.register("meetup", "alan@example.com")
    return registry
