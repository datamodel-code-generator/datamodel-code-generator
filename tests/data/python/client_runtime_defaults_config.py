"""Observe sparse generation defaults through configuration, rendering and publication."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields
from typing import TYPE_CHECKING

from tests.data.python.client_generation import (
    default_record_types,
    defaults_load,
    defaults_project,
    runtime_defaults,
    runtime_defaults_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def defaults_config_report(root: Path) -> str:
    """Report the complete inventory, canonical projection and configuration refusal vectors."""
    lines = ["# runtime defaults"]
    for kind in default_record_types():
        value = kind()
        try:
            setattr(value, fields(kind)[0].name, 1)
        except FrozenInstanceError:
            frozen = True
        lines.append(
            f"{kind.__name__} fields {[item.name for item in fields(kind)]} "
            f"UNSET {all(getattr(value, item.name) is item.default for item in fields(kind))} "
            f"slots {not hasattr(value, '__dict__')} frozen {frozen}"
        )
        try:
            kind(1)
        except TypeError:
            lines.append(f"{kind.__name__} positional TypeError")
    complete = {
        "timeout": {"connect": 1, "read": 2, "write": 3, "pool": None},
        "retry": {
            "max_retries": 0,
            "initial_delay": 0,
            "max_delay": 0,
            "jitter": "none",
            "statuses": [503, 429],
            "max_retry_after": None,
            "respect_retry_after": False,
            "retry_on_pool_timeout": True,
        },
        "redirects": {
            "enabled": True,
            "max_redirects": 0,
            "allow_303_to_get": True,
            "allowed_origins": ["https://EXAMPLE.com:443", "http://[::1]:80"],
            "allow_https_downgrade": False,
        },
        "total_timeout": None,
        "max_network_sends": 0,
        "max_response_bytes": 0,
        "max_error_body_bytes": 1,
        "max_stream_bytes": 0,
        "stream_idle_timeout": None,
        "stream_total_timeout": 0,
        "cleanup_timeout": 1,
        "compression": "gzip",
    }
    config = runtime_defaults_config(root, runtime_defaults(complete))
    artifacts = defaults_project(root, config)
    manifest = json.loads(artifacts["client/.dcg-target-manifest.json"])
    node = manifest["inputs"]["target_config"]["runtime_defaults"]
    lines.extend((
        f"projection {json.dumps(node, sort_keys=True)}",
        f"reference {manifest['target_data']['client']['runtime_defaults_ref']}",
        f"public duplicate {'runtime_defaults' in manifest['inputs']['target_config']['public_options']}",
        f"shared module {'client/_generated/runtime_defaults.py' in artifacts}",
    ))
    for label, default in (
        ("omitted", runtime_defaults({})),
        ("empty nested", runtime_defaults({"timeout": {}, "retry": {}, "redirects": {}})),
        ("equal", runtime_defaults({"timeout": {"read": 30}})),
        ("disabled", runtime_defaults({"timeout": None, "compression": None})),
    ):
        data = defaults_project(root, runtime_defaults_config(root, default))
        record = json.loads(data["client/.dcg-target-manifest.json"])
        lines.append(
            f"{label} {json.dumps(record['inputs']['target_config']['runtime_defaults'], sort_keys=True)} "
            f"module {'client/_generated/runtime_defaults.py' in data} "
            f"models same {data['models.py'] == artifacts['models.py']}"
        )
    toml = root / "defaults.toml"
    toml.write_text(
        'schema_version=1\noutput="client"\npackage="client"\nmodel_package="models"\n'
        '[runtime_defaults]\ntotal_timeout={mode="unlimited"}\nmax_network_sends=0\n'
        "max_response_bytes=0\nmax_error_body_bytes=1\nmax_stream_bytes=0\n"
        'stream_idle_timeout={mode="unlimited"}\nstream_total_timeout=0\ncleanup_timeout=1\ncompression="gzip"\n'
        '[runtime_defaults.timeout]\nconnect=1\nread=2\nwrite=3\npool={mode="disabled"}\n'
        '[runtime_defaults.retry]\nmax_retries=0\ninitial_delay=0\nmax_delay=0\njitter="none"\n'
        'statuses=[503,429]\nmax_retry_after={mode="unlimited"}\nrespect_retry_after=false\nretry_on_pool_timeout=true\n'
        "[runtime_defaults.redirects]\nenabled=true\nmax_redirects=0\nallow_303_to_get=true\n"
        'allowed_origins=["https://EXAMPLE.com:443","http://[::1]:80"]\nallow_https_downgrade=false\n'
    )
    other = defaults_project(root, defaults_load(toml))
    lines.append(f"Python TOML artifacts identical {artifacts == other}")
    _invalid(root, lines)
    _manifest_states(root, config, lines)
    return "\n".join(lines) + "\n"


def _invalid(root: Path, lines: list[str]) -> None:
    """Report every field's refusal and effective nested retry ordering."""
    bad = {
        "timeout": True,
        "total_timeout": True,
        "retry": None,
        "redirects": None,
        "max_network_sends": 1.5,
        "max_response_bytes": -1,
        "max_error_body_bytes": 0,
        "max_stream_bytes": True,
        "stream_idle_timeout": float("inf"),
        "stream_total_timeout": float("nan"),
        "cleanup_timeout": None,
        "compression": "br",
        **{f"timeout.{name}": True for name in ("connect", "read", "write", "pool")},
        "retry.max_retries": True,
        "retry.initial_delay": -1,
        "retry.max_delay": -1,
        "retry.jitter": "random",
        "retry.statuses": frozenset({200}),
        "retry.max_retry_after": 0,
        "retry.respect_retry_after": 1,
        "retry.retry_on_pool_timeout": "yes",
        "redirects.enabled": 1,
        "redirects.max_redirects": 0.5,
        "redirects.allow_303_to_get": 1,
        "redirects.allowed_origins": ("https://a.example/path",),
        "redirects.allow_https_downgrade": 1,
        "retry.initial_delay effective": 61,
        "retry.max_delay effective": 0,
    }
    for path, value in bad.items():
        key = path.removesuffix(" effective")
        name, _, field = key.partition(".")
        candidate = {name: {field: value}} if field else {name: value}
        try:
            runtime_defaults_config(root, runtime_defaults(candidate))
        except Exception as error:  # ruff: ignore[blind-except] - Record the public configuration/publication failure.
            lines.append(f"invalid {path} " + " ".join(f"{d.code} {d.option_path}" for d in error.diagnostics))
        else:
            lines.append(f"invalid {path} ACCEPTED")


def _manifest_states(root: Path, config: object, lines: list[str]) -> None:
    """Refuse independently corrupted saved-state inputs on public regeneration."""
    defaults_project(root, config, publish=True)
    saved = root / "client/.dcg-target-manifest.json"
    original = saved.read_bytes()
    for label, change in (
        ("wrong reference", lambda m: m["target_data"]["client"].update(runtime_defaults_ref="/inputs/nope")),
        ("missing defaults", lambda m: m["inputs"]["target_config"].pop("runtime_defaults")),
        ("duplicate defaults", lambda m: m["inputs"]["target_config"]["public_options"].update(runtime_defaults={})),
        ("unknown defaults", lambda m: m["inputs"]["target_config"]["runtime_defaults"].update(auth="secret")),
        ("boolean count", lambda m: m["inputs"]["target_config"]["runtime_defaults"].update(max_stream_bytes=True)),
        (
            "unsorted statuses",
            lambda m: m["inputs"]["target_config"]["runtime_defaults"]["retry"].update(statuses=[503, 429]),
        ),
        (
            "tag in manifest",
            lambda m: m["inputs"]["target_config"]["runtime_defaults"].update(timeout={"mode": "disabled"}),
        ),
        ("missing client", lambda m: m["target_data"].pop("client")),
        ("missing config", lambda m: m["inputs"].pop("target_config")),
    ):
        corrupted = json.loads(original)
        change(corrupted)
        saved.write_text(json.dumps(corrupted))
        try:
            defaults_project(root, config, publish=True)
        except Exception as error:  # ruff: ignore[blind-except] - Record the public configuration/publication failure.
            lines.append(f"manifest {label} " + " ".join(d.code for d in error.diagnostics))
        else:
            lines.append(f"manifest {label} ACCEPTED")
        finally:
            saved.write_bytes(original)
