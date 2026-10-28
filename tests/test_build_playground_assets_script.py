"""Tests for generated browser playground assets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from datamodel_code_generator.arguments import arg_parser
from scripts import build_playground_assets, build_playground_release_versions
from tests.conftest import assert_output

EXPECTED_PLAYGROUND_ASSETS_PATH = Path(__file__).resolve().parent / "data" / "expected" / "playground_assets"
ROOT = Path(__file__).resolve().parents[1]


def test_option_metadata_docs_urls() -> None:
    """Playground option metadata links back to generated CLI docs."""
    actions = arg_parser._actions
    option_targets = build_playground_assets._option_target_index(actions)
    output = [
        _metadata_docs_summary("--output-model-type", actions, option_targets),
        _metadata_docs_summary("--capitalise-enum-members", actions, option_targets),
    ]

    assert_output(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        EXPECTED_PLAYGROUND_ASSETS_PATH / "option_docs_urls.txt",
    )


def test_playground_template_runtime_contract() -> None:
    """Current builds skip Jinja while older releases retain their runtime dependency."""
    metadata = build_playground_assets.build_metadata()
    custom_template_option = next(option for option in metadata["options"] if option["dest"] == "custom_template_dir")
    worker_source = (ROOT / "docs" / "assets" / "playground" / "worker.js").read_text(encoding="utf-8")
    standard_packages_marker = "const STANDARD_RUNTIME_PACKAGES = ["
    standard_packages = worker_source.partition(standard_packages_marker)[2].partition("];")[0]
    output = {
        "custom_template_option": {
            "browser_supported": custom_template_option["browser_supported"],
            "unsupported_reason": custom_template_option["unsupported_reason"],
        },
        "release_runtime_packages": {
            "compiled": build_playground_release_versions._release_version("v9.9.9", uses_compiled_templates=True).get(
                "runtime_packages", []
            ),
            "legacy": build_playground_release_versions._release_version("v0.0.1", uses_compiled_templates=False).get(
                "runtime_packages", []
            ),
        },
        "worker": {
            "current_installs_jinja2": "jinja2" in standard_packages,
            "standard_packages_declared": standard_packages_marker in worker_source,
            "merges_version_runtime_packages": (
                "[...STANDARD_RUNTIME_PACKAGES, ...activeVersion.runtimePackages, "
                "...packagesForRuntimeApp(activeVersion)]" in worker_source
            ),
        },
    }

    assert_output(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        EXPECTED_PLAYGROUND_ASSETS_PATH / "template_runtime_contract.txt",
    )


def _metadata_docs_summary(
    option: str,
    actions: list[Any],
    option_targets: dict[str, dict[str, str]],
) -> dict[str, str]:
    action = next(action for action in actions if option in action.option_strings)
    metadata = cast(
        "dict[str, Any]",
        build_playground_assets._option_metadata(action, option_targets),
    )
    return {
        "name": metadata["name"],
        "docs_url": metadata["docs_url"],
    }


def test_playground_target_options_unsupported() -> None:
    """Server and client options are generate() options that the browser playground does not offer."""
    metadata = build_playground_assets.build_metadata()
    output = {
        option["dest"]: {
            "browser_supported": option["browser_supported"],
            "unsupported_reason": option["unsupported_reason"],
        }
        for option in metadata["options"]
        if option["dest"].startswith(("generate_server", "generate_client", "server_", "client_"))
    }

    assert_output(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        EXPECTED_PLAYGROUND_ASSETS_PATH / "target_options.txt",
    )
