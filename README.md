# DisKord
A feature-rich async Discord bot library for Python — slash commands, components, AutoMod, state machines, and more.
![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.4.0-5865F2?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)
![aiohttp](https://img.shields.io/badge/built%20with-aiohttp-orange?style=flat-square)


# pydisk

A feature-rich Discord bot library built directly on the REST API and Gateway.

```python
import pydisk

bot = pydisk.Client(token="YOUR_TOKEN", prefix="!")

@bot.slash_command(description="Say hello")
async def hello(interaction: pydisk.Interaction):
    await interaction.respond("Hello! 👋")

bot.run()
```

---

## Installation

**Minimum (core only):**
```bash
pip install pydisk
```

**With Pydantic validation:**
```bash
pip install pydisk[validation]
```

**With PDF generation:**
```bash
pip install pydisk[pdf]
```

**Everything:**
```bash
pip install pydisk[all]
```

> Requires **Python 3.10+**

---

## Quick Examples

### Slash command
```python
@bot.slash_command(description="Roll a die")
async def roll(interaction: pydisk.Interaction, sides: int = 6):
    import random
    await interaction.respond(f"🎲 You rolled a {random.randint(1, sides)}!")
```

### Prefix command
```python
@bot.prefix_command(aliases=["h"])
async def help(message: pydisk.Message):
    await bot.send_message(message.channel_id, "Here's the help text!")
```

### Command group (subcommands)
```python
config = bot.group("config", "Server configuration")

@config.command(description="Set the log channel")
async def log(interaction, channel_id: str):
    await interaction.respond(f"Log channel set to <#{channel_id}>")
```

### Buttons & components
```python
from pydisk import AppRouter, ComponentBuilder, ButtonStyle

router = AppRouter()
bot.mount_router(router)

@bot.slash_command(description="Ask a question")
async def confirm(interaction):
    rows = (
        ComponentBuilder()
        .button("✅ Yes", custom_id="answer:yes", style=ButtonStyle.SUCCESS)
        .button("❌ No",  custom_id="answer:no",  style=ButtonStyle.DANGER)
        .build()
    )
    await interaction.respond("Are you sure?", components=rows)

@router.button("answer:{choice}")
async def on_answer(inter):
    await inter.respond(f"You picked: {inter.params['choice']}", ephemeral=True)
```

### Cogs (class-based commands)
```python
class Moderation(pydisk.Cog):
    @pydisk.command(description="Kick a user")
    async def kick(self, interaction, user_id: str):
        await bot.kick_member(interaction.guild_id, user_id)
        await interaction.respond(f"Kicked <@{user_id}>")

bot.add_cog(Moderation())
```

### Background tasks
```python
import asyncio

@bot.background_task(name="status_loop")
async def update_status():
    while True:
        await bot.change_presence(activity="the server", activity_type=3)
        await asyncio.sleep(60)
```

### Sending files
```python
import io

@bot.slash_command(description="Send a text file")
async def sendfile(interaction):
    await interaction.defer()
    content = io.BytesIO(b"Hello from pydisk!")
    await bot.http.post(
        f"/channels/{interaction.channel_id}/messages",
        data={"content": "Here you go!"},
        files=[("hello.txt", content, "text/plain")],
    )
```

---

## Features

| Feature | Status |
|---|---|
| Slash commands + groups | ✅ |
| Prefix commands | ✅ |
| Cogs (class-based) | ✅ |
| Buttons & select menus | ✅ |
| Modals | ✅ |
| Embeds (full-featured) | ✅ |
| Auto-moderation engine | ✅ |
| State machines | ✅ |
| i18n / Translations | ✅ |
| Event middleware pipeline | ✅ |
| Smart context (slash + prefix unified) | ✅ |
| File / attachment uploads | ✅ |
| PDF generation | ✅ (optional) |
| Pydantic validation | ✅ (optional) |
| Gateway (WebSocket) with auto-reconnect | ✅ |
| Rate limit handling | ✅ |

---

## Project Structure

```
pydisk/
├── __init__.py          # Public API surface
├── client.py            # Main Client class
├── cog.py               # Cog base class
├── commands/            # Command framework
├── components.py        # Buttons, modals, routers
├── core/
│   ├── rest.py          # HTTP client
│   ├── gateway.py       # WebSocket gateway
│   └── async_utils.py   # TaskGroup, EventBus, etc.
├── models/              # User, Message, Interaction, Embed
├── embed.py             # Rich embed builder
├── automod.py           # Auto-moderation engine
├── statemachine.py      # Multi-step interaction flows
├── events.py            # EventEmitter
├── middleware.py        # Event pipeline + SmartRouter
├── i18n.py              # Translations
├── validation.py        # Input validation (+ Pydantic)
├── smart.py             # SmartContext / SmartResponder
├── html_embed.py        # HTML → Embed parser
├── api.py               # High-level API helpers
└── pdf_gen.py           # PDF generation (optional)
```

---

## Environment Variables

Never hardcode your token. Use a `.env` file (already in `.gitignore`):

```env
DISCORD_TOKEN=your_token_here
DISCORD_APP_ID=your_application_id_here
```

```python
import os
bot = pydisk.Client(token=os.environ["DISCORD_TOKEN"])
```

---

## License

MIT © Moro
