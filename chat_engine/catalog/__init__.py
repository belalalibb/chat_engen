"""Canonical model identity, aliases, CatalogIndex and resolver (ADR-CE-0001 D1)."""

from chat_engine.catalog.contracts import (
    AliasDecision,
    AliasSource,
    CanonicalModel,
    CatalogEntry,
    CatalogPage,
    ModelAlias,
)
from chat_engine.catalog.index import CatalogIndex
from chat_engine.catalog.service import AliasNotFound, AliasUnconfirmed, CatalogService

__all__ = [
    "AliasDecision",
    "AliasNotFound",
    "AliasSource",
    "AliasUnconfirmed",
    "CanonicalModel",
    "CatalogEntry",
    "CatalogIndex",
    "CatalogPage",
    "CatalogService",
    "ModelAlias",
]
