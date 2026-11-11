"""Sample data for the dev server."""

from datetime import date

from evently.registry import Event, Registry


def sample_registry() -> Registry:
    registry = Registry()
    registry.add_event(Event("rust-workshop", "Rust for Finance workshop", date(2026, 12, 3), capacity=2))
    registry.add_event(Event("ai-evening", "AI in Finance evening", date(2026, 11, 25), capacity=70))
    for email in ["ada@example.com", "grace@example.com", "linus@example.com"]:
        registry.register("rust-workshop", email)
    registry.register("ai-evening", "alan@example.com")
    return registry
