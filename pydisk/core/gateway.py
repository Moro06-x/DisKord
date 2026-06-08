"""
pydisk.core.gateway
~~~~~~~~~~~~~~~~~~~
Discord Gateway (WebSocket) client.
"""

import asyncio
import json
import logging
import platform
import random
import time
from typing import Any, Callable, Dict, Optional

import aiohttp

log = logging.getLogger("pydisk.gateway")

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

class OpCode:
    DISPATCH            = 0
    HEARTBEAT           = 1
    IDENTIFY            = 2
    PRESENCE_UPDATE     = 3
    VOICE_STATE_UPDATE  = 4
    RESUME              = 6
    RECONNECT           = 7
    REQUEST_GUILD_MEMBERS = 8
    INVALID_SESSION     = 9
    HELLO               = 10
    HEARTBEAT_ACK       = 11

class Intents:
    GUILDS                  = 1 << 0
    GUILD_MEMBERS           = 1 << 1
    GUILD_MODERATION        = 1 << 2
    GUILD_EMOJIS            = 1 << 3
    GUILD_INTEGRATIONS      = 1 << 4
    GUILD_WEBHOOKS          = 1 << 5
    GUILD_INVITES           = 1 << 6
    GUILD_VOICE_STATES      = 1 << 7
    GUILD_PRESENCES         = 1 << 8
    GUILD_MESSAGES          = 1 << 9
    GUILD_MESSAGE_REACTIONS = 1 << 10
    GUILD_MESSAGE_TYPING    = 1 << 11
    DIRECT_MESSAGES         = 1 << 12
    DIRECT_MESSAGE_REACTIONS= 1 << 13
    DIRECT_MESSAGE_TYPING   = 1 << 14
    MESSAGE_CONTENT         = 1 << 15
    GUILD_SCHEDULED_EVENTS  = 1 << 16
    AUTO_MODERATION_CONFIG  = 1 << 20
    AUTO_MODERATION_EXECUTE = 1 << 21

    DEFAULT = (
        GUILDS | GUILD_MESSAGES | GUILD_MESSAGE_REACTIONS |
        DIRECT_MESSAGES | GUILD_VOICE_STATES | MESSAGE_CONTENT
    )
    ALL = 0x7FFFF


class GatewayClient:
    """
    Manages the WebSocket connection to the Discord Gateway.
    Automatically heartbeats, resumes, and reconnects.
    """

    def __init__(
        self,
        token: str,
        intents: int,
        dispatch: Callable,
        *,
        shard_id: int = 0,
        shard_count: int = 1,
    ):
        self.token = token
        self.intents = intents
        self.dispatch = dispatch
        self.shard_id = shard_id
        self.shard_count = shard_count

        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._sequence: Optional[int] = None
        self._session_id: Optional[str] = None
        self._resume_gateway_url: Optional[str] = None
        self._heartbeat_interval: float = 41.25
        self._last_heartbeat_ack: float = time.monotonic()
        self._last_heartbeat_sent: float = 0.0
        self._closed = False
        # BUG FIX: zlib decompression needs a persistent decompressor
        # across frames (Discord uses a shared zlib context per connection)
        self._zlib_decompressor = None

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def connect(self):
        """Connect (or reconnect) to the gateway and start the event loop."""
        self._closed = False
        backoff = 1.0

        while not self._closed:
            try:
                await self._run()
                backoff = 1.0
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
                log.warning(f"Gateway connection error: {e}. Reconnecting in {backoff}s…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            except Exception as e:
                log.error(f"Unexpected gateway error: {e}", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def close(self):
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    async def update_presence(self, *, status: str = "online", activity_name: str = "", activity_type: int = 0):
        """Change the bot's status/activity at runtime."""
        payload: Dict[str, Any] = {
            "op": OpCode.PRESENCE_UPDATE,
            "d": {
                "since": None,
                "activities": [{"name": activity_name, "type": activity_type}] if activity_name else [],
                "status": status,
                "afk": False,
            }
        }
        await self._send(payload)

    # ------------------------------------------------------------------ #
    #  Internal connection loop
    # ------------------------------------------------------------------ #

    async def _run(self):
        url = self._resume_gateway_url or GATEWAY_URL
        self._session = aiohttp.ClientSession()
        # Reset zlib decompressor for each new connection
        import zlib
        self._zlib_decompressor = zlib.decompressobj()

        try:
            async with self._session.ws_connect(
                url,
                heartbeat=None,
                max_msg_size=0,
            ) as ws:
                self._ws = ws
                log.info(f"Connected to gateway: {url}")
                await self._handle_messages()
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                self._heartbeat_task = None
            if not self._session.closed:
                await self._session.close()

    async def _handle_messages(self):
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_payload(json.loads(msg.data))
            elif msg.type == aiohttp.WSMsgType.BINARY:
                # BUG FIX: Discord uses a shared zlib context — must use the
                # persistent decompressor, not a fresh zlib.decompress() call.
                # Also, Discord's compressed frames end with the zlib suffix
                # b'\x00\x00\xff\xff'; we must feed each chunk to the decompressor.
                try:
                    data = self._zlib_decompressor.decompress(msg.data)
                    await self._handle_payload(json.loads(data))
                except Exception as e:
                    log.error(f"Failed to decompress gateway message: {e}")
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
                log.warning(f"WebSocket closed: {msg.data}")
                break
            elif msg.type == aiohttp.WSMsgType.ERROR:
                log.error(f"WebSocket error: {msg.data}")
                break

    # ------------------------------------------------------------------ #
    #  Payload handling
    # ------------------------------------------------------------------ #

    async def _handle_payload(self, payload: dict):
        op   = payload.get("op")
        data = payload.get("d")
        seq  = payload.get("s")
        event= payload.get("t")

        if seq is not None:
            self._sequence = seq

        if op == OpCode.HELLO:
            self._heartbeat_interval = data["heartbeat_interval"] / 1000.0
            # Jitter on first heartbeat per Discord spec
            await asyncio.sleep(self._heartbeat_interval * random.random())
            # Initialize ACK time so zombie detection doesn't fire immediately
            self._last_heartbeat_ack = time.monotonic()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            if self._session_id and self._sequence:
                await self._resume()
            else:
                await self._identify()

        elif op == OpCode.HEARTBEAT_ACK:
            self._last_heartbeat_ack = time.monotonic()
            latency = self._last_heartbeat_ack - self._last_heartbeat_sent
            log.debug(f"Heartbeat ACK (latency: {latency*1000:.0f}ms)")

        elif op == OpCode.HEARTBEAT:
            # Server requests immediate heartbeat
            await self._send_heartbeat()

        elif op == OpCode.RECONNECT:
            log.info("Gateway requested reconnect.")
            await self._ws.close()

        elif op == OpCode.INVALID_SESSION:
            resumable = bool(data)
            log.warning(f"Invalid session (resumable={resumable})")
            if not resumable:
                self._session_id = None
                self._sequence = None
                self._resume_gateway_url = None
            await asyncio.sleep(random.uniform(1, 5))
            await self._ws.close()

        elif op == OpCode.DISPATCH:
            await self._handle_dispatch(event, data)

    async def _handle_dispatch(self, event: str, data: dict):
        if event == "READY":
            self._session_id = data["session_id"]
            self._resume_gateway_url = data.get("resume_gateway_url", GATEWAY_URL)
            log.info(f"READY — session {self._session_id}")
            await self.dispatch("ready", data)

        elif event == "RESUMED":
            log.info("Session resumed successfully.")
            await self.dispatch("resumed", data)

        elif event == "INTERACTION_CREATE":
            await self.dispatch("interaction_create", data)

        elif event == "MESSAGE_CREATE":
            await self.dispatch("message_create", data)

        elif event == "MESSAGE_UPDATE":
            await self.dispatch("message_update", data)

        elif event == "MESSAGE_DELETE":
            await self.dispatch("message_delete", data)

        elif event == "GUILD_CREATE":
            await self.dispatch("guild_create", data)

        elif event == "GUILD_DELETE":
            await self.dispatch("guild_delete", data)

        elif event == "GUILD_MEMBER_ADD":
            await self.dispatch("member_join", data)

        elif event == "GUILD_MEMBER_REMOVE":
            await self.dispatch("member_leave", data)

        elif event == "GUILD_MEMBER_UPDATE":
            await self.dispatch("member_update", data)

        elif event == "GUILD_BAN_ADD":
            await self.dispatch("ban_add", data)

        elif event == "GUILD_BAN_REMOVE":
            await self.dispatch("ban_remove", data)

        elif event == "CHANNEL_CREATE":
            await self.dispatch("channel_create", data)

        elif event == "CHANNEL_DELETE":
            await self.dispatch("channel_delete", data)

        elif event == "TYPING_START":
            await self.dispatch("typing_start", data)

        elif event == "REACTION_ADD":
            await self.dispatch("reaction_add", data)

        elif event == "REACTION_REMOVE":
            await self.dispatch("reaction_remove", data)

        elif event == "VOICE_STATE_UPDATE":
            await self.dispatch("voice_state_update", data)

        else:
            await self.dispatch(event.lower(), data)

    # ------------------------------------------------------------------ #
    #  Heartbeat
    # ------------------------------------------------------------------ #

    async def _heartbeat_loop(self):
        """
        BUG FIX: Added zombie connection detection.
        If we send a heartbeat and don't get an ACK back before the NEXT
        heartbeat is due, the connection is dead — close and reconnect.
        """
        try:
            while True:
                await self._send_heartbeat()
                await asyncio.sleep(self._heartbeat_interval)
                # Check if we got an ACK since the last send
                if self._last_heartbeat_ack < self._last_heartbeat_sent:
                    log.warning("No heartbeat ACK received — zombie connection detected. Reconnecting.")
                    if self._ws and not self._ws.closed:
                        await self._ws.close()
                    return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"Heartbeat loop error: {e}")

    async def _send_heartbeat(self):
        self._last_heartbeat_sent = time.monotonic()
        await self._send({"op": OpCode.HEARTBEAT, "d": self._sequence})

    # ------------------------------------------------------------------ #
    #  Identify / Resume
    # ------------------------------------------------------------------ #

    async def _identify(self):
        payload = {
            "op": OpCode.IDENTIFY,
            "d": {
                "token": self.token,
                "intents": self.intents,
                "properties": {
                    "os": platform.system().lower(),
                    "browser": "pydisk",
                    "device": "pydisk",
                },
                "shard": [self.shard_id, self.shard_count],
                "presence": {
                    "activities": [],
                    "status": "online",
                    "since": None,
                    "afk": False,
                },
                "compress": False,
                "large_threshold": 250,
            }
        }
        await self._send(payload)

    async def _resume(self):
        payload = {
            "op": OpCode.RESUME,
            "d": {
                "token": self.token,
                "session_id": self._session_id,
                "seq": self._sequence,
            }
        }
        await self._send(payload)

    # ------------------------------------------------------------------ #
    #  Send helper
    # ------------------------------------------------------------------ #

    async def _send(self, payload: dict):
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(payload))
