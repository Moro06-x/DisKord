# Changelog

## [0.4.0] — 2025

### Added
- `AppRouter` with pattern-based button, select, and modal routing
- `AutoMod` engine with spam/filter/rate detection and auto-actions
- `StateMachine` for multi-step Discord interaction flows
- `EmbedPaginator` — split long field lists across multiple embeds
- `SmartContext` / `SmartResponder` — unified slash + prefix handling
- `EventEmitter` with wildcard support (`"*"`, `"message.*"`)
- `EventPipeline` middleware + `SmartRouter` context/content routing
- `Translations` / `i18n` — automatic locale detection from interactions
- `HTMLEmbedParser` — parse HTML fragments into Discord embeds
- `PDFStyle` + `generate_pdf` — styled PDF generation (optional dep)
- `APIClient` — high-level typed wrappers for all major REST endpoints
- File upload support in `HTTPClient` (multipart/form-data)
- `BackgroundTask` with exponential back-off and graceful shutdown
- `RateSemaphore` token-bucket rate limiter
- `TaskGroup` structured-concurrency helper (3.10-compatible)
- `validated_dataclass` + `CommandInput` — Pydantic-optional validation
- Per-route and global rate limit handling with automatic retries
- Gateway zombie connection detection

### Fixed
- `Interaction.followup()` used wrong webhook endpoint (`/webhooks/me/...`)
- `asyncio.get_event_loop()` replaced with `get_running_loop()` throughout
- `BackgroundTask` backoff sleep used an unresolvable Future
- `async_timeout` fallback on Python 3.10 cancelled incorrectly
- `guild_only()` / `has_role()` check logic
- `sync_commands()` deduplication applied correctly to both commands and groups
- Duplicate `modal()` method definition in `Client`
- Gateway zlib decompression now uses a persistent per-connection decompressor
- HTTP client reads response body exactly once inside the context manager

## [0.3.0] — Earlier
- Initial slash command + prefix command framework
- Gateway WebSocket client with heartbeat and resume
- Component and modal builders
- Basic embed builder
