## 2026-03-10

- `addon/oig-proxy/correlation_id.py` reached full statement coverage by explicitly testing both normal and fallback branches in `correlation_id_context_frame` (bytes, str, None, and exception-producing frame objects).
- Decorator branch coverage required separate tests for: (a) no existing context ID (set/reset path), (b) existing context ID (passthrough), and (c) exception path cleanup.
- Logging helpers are best verified by asserting `LogRecord` attributes (presence of `correlation_id`) in addition to message text.
