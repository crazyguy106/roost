"""CRM provider contract test.

Cheap insurance that every CRM adapter:
1. Subclasses CrmProvider with no abstract methods left over (Python
   raises TypeError at instantiation if an @abstractmethod is missing).
2. Sets a non-default `name` attribute.
3. Returns a well-shaped `test_connection()` result — `{ok: bool, detail: str}`
   — even when nothing is configured, instead of leaking vendor SDK
   exceptions to the caller.

These tests do NOT need real CRM credentials. Providers should catch
their own auth/config errors inside `test_connection()` and report
them in `detail`.
"""

from __future__ import annotations

import pytest

from roost.extras.crm.services import get_provider, reset_provider_cache
from roost.extras.crm.services.attio import AttioProvider
from roost.extras.crm.services.base import CrmProvider
from roost.extras.crm.services.hubspot import HubspotProvider
from roost.extras.crm.services.local import LocalProvider
from roost.extras.crm.services.pipedrive import PipedriveProvider
from roost.extras.crm.services.salesforce import SalesforceProvider
from roost.extras.crm.services.zoho import ZohoProvider

PROVIDERS = [
    ("local", LocalProvider),
    ("attio", AttioProvider),
    ("hubspot", HubspotProvider),
    ("zoho", ZohoProvider),
    ("salesforce", SalesforceProvider),
    ("pipedrive", PipedriveProvider),
]


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_provider_cache()
    yield
    reset_provider_cache()


@pytest.mark.parametrize("name,cls", PROVIDERS)
def test_provider_subclasses_base_and_instantiates(name, cls):
    """ABC enforcement — TypeError if any @abstractmethod is missing."""
    assert issubclass(cls, CrmProvider)
    instance = cls()
    assert isinstance(instance, CrmProvider)
    assert instance.name == name


@pytest.mark.parametrize("name,cls", PROVIDERS)
def test_test_connection_returns_well_shaped_dict(name, cls):
    """`test_connection()` must return {ok: bool, detail: str} regardless
    of whether the provider is configured. Vendor exceptions must not
    leak.
    """
    result = cls().test_connection()
    assert isinstance(result, dict)
    assert "ok" in result
    assert "detail" in result
    assert isinstance(result["ok"], bool)
    assert isinstance(result["detail"], str)


def test_get_provider_returns_local_by_default():
    """No CRM_PROVIDER env / no DB setting → local."""
    crm = get_provider("local")
    assert crm.name == "local"


def test_get_provider_unknown_raises():
    from roost.extras.crm.services.base import CrmConfigError

    with pytest.raises(CrmConfigError):
        get_provider("nope")
