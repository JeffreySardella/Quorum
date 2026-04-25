from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable


class NotificationBus:
    """Lightweight pub/sub event system for processing notifications."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}
        self._history: list[dict] = []

    def on(self, event: str, callback: Callable) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def emit(self, event: str, summary: str, details: dict[str, Any] | None = None) -> None:
        entry = {
            "event": event,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "details": details or {},
        }
        self._history.append(entry)
        for cb in self._listeners.get(event, []):
            try:
                cb(entry)
            except Exception:
                pass
        for cb in self._listeners.get("*", []):
            try:
                cb(entry)
            except Exception:
                pass

    def history(self, limit: int = 50) -> list[dict]:
        return list(reversed(self._history[-limit:]))


def webhook_listener(url: str, event_filter: list[str] | None = None) -> Callable:
    """Create a listener that POSTs notifications to a webhook URL."""
    def _send(entry: dict) -> None:
        if event_filter and entry["event"] not in event_filter:
            return
        try:
            import httpx
            httpx.post(url, json=entry, timeout=10)
        except Exception:
            pass
    return _send


def desktop_listener() -> Callable:
    """Create a listener that shows OS-native desktop notifications."""
    def _notify(entry: dict) -> None:
        try:
            from plyer import notification
            notification.notify(
                title=f"Quorum: {entry['event']}",
                message=entry["summary"],
                timeout=5,
            )
        except Exception:
            pass
    return _notify


def setup_notifications(settings) -> NotificationBus:
    """Create and configure a NotificationBus from settings."""
    bus = NotificationBus()

    notify_cfg = getattr(settings, "notify", None)
    if not notify_cfg:
        return bus

    if getattr(notify_cfg, "desktop", False):
        bus.on("*", desktop_listener())

    webhook_url = getattr(notify_cfg, "webhook", "")
    if webhook_url:
        event_filter = getattr(notify_cfg, "webhook_events", None)
        bus.on("*", webhook_listener(webhook_url, event_filter))

    return bus
