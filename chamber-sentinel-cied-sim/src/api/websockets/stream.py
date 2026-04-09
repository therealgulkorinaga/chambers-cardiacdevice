"""
WebSocket endpoints for real-time streaming.

Provides four WebSocket channels per simulation:
- /ws/telemetry/{sim_id}  -- raw telemetry events
- /ws/burns/{sim_id}      -- burn lifecycle events
- /ws/alerts/{sim_id}     -- clinical alert events
- /ws/metrics/{sim_id}    -- aggregated metric updates

Uses asyncio.Queue-based fan-out so multiple dashboard clients can subscribe
to the same simulation stream simultaneously.  Handles connect/disconnect
gracefully with automatic cleanup of dead subscribers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from enum import Enum
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger("chamber_sentinel.ws")

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["websockets"])

# ---------------------------------------------------------------------------
# Channel types
# ---------------------------------------------------------------------------


class ChannelType(str, Enum):
    TELEMETRY = "telemetry"
    BURNS = "burns"
    ALERTS = "alerts"
    METRICS = "metrics"


# ---------------------------------------------------------------------------
# Fan-out subscription manager
# ---------------------------------------------------------------------------


class Subscriber:
    """Wraps a single WebSocket client with its private async queue."""

    def __init__(self, websocket: WebSocket, subscriber_id: str | None = None) -> None:
        self.websocket = websocket
        self.subscriber_id = subscriber_id or str(uuid.uuid4())
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self.connected = True
        self.created_at = time.time()
        self.messages_sent = 0

    async def send(self, data: dict[str, Any]) -> bool:
        """Send a message to this subscriber. Returns False if disconnected."""
        if not self.connected:
            return False
        try:
            await self.websocket.send_json(data)
            self.messages_sent += 1
            return True
        except Exception:
            self.connected = False
            return False

    def enqueue(self, data: dict[str, Any]) -> bool:
        """Put a message on the subscriber's queue. Drops if full."""
        if not self.connected:
            return False
        try:
            self.queue.put_nowait(data)
            return True
        except asyncio.QueueFull:
            logger.warning(
                "Subscriber %s queue full; dropping message", self.subscriber_id
            )
            return False


class ChannelManager:
    """Manages subscriber lists per (sim_id, channel_type) pair.

    Provides publish/subscribe fan-out: when a message is published to a
    channel, every active subscriber on that channel receives a copy.
    """

    def __init__(self) -> None:
        # (sim_id, channel_type) -> list[Subscriber]
        self._channels: dict[tuple[str, ChannelType], list[Subscriber]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(
        self, sim_id: str, channel: ChannelType, websocket: WebSocket
    ) -> Subscriber:
        """Register a new subscriber on a channel."""
        subscriber = Subscriber(websocket)
        key = (sim_id, channel)
        async with self._lock:
            if key not in self._channels:
                self._channels[key] = []
            self._channels[key].append(subscriber)
        logger.info(
            "Subscriber %s joined %s/%s (total: %d)",
            subscriber.subscriber_id,
            sim_id,
            channel.value,
            len(self._channels[key]),
        )
        return subscriber

    async def unsubscribe(
        self, sim_id: str, channel: ChannelType, subscriber: Subscriber
    ) -> None:
        """Remove a subscriber from a channel."""
        subscriber.connected = False
        key = (sim_id, channel)
        async with self._lock:
            subs = self._channels.get(key, [])
            self._channels[key] = [s for s in subs if s.subscriber_id != subscriber.subscriber_id]
            if not self._channels[key]:
                del self._channels[key]
        logger.info(
            "Subscriber %s left %s/%s",
            subscriber.subscriber_id,
            sim_id,
            channel.value,
        )

    async def publish(
        self, sim_id: str, channel: ChannelType, data: dict[str, Any]
    ) -> int:
        """Publish a message to all subscribers on a channel.

        Returns the number of subscribers that successfully received the
        message.
        """
        key = (sim_id, channel)
        async with self._lock:
            subscribers = list(self._channels.get(key, []))

        delivered = 0
        dead: list[Subscriber] = []
        for sub in subscribers:
            ok = sub.enqueue(data)
            if ok:
                delivered += 1
            elif not sub.connected:
                dead.append(sub)

        # Prune disconnected subscribers
        if dead:
            async with self._lock:
                subs = self._channels.get(key, [])
                alive_ids = {s.subscriber_id for s in dead}
                self._channels[key] = [s for s in subs if s.subscriber_id not in alive_ids]

        return delivered

    async def get_channel_info(
        self, sim_id: str, channel: ChannelType
    ) -> dict[str, Any]:
        """Return metadata about a channel."""
        key = (sim_id, channel)
        async with self._lock:
            subs = self._channels.get(key, [])
        return {
            "sim_id": sim_id,
            "channel": channel.value,
            "subscriber_count": len(subs),
            "subscribers": [
                {
                    "id": s.subscriber_id,
                    "connected": s.connected,
                    "messages_sent": s.messages_sent,
                    "queue_size": s.queue.qsize(),
                }
                for s in subs
            ],
        }

    async def get_all_channels(self) -> list[dict[str, Any]]:
        """Return info about all active channels."""
        async with self._lock:
            keys = list(self._channels.keys())
        result = []
        for sim_id, channel in keys:
            info = await self.get_channel_info(sim_id, channel)
            result.append(info)
        return result


# Singleton channel manager
channel_manager = ChannelManager()


# ---------------------------------------------------------------------------
# Drainer task: reads from subscriber queue and pushes to websocket
# ---------------------------------------------------------------------------


async def _drain_subscriber(subscriber: Subscriber) -> None:
    """Continuously drain the subscriber's queue to the websocket.

    Exits when the subscriber disconnects or the queue yields a sentinel.
    """
    try:
        while subscriber.connected:
            try:
                data = await asyncio.wait_for(subscriber.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Send keepalive ping to detect dead connections
                try:
                    await subscriber.websocket.send_json({"type": "ping", "ts": time.time()})
                except Exception:
                    subscriber.connected = False
                    break
                continue

            if data is None:
                # Sentinel: graceful shutdown
                break

            ok = await subscriber.send(data)
            if not ok:
                break
    except Exception:
        subscriber.connected = False


# ---------------------------------------------------------------------------
# WebSocket handler helper
# ---------------------------------------------------------------------------


async def _ws_handler(
    websocket: WebSocket, sim_id: str, channel: ChannelType
) -> None:
    """Shared WebSocket lifecycle handler for all channel types.

    1. Accept the connection
    2. Subscribe to the channel
    3. Start the drain task
    4. Listen for client messages (control frames / pong)
    5. Clean up on disconnect
    """
    await websocket.accept()

    subscriber = await channel_manager.subscribe(sim_id, channel, websocket)

    # Send welcome message
    await subscriber.send({
        "type": "connected",
        "channel": channel.value,
        "sim_id": sim_id,
        "subscriber_id": subscriber.subscriber_id,
        "ts": time.time(),
    })

    # Start background drain task
    drain_task = asyncio.create_task(_drain_subscriber(subscriber))

    try:
        while True:
            # Listen for client messages (pong, or control commands)
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await subscriber.send({
                    "type": "error",
                    "detail": "Invalid JSON",
                    "ts": time.time(),
                })
                continue

            msg_type = msg.get("type", "")

            if msg_type == "pong":
                # Client responded to our keepalive
                continue
            elif msg_type == "subscribe":
                # Could extend to subscribe to additional filters
                await subscriber.send({
                    "type": "ack",
                    "detail": "Already subscribed",
                    "ts": time.time(),
                })
            elif msg_type == "unsubscribe":
                break
            else:
                await subscriber.send({
                    "type": "ack",
                    "received": msg_type,
                    "ts": time.time(),
                })

    except WebSocketDisconnect:
        logger.info(
            "Client disconnected from %s/%s (sub=%s)",
            sim_id,
            channel.value,
            subscriber.subscriber_id,
        )
    except Exception as exc:
        logger.warning(
            "WebSocket error on %s/%s: %s",
            sim_id,
            channel.value,
            exc,
        )
    finally:
        subscriber.connected = False
        # Signal drain task to stop
        try:
            subscriber.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        await channel_manager.unsubscribe(sim_id, channel, subscriber)


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


@router.websocket("/ws/telemetry/{sim_id}")
async def ws_telemetry(websocket: WebSocket, sim_id: str) -> None:
    """Stream raw telemetry events for a simulation in real time.

    Each message is a JSON object with fields:
    ``type``, ``event_id``, ``patient_id``, ``event_type``, ``timestamp_s``,
    ``payload``, ``size_bytes``.

    Clients can send ``{"type": "pong"}`` in response to keepalive pings,
    or ``{"type": "unsubscribe"}`` to gracefully close.
    """
    await _ws_handler(websocket, sim_id, ChannelType.TELEMETRY)


@router.websocket("/ws/burns/{sim_id}")
async def ws_burns(websocket: WebSocket, sim_id: str) -> None:
    """Stream burn lifecycle events for a simulation.

    Each message describes a data record that was permanently deleted,
    including the world, patient, event type, and reason.
    """
    await _ws_handler(websocket, sim_id, ChannelType.BURNS)


@router.websocket("/ws/alerts/{sim_id}")
async def ws_alerts(websocket: WebSocket, sim_id: str) -> None:
    """Stream clinical alert events for a simulation.

    Includes alert priority, type, associated patient and episode data.
    High/critical alerts are delivered with minimal latency.
    """
    await _ws_handler(websocket, sim_id, ChannelType.ALERTS)


@router.websocket("/ws/metrics/{sim_id}")
async def ws_metrics(websocket: WebSocket, sim_id: str) -> None:
    """Stream real-time metric updates for a simulation.

    Delivers periodic snapshots of persistence volume, attack surface,
    clinical availability, and per-world counters.
    """
    await _ws_handler(websocket, sim_id, ChannelType.METRICS)


# ---------------------------------------------------------------------------
# Publish helpers (called by the simulation engine)
# ---------------------------------------------------------------------------


async def publish_telemetry_event(sim_id: str, event: dict[str, Any]) -> int:
    """Publish a telemetry event to all subscribers on the telemetry channel."""
    event["type"] = "telemetry_event"
    event["ts"] = time.time()
    return await channel_manager.publish(sim_id, ChannelType.TELEMETRY, event)


async def publish_burn_event(sim_id: str, burn: dict[str, Any]) -> int:
    """Publish a burn event to all subscribers on the burns channel."""
    burn["type"] = "burn_event"
    burn["ts"] = time.time()
    return await channel_manager.publish(sim_id, ChannelType.BURNS, burn)


async def publish_alert(sim_id: str, alert: dict[str, Any]) -> int:
    """Publish a clinical alert to all subscribers on the alerts channel."""
    alert["type"] = "alert_event"
    alert["ts"] = time.time()
    return await channel_manager.publish(sim_id, ChannelType.ALERTS, alert)


async def publish_metrics(sim_id: str, metrics: dict[str, Any]) -> int:
    """Publish a metrics snapshot to all subscribers on the metrics channel."""
    metrics["type"] = "metrics_update"
    metrics["ts"] = time.time()
    return await channel_manager.publish(sim_id, ChannelType.METRICS, metrics)
