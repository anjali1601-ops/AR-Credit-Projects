"""Deterministic offline implementations behind the LLM interface.

These modules are what the default ``mock`` provider runs. They are rule based
on purpose: the demo has to produce the same answer every time, with no network
and no API key, while the agents stay written against the LLM interface.
"""
