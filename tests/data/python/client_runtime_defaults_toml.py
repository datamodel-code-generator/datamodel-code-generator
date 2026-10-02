"""Exercise exact runtime-default TOML tags and diagnostic ownership through the config loader."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.data.python.client_generation import defaults_load, defaults_project

if TYPE_CHECKING:
    from pathlib import Path


_VALID = {
    "timeout": "disabled",
    "timeout.connect": "disabled",
    "timeout.read": "disabled",
    "timeout.write": "disabled",
    "timeout.pool": "disabled",
    "compression": "disabled",
    "total_timeout": "unlimited",
    "max_network_sends": "unlimited",
    "max_response_bytes": "unlimited",
    "max_stream_bytes": "unlimited",
    "stream_idle_timeout": "unlimited",
    "stream_total_timeout": "unlimited",
    "retry.max_retry_after": "unlimited",
}
_BAD = {
    "root mode": 'mode="disabled"',
    "root conflict": 'mode="disabled"\ntimeout={read=1}',
    "root extra": 'mode="disabled"\nsecret=1',
    "unknown": "unknown=1",
    "unknown nested": "retry={secret=1}",
    "vendor": 'retry={retry_after_ms_header="X-Retry"}',
    "auth": "auth={}",
    "protocol": "max_items=1",
    "deadline": "deadline=1",
    "provider": "content_coding_factories={}",
    "wrong disabled": 'total_timeout={mode="disabled"}',
    "wrong unlimited": 'timeout={mode="unlimited"}',
    "retry tag": 'retry={mode="disabled"}',
    "redirect tag": 'redirects={mode="disabled"}',
    "error tag": 'max_error_body_bytes={mode="unlimited"}',
    "cleanup tag": 'cleanup_timeout={mode="disabled"}',
    "unknown mode": 'compression={mode="off"}',
    "mode value": 'compression={mode="disabled",value=1}',
    "mode fields": 'timeout={mode="disabled",read=1}',
    "mode extra": 'timeout={mode="disabled",secret=1}',
    "string null": 'timeout="null"',
    "string None": 'compression="None"',
    "count bool": "max_network_sends=true",
    "count fractional": "max_stream_bytes=1.5",
    "infinite": "total_timeout=inf",
    "nan": "stream_total_timeout=nan",
    "phase bool": "timeout={read=true}",
    "error none": 'max_error_body_bytes="null"',
    "cleanup zero": "cleanup_timeout=0",
    "duplicate statuses": "retry={statuses=[503,503]}",
    "bool statuses": "retry={statuses=[true]}",
    "statuses type": "retry={statuses=503}",
    "origins type": 'redirects={allowed_origins="https://a.example"}',
    "origins bad": 'redirects={allowed_origins=["https://a.example/path"]}',
    "delay ordering": "retry={initial_delay=1,max_delay=0}",
    "effective ordering": "retry={max_delay=0}",
}


def defaults_toml_report(root: Path) -> str:
    """Observe each legal None tag and invalid closed-table vector independently."""
    lines = ["# runtime defaults TOML"]
    header = 'schema_version=1\noutput="client"\npackage="client"\nmodel_package="models"\n[runtime_defaults]\n'
    path = root / "defaults.toml"
    for name, mode in _VALID.items():
        parent, _, field = name.partition(".")
        entry = f'{parent}={{{field}={{mode="{mode}"}}}}' if field else f'{name}={{mode="{mode}"}}'
        path.write_text(header + entry + "\n")
        artifacts = defaults_project(root, defaults_load(path))
        manifest = json.loads(artifacts["client/.dcg-target-manifest.json"])
        lines.append(
            f"valid {name} {json.dumps(manifest['inputs']['target_config']['runtime_defaults'], sort_keys=True)}"
        )
    for label, entry in _BAD.items():
        path.write_text(header + entry + "\n")
        try:
            defaults_load(path)
        except Exception as error:  # ruff: ignore[blind-except] - Record the public config loader failure.
            lines.append(f"invalid {label} " + " ".join(f"{d.code} {d.option_path}" for d in error.diagnostics))
        else:
            lines.append(f"invalid {label} ACCEPTED")
    return "\n".join(lines) + "\n"
