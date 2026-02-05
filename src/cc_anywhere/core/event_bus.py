"""Event bus for publishing and subscribing to hook events."""

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Optional

from .events import HookEvent, HookEventType

logger = logging.getLogger(__name__)

# Type alias for event callbacks
# Callbacks receive HookEvent and should be async functions
EventCallback = Callable[[HookEvent], None]


class EventBus:
    """Pub/Sub event bus for hook events.

    Allows adapters to subscribe to specific event types and receive
    events when they are published.

    Example:
        bus = EventBus()

        async def on_stop(event: HookEvent):
            print(f"Task completed: {event.session_id}")

        bus.subscribe(HookEventType.STOP, on_stop)
        await bus.publish(event)
    """

    _instance: Optional["EventBus"] = None

    def __init__(self):
        """Initialize EventBus."""
        # Dict mapping event types to list of callbacks
        self._subscribers: dict[HookEventType, list[EventCallback]] = defaultdict(list)
        # Callbacks for all events
        self._global_subscribers: list[EventCallback] = []

    @classmethod
    def get_instance(cls) -> "EventBus":
        """Get singleton instance of EventBus."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def subscribe(
        self,
        event_type: Optional[HookEventType],
        callback: EventCallback,
    ) -> None:
        """Subscribe to events of a specific type.

        Args:
            event_type: Type of event to subscribe to, or None for all events
            callback: Async function to call when event is published
        """
        if event_type is None:
            if callback not in self._global_subscribers:
                self._global_subscribers.append(callback)
        else:
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(
        self,
        event_type: Optional[HookEventType],
        callback: EventCallback,
    ) -> None:
        """Unsubscribe from events.

        Args:
            event_type: Type of event to unsubscribe from, or None for global
            callback: Callback to remove
        """
        if event_type is None:
            if callback in self._global_subscribers:
                self._global_subscribers.remove(callback)
        else:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    async def publish(self, event: HookEvent) -> None:
        """Publish an event to all subscribers.

        Args:
            event: The event to publish
        """
        logger.info(f"Publishing event: {event.event_type.value} for session {event.session_id}")

        # Get all relevant callbacks
        callbacks = list(self._subscribers[event.event_type]) + list(self._global_subscribers)

        if not callbacks:
            return

        # Execute all callbacks concurrently
        tasks = []
        for callback in callbacks:
            try:
                result = callback(event)
                # Handle both sync and async callbacks
                if asyncio.iscoroutine(result):
                    tasks.append(asyncio.create_task(result))
            except Exception as e:
                logger.error(f"Error in callback {callback.__name__}: {e}")

        # Wait for all async callbacks to complete
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Async callback error: {result}")

    def clear(self) -> None:
        """Clear all subscribers."""
        self._subscribers.clear()
        self._global_subscribers.clear()

    @property
    def subscriber_count(self) -> int:
        """Get total number of subscribers."""
        count = len(self._global_subscribers)
        for callbacks in self._subscribers.values():
            count += len(callbacks)
        return count


# Global event bus instance
_event_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Get the global EventBus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
