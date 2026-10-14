"""Build a ready-to-run connector for an integrations row (credentials decrypted via app.crypto)."""
from __future__ import annotations

from .base import Connector
from .credentials import decrypt_credentials
from .errors import ConfigError
from .google_ads import GoogleAdsConnector
from .httpclient import HttpClient
from .meta import MetaInsightsConnector
from .shiprocket import ShiprocketConnector
from .shopify import ShopifyConnector
from .store import Store

CONNECTORS: dict[str, type[Connector]] = {
    "shopify": ShopifyConnector, "shiprocket": ShiprocketConnector,
    "meta": MetaInsightsConnector, "google_ads": GoogleAdsConnector,
}  # provider 'csv' is upload-driven: use connectors.csv_import, not a scheduled sync


def build_connector(store: Store, integration_id: str, credentials_key_b64: str, *, aad: bytes = b"",
                    transport=None, **connector_kwargs) -> Connector:
    integ = store.get_integration(integration_id)
    cls = CONNECTORS.get(integ.provider)
    if cls is None:
        raise ConfigError(f"no scheduled connector for provider '{integ.provider}'", provider=integ.provider)
    creds = decrypt_credentials(integ.credentials_enc, credentials_key_b64, aad, integ.provider)
    http = HttpClient(provider=integ.provider, transport=transport, secrets=creds.secret_values())
    c = cls(integ, creds, store, http, **connector_kwargs)
    if hasattr(c, "classify"):
        http._classify = c.classify
    return c
