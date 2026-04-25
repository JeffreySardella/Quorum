from __future__ import annotations

from quorum.notify import NotificationBus


class TestNotificationBus:
    def test_emit_and_history(self) -> None:
        bus = NotificationBus()
        bus.emit("job.completed", "Done", {"count": 5})
        history = bus.history()
        assert len(history) == 1
        assert history[0]["event"] == "job.completed"
        assert history[0]["summary"] == "Done"
        assert history[0]["details"]["count"] == 5

    def test_listener_called(self) -> None:
        bus = NotificationBus()
        received = []
        bus.on("job.completed", lambda e: received.append(e))
        bus.emit("job.completed", "Done")
        assert len(received) == 1

    def test_wildcard_listener(self) -> None:
        bus = NotificationBus()
        received = []
        bus.on("*", lambda e: received.append(e))
        bus.emit("job.completed", "Done")
        bus.emit("file.moved", "Moved")
        assert len(received) == 2

    def test_listener_error_doesnt_crash(self) -> None:
        bus = NotificationBus()
        bus.on("test", lambda e: 1 / 0)  # raises ZeroDivisionError
        bus.emit("test", "This should not crash")
        assert len(bus.history()) == 1

    def test_history_limit(self) -> None:
        bus = NotificationBus()
        for i in range(100):
            bus.emit("test", f"Event {i}")
        history = bus.history(limit=10)
        assert len(history) == 10
        assert history[0]["summary"] == "Event 99"  # most recent first

    def test_specific_listener_not_called_for_other_events(self) -> None:
        bus = NotificationBus()
        received = []
        bus.on("job.completed", lambda e: received.append(e))
        bus.emit("file.moved", "Different event")
        assert len(received) == 0

    def test_multiple_listeners(self) -> None:
        bus = NotificationBus()
        results = {"a": 0, "b": 0}
        bus.on("test", lambda e: results.update(a=results["a"] + 1))
        bus.on("test", lambda e: results.update(b=results["b"] + 1))
        bus.emit("test", "Both should fire")
        assert results["a"] == 1
        assert results["b"] == 1
