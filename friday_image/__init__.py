"""Quiet Image child application for Friday's Image Generation panel.

The default backend performs deterministic local art rendering with no
network calls. A real, explicit Cloudflare Workers AI adapter is available
(providers.py) and is activated only when the user adds their own key under
Settings -> Image Generation; nothing is inferred or faked either way.
"""
