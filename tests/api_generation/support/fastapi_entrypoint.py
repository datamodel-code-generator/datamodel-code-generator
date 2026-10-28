"""Generate servers with their service stubs, then serve their entrypoints as users replace and break the stubs.

A nested server package, `app.server`, imports its implementations from `app.services`; a top-level one, `secured`,
from the top-level `services` package, under another name for a class whose name another group's class takes. Each
step imports the entrypoint afresh and reports its application or error. The same servers run the Python examples of
their READMEs, and a generated server is served with the commands its run notice prints.
"""

from __future__ import annotations

import importlib
import json
import re
import shlex
import shutil
import sys
import urllib.request
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from datamodel_code_generator import generate
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.api_generation.support.docs_tutorial import run_python, run_tests, serve
from tests.api_generation.support.fastapi_generation import SOURCE
from tests.api_generation.support.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

USER = SOURCE / "entrypoint"
PYTHON = re.compile(r"^```python\n(.*?)^```$", re.MULTILINE | re.DOTALL)
SECURED_NAMES = {"tag:public": "service", "default": "operations"}
MODEL: dict[str, Any] = {
    "input_file_type": "openapi",
    "target_python_version": "3.11",
    "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
    "output_model_type": "pydantic_v2.BaseModel",
    "disable_timestamp": True,
    "formatters": [Formatter.BUILTIN],
}


def _generate(root: Path, source: str, package: str, models: str, **settings: Any) -> None:
    generate(
        shutil.copy2(SOURCE / source, root / source),
        output=root.joinpath(*models.split(".")).with_suffix(".py"),
        generate_server="fastapi",
        server_output=root.joinpath(*package.split(".")),
        server_package=package,
        server_model_package=models,
        **MODEL,
        **settings,
    )


def _tree(directory: Path, root: Path) -> str:
    return ", ".join(sorted(path.relative_to(root).as_posix() for path in directory.rglob("*.py")))


def _serve(root: Path, package: str, *requests: tuple[str, str, dict[str, str]]) -> Iterator[str]:
    """Import the package's entrypoint and send each request to its application, or report why the import failed.

    Errors name files under the generation root, which the report spells `<root>` with forward slashes.
    """
    top = package.partition(".")[0]
    try:
        import_generated(package)
        main = importlib.import_module(f"{package}.main")
    except Exception as error:  # noqa: BLE001
        cause = error.__cause__
        message = str(error).replace(str(root), "<root>").replace(root.as_posix(), "<root>").replace("\\", "/")
        yield f"  {type(error).__name__}: {message}"
        yield f"  caused by {type(cause).__name__} of {getattr(cause, 'name', None)}" if cause else "  not chained"
        return
    finally:
        for name in (top, "services"):
            forget_generated(name)
    with TestClient(main.app, raise_server_exceptions=False) as client:
        for method, url, headers in requests:
            response = client.request(method, url, headers=headers)
            yield f"  {method} {url}: {response.status_code} {response.text}"


def fastapi_entrypoint_report(root: Path) -> tuple[str, dict[tuple[str, ...], str]]:
    """Report each step of a user implementing and breaking the stubs, and return the stubs generation wrote."""
    nested, secured = root / "nested", root / "secured"
    nested.mkdir()
    secured.mkdir()
    services = nested / "app" / "services"
    store = services / "store.py"
    inventory = ("GET", "/store/inventory", {})
    lines = ["# nested server package app.server"]
    _generate(nested, "pets.yaml", "app.server", "app.models")
    lines.append(f"stubs: {_tree(services, nested)}")
    stubs = {
        path.relative_to(root).parts: path.read_text(encoding="utf-8")
        for path in (*sorted(services.rglob("*.py")), nested / "app" / "server" / "main.py")
    }
    sys.path.insert(0, str(nested))
    try:
        lines.extend(("serve the stubs", *_serve(nested, "app.server", inventory)))
        shutil.copy2(USER / "store.py", store)
        (services / "pets.py").write_text("# edited\n" + (services / "pets.py").read_text(encoding="utf-8"))
        _generate(nested, "pets.yaml", "app.server", "app.models")
        lines.append(
            f"regenerate: store kept {store.read_bytes() == (USER / 'store.py').read_bytes()}; "
            f"pets kept {(services / 'pets.py').read_text(encoding='utf-8').startswith('# edited')}"
        )
        lines.extend(("serve an implementation", *_serve(nested, "app.server", inventory)))
        for name in (
            "store-mismatch",
            "store-without-class",
            "store-dependency",
            "store-import-name",
            "store-wrapped",
            "store-builtin",
        ):
            shutil.copy2(USER / f"{name}.py", store)
            lines.extend((f"serve {name}", *_serve(nested, "app.server", inventory)))
        deferred = (USER / "store-deferred.py").read_text(encoding="utf-8")
        future = "" if sys.version_info >= (3, 14) else "from __future__ import annotations\n"
        store.write_text(future + deferred, encoding="utf-8")
        lines.extend(("serve store-deferred", *_serve(nested, "app.server", inventory)))
        shutil.copy2(USER / "store.py", store)
        pets = (services / "pets.py").read_text(encoding="utf-8")
        shutil.copy2(USER / "pets-import-name.py", services / "pets.py")
        lines.extend(("serve pets-import-name", *_serve(nested, "app.server", inventory)))
        (services / "pets.py").write_text(pets, encoding="utf-8")
        store.unlink()
        (store.with_suffix("") / "__init__.py").parent.mkdir()
        (store.with_suffix("") / "__init__.py").write_text('"""A store package without its class."""\n')
        lines.extend(("serve a store package without Store", *_serve(nested, "app.server", inventory)))
        shutil.rmtree(store.with_suffix(""))
        lines.extend(("serve without store.py", *_serve(nested, "app.server", inventory)))
        shutil.rmtree(services)
        lines.extend(("serve without the services package", *_serve(nested, "app.server", inventory)))
    finally:
        sys.path.remove(str(nested))
    lines.append("# top-level server package secured, whose groups service and operations both name a class Operations")
    _generate(secured, "server-security.yaml", "secured", "secured_models", server_router_names=SECURED_NAMES)
    lines.append(f"stubs: {_tree(secured / 'services', secured)}")
    stubs.update(
        (path.relative_to(root).parts, path.read_text(encoding="utf-8"))
        for path in sorted((secured / "services").rglob("*.py"))
    )
    stubs["secured", "secured", "main.py"] = (secured / "secured" / "main.py").read_text(encoding="utf-8")
    sys.path.insert(0, str(secured))
    try:
        key = {"X-API-Key": "key"}
        lines.extend(("serve the stubs", *_serve(secured, "secured", ("GET", "/public", {}), ("GET", "/pets", key))))
    finally:
        sys.path.remove(str(secured))
    return "\n".join(lines) + "\n", stubs


def readme_examples_report(root: Path) -> str:
    """Run the Python examples of the READMEs of an unsecured and a secured server as a reader copies them.

    The test example runs with pytest as printed: its fakes subclass the classes of the modules generation wrote with
    stubs. The example of the router builds its application, whose routes the report lists.
    """
    lines: list[str] = []
    for name, source, package, models, settings in (
        ("nested", "pets.yaml", "app.server", "app.models", {}),
        ("secured", "server-security.yaml", "secured", "secured_models", {"server_router_names": SECURED_NAMES}),
    ):
        (directory := root / name).mkdir()
        _generate(directory, source, package, models, **settings)
        readme = directory.joinpath(*package.split("."), "README.md").read_text(encoding="utf-8")
        test, router = PYTHON.findall(readme.partition("\n## Implementing the services\n")[2].partition("\n## ")[0])
        (directory / "test_readme.py").write_text(test, encoding="utf-8")
        (directory / "router_app.py").write_text(f"{router}\nprint(sorted(app.openapi()['paths']))\n", encoding="utf-8")
        lines.extend((
            f"# {package}",
            "test example:",
            run_tests(directory, "test_readme.py"),
            "router example:",
            run_python(directory, "router_app.py"),
        ))
    return "\n".join(lines)


def run_commands_report(root: Path, notice: str) -> str:
    """Serve a generated server with each command of its run notice, reporting the title of the document it serves.

    uvicorn runs with the arguments the notice prints. fastapi-cli is not a test dependency, so uvicorn serves the
    import string of `fastapi dev --entrypoint` from the directory that `uv run --directory` names, as `fastapi dev`
    would import it there.
    """
    lines: list[str] = []
    for line in notice.splitlines():
        if not (command := line.partition("&& uv run ")[2]):
            continue
        arguments = shlex.split(command)
        directory, served = root, [argument for argument in arguments[1:] if argument != "--reload"]
        if arguments[0] != "uvicorn":
            directory = root / arguments[1] if arguments[0] == "--directory" else root
            served = [arguments[arguments.index("--entrypoint") + 1]]
        with serve(directory, *served) as url, urllib.request.urlopen(f"{url}/openapi.json") as response:
            title = json.load(response)["info"]["title"]
        lines.append(f"{command}\n  serves {title!r} from {directory.relative_to(root).as_posix()}")
    return "\n".join(lines) + "\n"
