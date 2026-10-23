"""The connectors, one per kind of source, by name. Adding a source means adding a Connector here."""

from .base import Command, Connector, Document, Downloaded
from .mail import Mail

CONNECTORS = {c.name: c for c in (Mail,)}

__all__ = ["CONNECTORS", "Command", "Connector", "Document", "Downloaded"]
