"""Bedrock service tier of the segment analysis calls (BEDROCK_SERVICE_TIER).

The pipeline sets 'flex' (half the standard price, for background work).
Unset: the standard tier, and no tier field is sent.
"""
import os


def service_tier() -> str:
    """The tier to request ('flex'), or '' for the standard tier."""
    return os.environ.get('BEDROCK_SERVICE_TIER', '').strip()


def converse_fields() -> dict:
    """Extra Converse request fields: {'serviceTier': {'type': tier}}, or {}."""
    tier = service_tier()
    return {'serviceTier': {'type': tier}} if tier else {}
