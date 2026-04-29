"""CRM adapter dispatch.

The active CRM is selected by the CRM_PROVIDER env var:
  attio | hubspot | zoho | salesforce | pipedrive | local

`local` (default) means "use Roost's own contacts table" — no external
CRM. This keeps the API uniform whether or not a CRM is connected.

Usage:
    from roost.services.crm import get_provider
    crm = get_provider()
    p = crm.find_person(email="jane@example.com")
"""

from __future__ import annotations

import logging
from functools import lru_cache

from roost.services.crm.base import (
    CrmAuthError,
    CrmConfigError,
    CrmError,
    CrmNotFoundError,
    CrmProvider,
    Deal,
    Org,
    Person,
)

__all__ = [
    "get_provider",
    "reset_provider_cache",
    "CrmProvider",
    "Person",
    "Org",
    "Deal",
    "CrmError",
    "CrmConfigError",
    "CrmAuthError",
    "CrmNotFoundError",
]

logger = logging.getLogger("roost.services.crm")


@lru_cache(maxsize=4)
def _build(provider: str) -> CrmProvider:
    p = provider.strip().lower()
    if p == "attio":
        from roost.services.crm.attio import AttioProvider
        return AttioProvider()
    if p == "hubspot":
        from roost.services.crm.hubspot import HubspotProvider
        return HubspotProvider()
    if p == "zoho":
        from roost.services.crm.zoho import ZohoProvider
        return ZohoProvider()
    if p == "salesforce":
        from roost.services.crm.salesforce import SalesforceProvider
        return SalesforceProvider()
    if p == "pipedrive":
        from roost.services.crm.pipedrive import PipedriveProvider
        return PipedriveProvider()
    if p == "local":
        from roost.services.crm.local import LocalProvider
        return LocalProvider()
    raise CrmConfigError(f"unknown CRM_PROVIDER: {provider}")


def get_provider(provider: str | None = None) -> CrmProvider:
    """Return the active CRM provider. Resolution order:
    1. explicit `provider=` arg (tests, scoped overrides)
    2. DB setting "crm_provider" (set by the settings page)
    3. CRM_PROVIDER env var
    4. "local" default
    """
    if provider is None:
        try:
            from roost.services.settings import get_setting
            provider = get_setting("crm_provider")
        except Exception:
            provider = None
    if not provider:
        from roost.config import CRM_PROVIDER
        provider = CRM_PROVIDER
    return _build(provider)


def reset_provider_cache() -> None:
    """Drop cached provider instances. Call after credential changes."""
    _build.cache_clear()
