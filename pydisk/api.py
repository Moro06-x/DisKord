"""
diskord.api
~~~~~~~~~~
Direct Discord API integration helpers.

Wraps the raw HTTPClient with higher-level, fully-typed helpers for
the most common Discord REST endpoints — so you never have to remember
URL paths or payload shapes.

Coverage
--------
Messages:       send, edit, delete, pin, unpin, bulk-delete, crosspost
Reactions:      add, remove, remove_all, get_users
Channels:       get, create, edit, delete, get messages, create invite,
                trigger-typing, create thread from message, edit permissions
Guilds:         get, edit, get members, search members, get bans,
                create / edit / delete role, get channels
Members:        get, edit nickname, add role, remove role, kick, ban, unban,
                timeout / undo-timeout
Webhooks:       get, execute, edit message
Users:          get current user, get user, create DM
Audit log:      get entries
Emoji:          list, get, create, edit, delete
Stickers:       get guild stickers
Threads:        start, join, leave, add member, remove member, list public
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

__all__ = ["APIClient"]

_MISSING = object()  # sentinel


class APIClient:
    """
    High-level Discord API wrapper.

    Attach to a ``diskord.Client``::

        import diskord

        bot = diskord.Client(token="...", application_id="...")
        api = diskord.APIClient(bot.http)

        @bot.slash_command(description="Get server info")
        async def info(interaction):
            guild = await api.get_guild(interaction.guild_id)
            await interaction.respond(f"Server: {guild['name']}")
    """

    def __init__(self, http: Any) -> None:
        self._http = http

    # =========================================================================
    #  MESSAGES
    # =========================================================================

    async def send_message(
        self,
        channel_id: str,
        content: str = "",
        *,
        embed: Optional[Any] = None,
        embeds: Optional[List[Any]] = None,
        components: Optional[List[Any]] = None,
        tts: bool = False,
        reply_to: Optional[str] = None,
        allowed_mentions: Optional[Dict] = None,
    ) -> Dict:
        payload: Dict[str, Any] = {"content": content, "tts": tts}
        if embed:
            payload["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        if embeds:
            payload["embeds"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in embeds]
        if components:
            payload["components"] = [c.to_dict() if hasattr(c, "to_dict") else c for c in components]
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to}
        if allowed_mentions:
            payload["allowed_mentions"] = allowed_mentions
        return await self._http.post(f"/channels/{channel_id}/messages", json=payload)

    async def get_message(self, channel_id: str, message_id: str) -> Dict:
        return await self._http.get(f"/channels/{channel_id}/messages/{message_id}")

    async def edit_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        content: Optional[str] = None,
        embed: Optional[Any] = None,
        embeds: Optional[List[Any]] = None,
        components: Optional[List[Any]] = None,
    ) -> Dict:
        payload: Dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embed:
            payload["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        if embeds:
            payload["embeds"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in embeds]
        if components is not None:
            payload["components"] = [c.to_dict() if hasattr(c, "to_dict") else c for c in components]
        return await self._http.patch(f"/channels/{channel_id}/messages/{message_id}", json=payload)

    async def delete_message(self, channel_id: str, message_id: str, *, reason: str = "") -> None:
        params = {"reason": reason} if reason else None
        await self._http.delete(
            f"/channels/{channel_id}/messages/{message_id}", params=params
        )

    async def bulk_delete_messages(self, channel_id: str, message_ids: List[str]) -> None:
        """Delete 2–100 messages at once (max 14 days old)."""
        await self._http.post(
            f"/channels/{channel_id}/messages/bulk-delete",
            json={"messages": message_ids},
        )

    async def pin_message(self, channel_id: str, message_id: str) -> None:
        await self._http.put(f"/channels/{channel_id}/pins/{message_id}")

    async def unpin_message(self, channel_id: str, message_id: str) -> None:
        await self._http.delete(f"/channels/{channel_id}/pins/{message_id}")

    async def get_pinned_messages(self, channel_id: str) -> List[Dict]:
        return await self._http.get(f"/channels/{channel_id}/pins")

    async def crosspost_message(self, channel_id: str, message_id: str) -> Dict:
        """Publish an announcement channel message."""
        return await self._http.post(
            f"/channels/{channel_id}/messages/{message_id}/crosspost"
        )

    async def get_messages(
        self,
        channel_id: str,
        *,
        limit: int = 50,
        before: Optional[str] = None,
        after: Optional[str] = None,
        around: Optional[str] = None,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if before:
            params["before"] = before
        if after:
            params["after"] = after
        if around:
            params["around"] = around
        return await self._http.get(f"/channels/{channel_id}/messages", params=params)

    # =========================================================================
    #  REACTIONS
    # =========================================================================

    async def add_reaction(self, channel_id: str, message_id: str, emoji: str) -> None:
        encoded = _encode_emoji(emoji)
        await self._http.put(
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me"
        )

    async def remove_reaction(
        self, channel_id: str, message_id: str, emoji: str, user_id: str = "@me"
    ) -> None:
        encoded = _encode_emoji(emoji)
        await self._http.delete(
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/{user_id}"
        )

    async def remove_all_reactions(self, channel_id: str, message_id: str) -> None:
        await self._http.delete(
            f"/channels/{channel_id}/messages/{message_id}/reactions"
        )

    async def get_reactions(
        self,
        channel_id: str,
        message_id: str,
        emoji: str,
        *,
        limit: int = 25,
    ) -> List[Dict]:
        encoded = _encode_emoji(emoji)
        return await self._http.get(
            f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}",
            params={"limit": min(limit, 100)},
        )

    # =========================================================================
    #  CHANNELS
    # =========================================================================

    async def get_channel(self, channel_id: str) -> Dict:
        return await self._http.get(f"/channels/{channel_id}")

    async def edit_channel(self, channel_id: str, *, reason: str = "", **kwargs) -> Dict:
        """
        Edit a channel. Pass Discord fields as keyword args:
        ``name``, ``topic``, ``nsfw``, ``position``, ``slowmode_delay``, etc.
        """
        params = {"reason": reason} if reason else None
        return await self._http.patch(f"/channels/{channel_id}", json=kwargs, params=params)

    async def delete_channel(self, channel_id: str, *, reason: str = "") -> Dict:
        params = {"reason": reason} if reason else None
        return await self._http.delete(f"/channels/{channel_id}", params=params)

    async def trigger_typing(self, channel_id: str) -> None:
        await self._http.post(f"/channels/{channel_id}/typing")

    async def create_invite(
        self,
        channel_id: str,
        *,
        max_age: int = 86400,
        max_uses: int = 0,
        temporary: bool = False,
        unique: bool = True,
    ) -> Dict:
        return await self._http.post(
            f"/channels/{channel_id}/invites",
            json={
                "max_age": max_age,
                "max_uses": max_uses,
                "temporary": temporary,
                "unique": unique,
            },
        )

    async def edit_channel_permissions(
        self,
        channel_id: str,
        overwrite_id: str,
        *,
        allow: int = 0,
        deny: int = 0,
        type: int = 1,  # 0 = role, 1 = member
    ) -> None:
        await self._http.put(
            f"/channels/{channel_id}/permissions/{overwrite_id}",
            json={"allow": str(allow), "deny": str(deny), "type": type},
        )

    # =========================================================================
    #  GUILDS
    # =========================================================================

    async def get_guild(self, guild_id: str, *, with_counts: bool = False) -> Dict:
        return await self._http.get(
            f"/guilds/{guild_id}",
            params={"with_counts": "true"} if with_counts else None,
        )

    async def edit_guild(self, guild_id: str, *, reason: str = "", **kwargs) -> Dict:
        params = {"reason": reason} if reason else None
        return await self._http.patch(f"/guilds/{guild_id}", json=kwargs, params=params)

    async def get_guild_channels(self, guild_id: str) -> List[Dict]:
        return await self._http.get(f"/guilds/{guild_id}/channels")

    async def create_guild_channel(
        self,
        guild_id: str,
        name: str,
        *,
        type: int = 0,
        topic: str = "",
        parent_id: Optional[str] = None,
        position: Optional[int] = None,
        reason: str = "",
    ) -> Dict:
        payload: Dict[str, Any] = {"name": name, "type": type}
        if topic:
            payload["topic"] = topic
        if parent_id:
            payload["parent_id"] = parent_id
        if position is not None:
            payload["position"] = position
        params = {"reason": reason} if reason else None
        return await self._http.post(f"/guilds/{guild_id}/channels", json=payload, params=params)

    async def get_guild_bans(self, guild_id: str, *, limit: int = 1000) -> List[Dict]:
        return await self._http.get(
            f"/guilds/{guild_id}/bans", params={"limit": min(limit, 1000)}
        )

    async def get_ban(self, guild_id: str, user_id: str) -> Dict:
        return await self._http.get(f"/guilds/{guild_id}/bans/{user_id}")

    # =========================================================================
    #  ROLES
    # =========================================================================

    async def get_guild_roles(self, guild_id: str) -> List[Dict]:
        return await self._http.get(f"/guilds/{guild_id}/roles")

    async def create_role(
        self,
        guild_id: str,
        *,
        name: str = "new role",
        color: int = 0,
        hoist: bool = False,
        mentionable: bool = False,
        permissions: int = 0,
        reason: str = "",
    ) -> Dict:
        params = {"reason": reason} if reason else None
        return await self._http.post(
            f"/guilds/{guild_id}/roles",
            json={
                "name": name,
                "color": color,
                "hoist": hoist,
                "mentionable": mentionable,
                "permissions": str(permissions),
            },
            params=params,
        )

    async def edit_role(
        self,
        guild_id: str,
        role_id: str,
        *,
        reason: str = "",
        **kwargs,
    ) -> Dict:
        params = {"reason": reason} if reason else None
        return await self._http.patch(
            f"/guilds/{guild_id}/roles/{role_id}", json=kwargs, params=params
        )

    async def delete_role(self, guild_id: str, role_id: str, *, reason: str = "") -> None:
        params = {"reason": reason} if reason else None
        await self._http.delete(f"/guilds/{guild_id}/roles/{role_id}", params=params)

    # =========================================================================
    #  MEMBERS
    # =========================================================================

    async def get_member(self, guild_id: str, user_id: str) -> Dict:
        return await self._http.get(f"/guilds/{guild_id}/members/{user_id}")

    async def list_members(
        self, guild_id: str, *, limit: int = 100, after: Optional[str] = None
    ) -> List[Dict]:
        params: Dict[str, Any] = {"limit": min(limit, 1000)}
        if after:
            params["after"] = after
        return await self._http.get(f"/guilds/{guild_id}/members", params=params)

    async def search_members(self, guild_id: str, query: str, *, limit: int = 10) -> List[Dict]:
        return await self._http.get(
            f"/guilds/{guild_id}/members/search",
            params={"query": query, "limit": min(limit, 100)},
        )

    async def edit_member(self, guild_id: str, user_id: str, *, reason: str = "", **kwargs) -> Dict:
        """
        Edit a member. Supported kwargs: ``nick``, ``roles``, ``mute``,
        ``deaf``, ``channel_id``, ``communication_disabled_until``.
        """
        params = {"reason": reason} if reason else None
        return await self._http.patch(
            f"/guilds/{guild_id}/members/{user_id}", json=kwargs, params=params
        )

    async def set_nickname(
        self, guild_id: str, user_id: str, nick: Optional[str], *, reason: str = ""
    ) -> Dict:
        return await self.edit_member(guild_id, user_id, nick=nick, reason=reason)

    async def add_member_role(
        self, guild_id: str, user_id: str, role_id: str, *, reason: str = ""
    ) -> None:
        params = {"reason": reason} if reason else None
        await self._http.put(
            f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", params=params
        )

    async def remove_member_role(
        self, guild_id: str, user_id: str, role_id: str, *, reason: str = ""
    ) -> None:
        params = {"reason": reason} if reason else None
        await self._http.delete(
            f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", params=params
        )

    async def kick_member(self, guild_id: str, user_id: str, *, reason: str = "") -> None:
        params = {"reason": reason} if reason else None
        await self._http.delete(f"/guilds/{guild_id}/members/{user_id}", params=params)

    async def ban_member(
        self,
        guild_id: str,
        user_id: str,
        *,
        reason: str = "",
        delete_message_seconds: int = 0,
    ) -> None:
        params = {"reason": reason} if reason else None
        await self._http.put(
            f"/guilds/{guild_id}/bans/{user_id}",
            json={"delete_message_seconds": delete_message_seconds},
            params=params,
        )

    async def unban_member(self, guild_id: str, user_id: str, *, reason: str = "") -> None:
        params = {"reason": reason} if reason else None
        await self._http.delete(f"/guilds/{guild_id}/bans/{user_id}", params=params)

    async def timeout_member(
        self,
        guild_id: str,
        user_id: str,
        duration: int,
        *,
        reason: str = "",
    ) -> Dict:
        """Timeout (mute) a member for ``duration`` seconds."""
        until = (
            datetime.datetime.utcnow() + datetime.timedelta(seconds=duration)
        ).isoformat() + "Z"
        return await self.edit_member(
            guild_id, user_id,
            communication_disabled_until=until,
            reason=reason,
        )

    async def remove_timeout(self, guild_id: str, user_id: str, *, reason: str = "") -> Dict:
        return await self.edit_member(
            guild_id, user_id,
            communication_disabled_until=None,
            reason=reason,
        )

    # =========================================================================
    #  WEBHOOKS
    # =========================================================================

    async def get_webhook(self, webhook_id: str) -> Dict:
        return await self._http.get(f"/webhooks/{webhook_id}")

    async def execute_webhook(
        self,
        webhook_id: str,
        webhook_token: str,
        *,
        content: str = "",
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
        embed: Optional[Any] = None,
        embeds: Optional[List[Any]] = None,
        wait: bool = False,
    ) -> Optional[Dict]:
        payload: Dict[str, Any] = {"content": content}
        if username:
            payload["username"] = username
        if avatar_url:
            payload["avatar_url"] = avatar_url
        if embed:
            payload["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        if embeds:
            payload["embeds"] = [e.to_dict() if hasattr(e, "to_dict") else e for e in embeds]
        params = {"wait": "true"} if wait else None
        return await self._http.post(
            f"/webhooks/{webhook_id}/{webhook_token}",
            json=payload,
            params=params,
        )

    async def edit_webhook_message(
        self,
        webhook_id: str,
        webhook_token: str,
        message_id: str,
        *,
        content: Optional[str] = None,
        embed: Optional[Any] = None,
    ) -> Dict:
        payload: Dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embed:
            payload["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        return await self._http.patch(
            f"/webhooks/{webhook_id}/{webhook_token}/messages/{message_id}",
            json=payload,
        )

    # =========================================================================
    #  USERS
    # =========================================================================

    async def get_current_user(self) -> Dict:
        return await self._http.get("/users/@me")

    async def get_user(self, user_id: str) -> Dict:
        return await self._http.get(f"/users/{user_id}")

    async def create_dm(self, user_id: str) -> Dict:
        return await self._http.post("/users/@me/channels", json={"recipient_id": user_id})

    async def send_dm(self, user_id: str, content: str = "", *, embed: Optional[Any] = None) -> Dict:
        dm = await self.create_dm(user_id)
        return await self.send_message(dm["id"], content, embed=embed)

    # =========================================================================
    #  AUDIT LOG
    # =========================================================================

    async def get_audit_log(
        self,
        guild_id: str,
        *,
        user_id: Optional[str] = None,
        action_type: Optional[int] = None,
        before: Optional[str] = None,
        limit: int = 50,
    ) -> Dict:
        params: Dict[str, Any] = {"limit": min(limit, 100)}
        if user_id:
            params["user_id"] = user_id
        if action_type is not None:
            params["action_type"] = action_type
        if before:
            params["before"] = before
        return await self._http.get(f"/guilds/{guild_id}/audit-logs", params=params)

    # =========================================================================
    #  EMOJI
    # =========================================================================

    async def list_guild_emojis(self, guild_id: str) -> List[Dict]:
        return await self._http.get(f"/guilds/{guild_id}/emojis")

    async def get_guild_emoji(self, guild_id: str, emoji_id: str) -> Dict:
        return await self._http.get(f"/guilds/{guild_id}/emojis/{emoji_id}")

    async def create_guild_emoji(
        self,
        guild_id: str,
        name: str,
        image_data: str,   # base64-encoded data URI: "data:image/png;base64,..."
        *,
        roles: Optional[List[str]] = None,
        reason: str = "",
    ) -> Dict:
        params = {"reason": reason} if reason else None
        return await self._http.post(
            f"/guilds/{guild_id}/emojis",
            json={"name": name, "image": image_data, "roles": roles or []},
            params=params,
        )

    async def delete_guild_emoji(self, guild_id: str, emoji_id: str, *, reason: str = "") -> None:
        params = {"reason": reason} if reason else None
        await self._http.delete(f"/guilds/{guild_id}/emojis/{emoji_id}", params=params)

    # =========================================================================
    #  THREADS
    # =========================================================================

    async def start_thread_from_message(
        self,
        channel_id: str,
        message_id: str,
        *,
        name: str,
        auto_archive_duration: int = 1440,
        reason: str = "",
    ) -> Dict:
        params = {"reason": reason} if reason else None
        return await self._http.post(
            f"/channels/{channel_id}/messages/{message_id}/threads",
            json={"name": name, "auto_archive_duration": auto_archive_duration},
            params=params,
        )

    async def start_thread(
        self,
        channel_id: str,
        *,
        name: str,
        type: int = 11,   # 11 = GUILD_PUBLIC_THREAD
        auto_archive_duration: int = 1440,
        invitable: bool = True,
    ) -> Dict:
        return await self._http.post(
            f"/channels/{channel_id}/threads",
            json={
                "name": name,
                "type": type,
                "auto_archive_duration": auto_archive_duration,
                "invitable": invitable,
            },
        )

    async def join_thread(self, thread_id: str) -> None:
        await self._http.put(f"/channels/{thread_id}/thread-members/@me")

    async def leave_thread(self, thread_id: str) -> None:
        await self._http.delete(f"/channels/{thread_id}/thread-members/@me")

    async def add_thread_member(self, thread_id: str, user_id: str) -> None:
        await self._http.put(f"/channels/{thread_id}/thread-members/{user_id}")

    async def remove_thread_member(self, thread_id: str, user_id: str) -> None:
        await self._http.delete(f"/channels/{thread_id}/thread-members/{user_id}")

    async def list_public_archived_threads(
        self, channel_id: str, *, limit: int = 50
    ) -> Dict:
        return await self._http.get(
            f"/channels/{channel_id}/threads/archived/public",
            params={"limit": min(limit, 100)},
        )

    # =========================================================================
    #  INTERACTIONS (follow-up helpers)
    # =========================================================================

    async def get_original_response(self, application_id: str, interaction_token: str) -> Dict:
        return await self._http.get(
            f"/webhooks/{application_id}/{interaction_token}/messages/@original"
        )

    async def edit_original_response(
        self,
        application_id: str,
        interaction_token: str,
        *,
        content: Optional[str] = None,
        embed: Optional[Any] = None,
        components: Optional[List[Any]] = None,
    ) -> Dict:
        payload: Dict[str, Any] = {}
        if content is not None:
            payload["content"] = content
        if embed:
            payload["embeds"] = [embed.to_dict() if hasattr(embed, "to_dict") else embed]
        if components is not None:
            payload["components"] = [c.to_dict() if hasattr(c, "to_dict") else c for c in components]
        return await self._http.patch(
            f"/webhooks/{application_id}/{interaction_token}/messages/@original",
            json=payload,
        )

    async def delete_original_response(
        self, application_id: str, interaction_token: str
    ) -> None:
        await self._http.delete(
            f"/webhooks/{application_id}/{interaction_token}/messages/@original"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _encode_emoji(emoji: str) -> str:
    """URL-encode an emoji string for reaction endpoints."""
    import urllib.parse
    return urllib.parse.quote(emoji, safe="")
