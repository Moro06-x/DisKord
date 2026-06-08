"""
pydisk.core.rest
~~~~~~~~~~~~~~~~
Async HTTP client for the Discord REST API.

Handles:
- Authentication headers
- Per-route and global rate limit retry logic
- JSON requests
- Multipart file uploads (for sending attachments / PDFs)
- Automatic body reading within the response context manager
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Dict, IO, List, Optional, Tuple, Union

import aiohttp

__all__ = ["HTTPClient", "HTTPError", "RateLimitError"]

log = logging.getLogger("pydisk.http")

DISCORD_API_BASE = "https://discord.com/api/v10"
_LIB_VERSION = "0.4.0"

# Type alias for a file upload entry:
#   (filename, file_object, content_type)
#   or just (filename, file_object)
FileUpload = Union[
    Tuple[str, IO[bytes]],
    Tuple[str, IO[bytes], str],
]


class RateLimitError(Exception):
    """Raised when a rate limit is hit and all retries are exhausted."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Rate limited. Retry after {retry_after:.2f}s")


class HTTPError(Exception):
    """Raised on non-2xx Discord API responses."""

    def __init__(self, status: int, message: str, *, code: int = 0) -> None:
        self.status = status
        self.code = code          # Discord JSON error code (if any)
        super().__init__(f"HTTP {status}: {message}" + (f" (code {code})" if code else ""))


class HTTPClient:
    """
    Async HTTP client for the Discord REST API.

    Usage::

        http = HTTPClient("Bot TOKEN")

        # Plain JSON request
        data = await http.get("/guilds/123456789")

        # Send a file attachment
        await http.post(
            "/channels/987654321/messages",
            data={"content": "Here is your file!"},
            files=[("report.pdf", pdf_bytes_io, "application/pdf")],
        )

        await http.close()
    """

    def __init__(self, token: str, *, max_retries: int = 5) -> None:
        self.token = token
        self.max_retries = max_retries
        self._session: Optional[aiohttp.ClientSession] = None
        # Event is SET when we're clear to send; CLEARED during a global rate limit.
        self._global_rl_lock = asyncio.Event()
        self._global_rl_lock.set()
        # Back-reference so SmartResponder / confirm() / prompt() can hook in.
        self._client_ref: Any = None

    # ------------------------------------------------------------------ #
    #  Session management
    # ------------------------------------------------------------------ #

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @property
    def _base_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bot {self.token}",
            "User-Agent": f"DiscordBot (https://github.com/pydisk/pydisk, {_LIB_VERSION})",
        }

    # ------------------------------------------------------------------ #
    #  Core request method
    # ------------------------------------------------------------------ #

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[List[FileUpload]] = None,
    ) -> Any:
        """
        Make an authenticated request to the Discord API.

        Parameters
        ----------
        method:
            HTTP verb (``"GET"``, ``"POST"``, etc.).
        endpoint:
            API path, e.g. ``"/channels/123/messages"``.
        json:
            JSON body dict. Mutually exclusive with ``files``.
        params:
            URL query parameters.
        data:
            Form fields to include alongside file uploads.
            Ignored when ``files`` is None.
        files:
            List of file uploads. Each item is a tuple of
            ``(filename, file_object)`` or
            ``(filename, file_object, content_type)``.
            When provided, the request is sent as multipart/form-data.

        Returns
        -------
        Any
            Parsed JSON response, or ``None`` for 204 No Content.

        Raises
        ------
        RateLimitError
            If all retry attempts are exhausted on a 429 response.
        HTTPError
            On any non-2xx response after retries.
        """
        url = f"{DISCORD_API_BASE}{endpoint}"
        session = await self._get_session()

        for attempt in range(self.max_retries):
            # Block here if a global rate limit is active
            await self._global_rl_lock.wait()

            # Build request kwargs
            kwargs: Dict[str, Any] = {
                "headers": dict(self._base_headers),
                "params": params,
            }

            if files:
                # ── Multipart upload ────────────────────────────────────
                form = aiohttp.FormData()

                # Attach optional text fields (e.g. {"content": "Here!"})
                if data:
                    for key, value in data.items():
                        form.add_field(key, str(value))

                # Attach each file
                for i, upload in enumerate(files):
                    if len(upload) == 3:
                        fname, fobj, ctype = upload
                    else:
                        fname, fobj = upload
                        ctype = "application/octet-stream"

                    # Accept both BytesIO and raw bytes
                    if isinstance(fobj, (bytes, bytearray)):
                        fobj = io.BytesIO(fobj)

                    form.add_field(
                        f"files[{i}]",
                        fobj,
                        filename=fname,
                        content_type=ctype,
                    )

                kwargs["data"] = form
                # Do NOT set Content-Type header — aiohttp sets the correct
                # multipart boundary automatically.

            elif json is not None:
                kwargs["json"] = json
                kwargs["headers"]["Content-Type"] = "application/json"

            async with session.request(method, url, **kwargs) as response:
                # ── 204 No Content ──────────────────────────────────────
                if response.status == 204:
                    return None

                # ── Global rate limit ───────────────────────────────────
                # Discord sets X-RateLimit-Global: true AND Retry-After
                if response.headers.get("X-RateLimit-Global"):
                    retry_after = float(response.headers.get("Retry-After", 1))
                    log.warning(f"Global rate limit hit. Waiting {retry_after}s.")
                    self._global_rl_lock.clear()
                    await asyncio.sleep(retry_after)
                    self._global_rl_lock.set()
                    continue  # retry

                # ── Per-route rate limit (429) ──────────────────────────
                if response.status == 429:
                    try:
                        body = await response.json(content_type=None)
                        retry_after = float(body.get("retry_after", 1.0))
                    except Exception:
                        retry_after = float(response.headers.get("Retry-After", 1.0))

                    if attempt < self.max_retries - 1:
                        log.warning(
                            f"Rate limited on {method} {endpoint}. "
                            f"Retrying in {retry_after:.2f}s (attempt {attempt + 1})."
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    raise RateLimitError(retry_after)

                # ── Read body (do this ONCE inside the context manager) ─
                try:
                    body = await response.json(content_type=None)
                except Exception:
                    body = {}

                # ── Error responses ─────────────────────────────────────
                if response.status >= 400:
                    if isinstance(body, dict):
                        msg = body.get("message", "Unknown error")
                        code = body.get("code", 0)
                    else:
                        msg = "Unknown error"
                        code = 0
                    raise HTTPError(response.status, msg, code=code)

                return body

        raise HTTPError(0, f"Max retries ({self.max_retries}) exceeded on {method} {endpoint}")

    # ------------------------------------------------------------------ #
    #  Convenience wrappers
    # ------------------------------------------------------------------ #

    async def get(self, endpoint: str, **kwargs: Any) -> Any:
        return await self.request("GET", endpoint, **kwargs)

    async def post(self, endpoint: str, **kwargs: Any) -> Any:
        return await self.request("POST", endpoint, **kwargs)

    async def patch(self, endpoint: str, **kwargs: Any) -> Any:
        return await self.request("PATCH", endpoint, **kwargs)

    async def put(self, endpoint: str, **kwargs: Any) -> Any:
        return await self.request("PUT", endpoint, **kwargs)

    async def delete(self, endpoint: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", endpoint, **kwargs)

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
