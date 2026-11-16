"""Regression tests for AsyncAPI handshake `$ref` sibling handling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator.__main__ import Exit, main

if TYPE_CHECKING:
    from pathlib import Path


def test_asyncapi_v2_handshake_drops_ref_sibling_defaults_with_ignore_ref_siblings(tmp_path: Path) -> None:
    """Drop sibling defaults beside `$ref` in AsyncAPI v2 handshake under `--ignore-ref-siblings-by-schema-version`."""
    spec = tmp_path / "ticker.yaml"
    spec.write_text(
        """asyncapi: 2.6.0
info:
  title: Ticker stream
  version: 1.0.0
servers:
  live:
    url: wss://ticker.example.com
    protocol: wss
channels:
  /ticks/{market}:
    parameters:
      market:
        schema:
          type: string
    bindings:
      ws:
        query:
          type: object
          properties:
            token:
              $ref: '#/components/schemas/Token'
              default: abc
        headers:
          type: object
          properties:
            X-Client:
              $ref: '#/components/schemas/ClientName'
              default: me
    subscribe:
      message:
        payload:
          $ref: '#/components/schemas/Tick'
components:
  schemas:
    Token:
      type: string
    ClientName:
      type: string
      maxLength: 32
    Tick:
      type: object
      properties:
        price:
          type: number
      required: [price]
""",
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    code = main(
        [
            "--input",
            str(spec),
            "--input-file-type",
            "asyncapi",
            "--output",
            str(out_dir / "models.py"),
            "--formatters",
            "builtin",
            "--target-python-version",
            "3.11",
            "--generate-client",
            "httpx2",
            "--client-output",
            str(out_dir / "client"),
            "--client-package",
            "client",
            "--client-model-package",
            "models",
            "--ignore-ref-siblings-by-schema-version",
        ]
    )
    assert code == Exit.OK

    helpers = (out_dir / "client" / "protocols" / "_helpers.py").read_text(encoding="utf-8")
    async_helpers = (out_dir / "client" / "protocols" / "_async_helpers.py").read_text(encoding="utf-8")
    assert "= 'abc'" not in helpers
    assert "= 'me'" not in helpers
    assert "= 'abc'" not in async_helpers
    assert "= 'me'" not in async_helpers
