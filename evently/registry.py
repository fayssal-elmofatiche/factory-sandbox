"""In-memory event registrations with a capacity limit and a waitlist."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Event:
    id: str
    title: str
    day: date
    capacity: int
    confirmed: list[str] = field(default_factory=list)
    waitlist: list[str] = field(default_factory=list)

    @property
    def seats_taken(self) -> int:
        return len(self.confirmed)


class RegistrationError(ValueError):
    pass


class Registry:
    def __init__(self) -> None:
        self._events: dict[str, Event] = {}

    def add_event(self, event: Event) -> None:
        if event.id in self._events:
            raise RegistrationError(f"event {event.id!r} already exists")
        if event.capacity < 1:
            raise RegistrationError("capacity must be at least 1")
        self._events[event.id] = event

    def event(self, event_id: str) -> Event:
        try:
            return self._events[event_id]
        except KeyError:
            raise RegistrationError(f"unknown event {event_id!r}") from None

    def events(self) -> list[Event]:
        return sorted(self._events.values(), key=lambda e: e.day)

    def register(self, event_id: str, email: str) -> str:
        """Register an email. Returns "confirmed" or "waitlisted"."""
        event = self.event(event_id)
        email = email.strip().lower()
        if "@" not in email:
            raise RegistrationError(f"not an email address: {email!r}")
        if email in event.confirmed or email in event.waitlist:
            raise RegistrationError(f"{email} is already registered")
        if event.seats_taken < event.capacity:
            event.confirmed.append(email)
            return "confirmed"
        event.waitlist.append(email)
        return "waitlisted"

    def cancel(self, event_id: str, email: str) -> str | None:
        """Cancel a registration. Returns the email promoted from the waitlist, if any."""
        event = self.event(event_id)
        email = email.strip().lower()
        if email in event.waitlist:
            event.waitlist.remove(email)
            return None
        if email not in event.confirmed:
            raise RegistrationError(f"{email} is not registered")
        event.confirmed.remove(email)
        if event.waitlist:
            promoted = event.waitlist.pop(0)
            event.confirmed.append(promoted)
            return promoted
        return None
