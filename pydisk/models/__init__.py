"""
pydisk.models
~~~~~~~~~~~~~
Lightweight dataclasses representing Discord objects.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Any, Dict


@dataclass
class User:
    id: str
    username: str
    discriminator: str
    avatar: Optional[str] = None
    bot: bool = False

    @classmethod
    def from_data(cls, data: dict) -> "User":
        return cls(
            id=data["id"],
            username=data["username"],
            discriminator=data.get("discriminator", "0"),
            avatar=data.get("avatar"),
            bot=data.get("bot", False),
        )

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"

    def __str__(self) -> str:
        return f"{self.username}#{self.discriminator}"


@dataclass
class Member:
    user: User
    nick: Optional[str] = None
    roles: List[str] = field(default_factory=list)

    @classmethod
    def from_data(cls, data: dict) -> "Member":
        return cls(
            user=User.from_data(data["user"]),
            nick=data.get("nick"),
            roles=data.get("roles", []),
        )

    @property
    def display_name(self) -> str:
        return self.nick or self.user.username


@dataclass
class Message:
    id: str
    channel_id: str
    author: User
    content: str
    guild_id: Optional[str] = None

    @classmethod
    def from_data(cls, data: dict) -> "Message":
        return cls(
            id=data["id"],
            channel_id=data["channel_id"],
            author=User.from_data(data["author"]),
            content=data.get("content", ""),
            guild_id=data.get("guild_id"),
        )


@dataclass
class Embed:
    """Builder-style Discord embed."""
    title: Optional[str] = None
    description: Optional[str] = None
    color: Optional[int] = None
    fields: List[Dict[str, Any]] = field(default_factory=list)
    footer: Optional[Dict[str, str]] = None
    thumbnail: Optional[str] = None
    image: Optional[str] = None
    author_name: Optional[str] = None

    def add_field(self, name: str, value: str, inline: bool = False) -> "Embed":
        self.fields.append({"name": name, "value": value, "inline": inline})
        return self

    def set_footer(self, text: str, icon_url: Optional[str] = None) -> "Embed":
        self.footer = {"text": text}
        if icon_url:
            self.footer["icon_url"] = icon_url
        return self

    def set_thumbnail(self, url: str) -> "Embed":
        self.thumbnail = url
        return self

    def set_image(self, url: str) -> "Embed":
        self.image = url
        return self

    def set_author(self, name: str) -> "Embed":
        self.author_name = name
        return self

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if self.title:
            payload["title"] = self.title
        if self.description:
            payload["description"] = self.description
        if self.color is not None:
            payload["color"] = self.color
        if self.fields:
            payload["fields"] = self.fields
        if self.footer:
            payload["footer"] = self.footer
        if self.thumbnail:
            payload["thumbnail"] = {"url": self.thumbnail}
        if self.image:
            payload["image"] = {"url": self.image}
        if self.author_name:
            payload["author"] = {"name": self.author_name}
        return payload


@dataclass
class Interaction:
    """Represents a slash command interaction."""
    id: str
    token: str
    guild_id: Optional[str]
    channel_id: str
    user: User
    command_name: str
    application_id: str = ""
    locale: str = "en-US"
    guild_locale: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    _http: Any = field(default=None, repr=False)
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_data(cls, data: dict, http=None) -> "Interaction":
        user_data = data.get("member", {}).get("user") or data.get("user", {})
        options_raw = data.get("data", {}).get("options") or []
        options = {o["name"]: o.get("value") for o in options_raw}
        return cls(
            id=data["id"],
            token=data["token"],
            guild_id=data.get("guild_id"),
            channel_id=data["channel_id"],
            user=User.from_data(user_data),
            command_name=data["data"].get("name", data["data"].get("custom_id", "")),
            application_id=data.get("application_id", ""),
            locale=data.get("locale", "en-US"),
            guild_locale=data.get("guild_locale"),
            options=options,
            _http=http,
            _raw=data,
        )

    async def respond(self, content: str = "", *, embed: Optional[Embed] = None, ephemeral: bool = False) -> None:
        """Send an initial response to this interaction."""
        flags = 64 if ephemeral else 0
        msg_data: Dict[str, Any] = {"content": content, "flags": flags}
        if embed:
            msg_data["embeds"] = [embed.to_dict()]
        payload = {"type": 4, "data": msg_data}
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json=payload,
        )

    async def defer(self, *, ephemeral: bool = False) -> None:
        """Defer the interaction response (show loading state)."""
        flags = 64 if ephemeral else 0
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json={"type": 5, "data": {"flags": flags}},
        )

    async def respond_modal(self, modal: Dict[str, Any]) -> None:
        """Respond with a modal dialog (type 9)."""
        await self._http.post(
            f"/interactions/{self.id}/{self.token}/callback",
            json={"type": 9, "data": modal},
        )

    async def followup(self, content: str = "", *, embed: Optional[Embed] = None) -> None:
        """Send a follow-up message after responding.

        BUG FIX: was /webhooks/me/{token} which is invalid.
        Correct endpoint requires the application_id: /webhooks/{app_id}/{token}
        """
        msg_data: Dict[str, Any] = {"content": content}
        if embed:
            msg_data["embeds"] = [embed.to_dict()]
        app_id = self.application_id
        if not app_id:
            # Fallback: fetch from OAuth2 endpoint
            data = await self._http.get("/oauth2/applications/@me")
            app_id = data["id"]
        await self._http.post(
            f"/webhooks/{app_id}/{self.token}",
            json=msg_data,
        )
