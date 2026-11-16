"""AsyncAPI WebSocket client generation with ``allOf``, schema metadata, and ``oneOf`` subprotocols."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType, InputFileType, generate

if TYPE_CHECKING:
    from pathlib import Path


def test_asyncapi_websocket_query_headers_allof_metadata_and_oneof_subprotocols(tmp_path: Path) -> None:
    """Keep channels with ``allOf``, JSON Schema metadata, and ``oneOf`` ``Sec-WebSocket-Protocol`` consts."""
    spec = """
asyncapi: 3.0.0
info:
  title: Handshakes
  version: 1.0.0
servers:
  main:
    host: example.com
    protocol: wss
operations:
  pagedSend:
    action: send
    channel:
      $ref: '#/channels/paged'
  documentedSend:
    action: send
    channel:
      $ref: '#/channels/documented'
  chatSend:
    action: send
    channel:
      $ref: '#/channels/chat'
  lobbySend:
    action: send
    channel:
      $ref: '#/channels/lobby'
channels:
  paged:
    address: paged
    bindings:
      ws:
        query:
          allOf:
            - $ref: '#/components/schemas/Paging'
            - type: object
              properties:
                room:
                  type: string
              required: [room]
    messages:
      m:
        contentType: text/plain
        payload:
          type: string
  documented:
    address: documented
    bindings:
      ws:
        headers:
          type: object
          $schema: http://json-schema.org/draft-07/schema#
          examples:
            - X-Id: a
          properties:
            X-Id:
              type: string
    messages:
      m:
        contentType: text/plain
        payload:
          type: string
  chat:
    address: chat
    bindings:
      ws:
        headers:
          type: object
          properties:
            Sec-WebSocket-Protocol:
              oneOf:
                - const: chat.v1
                - const: chat.v2
    messages:
      m:
        contentType: text/plain
        payload:
          type: string
  lobby:
    address: lobby
    bindings:
      ws:
        headers:
          type: object
          properties:
            Sec-WebSocket-Protocol:
              oneOf:
                - const: lobby.v1
                - const: lobby.v2
          required: [Sec-WebSocket-Protocol]
    messages:
      m:
        contentType: text/plain
        payload:
          type: string
components:
  schemas:
    Paging:
      type: object
      properties:
        limit:
          type: integer
"""
    models_out = tmp_path / "models.py"
    client_out = tmp_path / "client"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        generate(
            spec,
            input_file_type=InputFileType.AsyncAPI,
            output=models_out,
            output_model_type=DataModelType.PydanticV2BaseModel,
            generate_client="httpx2",
            client_output=client_out,
            client_package="client",
            client_model_package="models",
        )

    skip_warnings = [str(w.message) for w in caught if "Skipping channel" in str(w.message)]
    assert skip_warnings == []

    plans_code = (client_out / "protocols" / "_plans.py").read_text(encoding="utf-8")
    assert "'chat.v1'" in plans_code
    assert "'chat.v2'" in plans_code
    assert "'lobby.v1'" in plans_code
    assert "'lobby.v2'" in plans_code

    operations_code = (client_out / "_operations.py").read_text(encoding="utf-8")
    assert "limit" in operations_code
    assert "room" in operations_code
    assert "X-Id" in operations_code
