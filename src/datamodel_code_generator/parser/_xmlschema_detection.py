"""Compatibility re-export for XML Schema input detection helpers."""

from __future__ import annotations

from datamodel_code_generator._xmlschema_detection import XML_SCHEMA_NAMESPACE, XML_SCHEMA_TAG, is_xml_schema_text

__all__ = ["XML_SCHEMA_NAMESPACE", "XML_SCHEMA_TAG", "is_xml_schema_text"]
