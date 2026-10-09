# 🐍 Using datamodel-code-generator as a Module

datamodel-code-generator is a CLI tool, but it can also be used as a Python module.

## 🚀 How to Use

You can generate models with `datamodel_code_generator.generate` using parameters that match the [CLI arguments](./index.md).

### 📦 Installation

The base package is sufficient when all inputs and references are local. To
load an HTTP(S) input or resolve a remote `$ref`, install the stable HTTP extra:

```bash
pip install 'datamodel-code-generator[http]'
```

The `http` extra uses HTTPX and is supported and not deprecated. The
experimental HTTPX2 backend is available separately:

```bash
pip install 'datamodel-code-generator[httpx2]'
```

The experimental extra is not included in `datamodel-code-generator[all]`.
The default `HTTPBackend.AUTO` policy selects stable HTTPX when its client
module is installed, including when both extras are installed, and selects
HTTPX2 only when that module is absent. Require HTTPX2 with
`--http-backend httpx2`, `http_backend = "httpx2"` under
`[tool.datamodel-codegen]`, or the public API:

```python
from urllib.parse import urlparse

from datamodel_code_generator import HTTPBackend, generate

result = generate(
    urlparse("https://example.com/schema.json"),
    http_backend=HTTPBackend.HTTPX2,
)
```

See [HTTP backend selection](faq.md#http-backend-selection) for the complete
automatic and explicit selection rules.

### 📝 Getting Generated Code as String

When the `output` parameter is omitted (or set to `None`), `generate()` returns the generated code directly as a string:

!!! note
    `GenerateConfig` requires a Pydantic v2 environment.

```python
from datamodel_code_generator import InputFileType, generate, GenerateConfig, DataModelType

json_schema: str = """{
    "type": "object",
    "properties": {
        "number": {"type": "number"},
        "street_name": {"type": "string"},
        "street_type": {"type": "string",
                        "enum": ["Street", "Avenue", "Boulevard"]
                        }
    }
}"""

config = GenerateConfig(
    input_file_type=InputFileType.JsonSchema,
    input_filename="example.json",
    output_model_type=DataModelType.PydanticV2BaseModel,
)
result = generate(json_schema, config=config)
print(result)
```

### Loading Project Configuration

`generate()` uses the options passed to it. To reuse `[tool.datamodel-codegen]`
from `pyproject.toml`, explicitly load a `GenerateConfig`:

```python
from datamodel_code_generator import generate, load_pyproject_config

config = load_pyproject_config()
result = generate(json_schema, config=config)
```

The loader searches upward from the working directory, stopping at a Git project
boundary, as the CLI does. Pass `path="project"`, a `Path`, or a `pyproject.toml` path
to start elsewhere, and `profile="models"` to select a profile, including its
`extends` inheritance. Without project settings, it returns the API defaults;
a requested profile must exist.

Both a `GenerateConfig` and a dictionary of individual Python API options can
override the project settings:

```python
from datamodel_code_generator import GenerateConfig, InputFileType

options = {"input_file_type": InputFileType.JsonSchema, "output": None}
config = load_pyproject_config(overrides=options)
result = generate(json_schema, config=config)

existing_config = GenerateConfig(output=None, disable_timestamp=True)
config = load_pyproject_config(profile="models", overrides=existing_config)
result = generate(json_schema, config=config)
```

The precedence is API defaults, project settings, profile settings, then
overrides. Only explicitly set fields of a `GenerateConfig` override are merged;
explicit `False`, `None`, and empty lists also replace project values. Paths
loaded from TOML are relative to its directory, including JSON configuration
files such as `aliases`. Override paths remain relative to the caller's working
directory. The project directory supplies formatter settings unless
`settings_path` is explicitly overridden. CLI-only settings such as `input`,
`check`, and batch jobs are ignored: pass the schema to `generate()` yourself.
The loader validates the merged settings and preserves the existing rule that
`config=` cannot be combined with individual `generate()` options.

### 📝 Reading Input From a File

Pass a `Path` to `input_` for file input. Plain strings are treated as schema
text; if a string points to an existing file and parsing fails, `generate()`
warns and recommends using `Path`.

```python
from pathlib import Path

from datamodel_code_generator import InputFileType, generate

result = generate(
    input_=Path("example.json"),
    input_file_type=InputFileType.JsonSchema,
)
print(result)
```

### 📝 Multiple Module Output

When the schema generates multiple modules, `generate()` returns a `GeneratedModules` dictionary mapping module path tuples to generated code:

```python
from datamodel_code_generator import InputFileType, generate, GenerateConfig, GeneratedModules

# Your OpenAPI specification (string schema text, Path, or dict)
openapi_spec: str = "..."  # Replace with your actual OpenAPI spec

# Schema that generates multiple modules (e.g., with $ref to other files)
config = GenerateConfig(
    input_file_type=InputFileType.OpenAPI,
)
result: str | GeneratedModules = generate(openapi_spec, config=config)

if isinstance(result, dict):
    for module_path, content in result.items():
        print(f"Module: {'/'.join(module_path)}")
        print(content)
        print("---")
else:
    print(result)
```

### 📝 Writing to Files

To write generated code to the file system, provide a `Path` to the `output` parameter in the config:

```python
from pathlib import Path
from tempfile import TemporaryDirectory
from datamodel_code_generator import InputFileType, generate, GenerateConfig, DataModelType

json_schema: str = """{
    "type": "object",
    "properties": {
        "number": {"type": "number"},
        "street_name": {"type": "string"},
        "street_type": {"type": "string",
                        "enum": ["Street", "Avenue", "Boulevard"]
                        }
    }
}"""

with TemporaryDirectory() as temporary_directory_name:
    temporary_directory = Path(temporary_directory_name)
    output = Path(temporary_directory / "model.py")
    config = GenerateConfig(
        input_file_type=InputFileType.JsonSchema,
        input_filename="example.json",
        output=output,
        # set up the output model types
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    generate(json_schema, config=config)
    model: str = output.read_text()
print(model)
```

**✨ Output:**
```python
# generated by datamodel-codegen:
#   filename:  example.json
#   timestamp: 2020-12-21T08:01:06+00:00

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class StreetType(Enum):
    Street = "Street"
    Avenue = "Avenue"
    Boulevard = "Boulevard"


class Model(BaseModel):
    number: Optional[float] = None
    street_name: Optional[str] = None
    street_type: Optional[StreetType] = None
```

---

### Generating a Server or Client Package

!!! warning "Experimental"
    Server and client generation is experimental; its options and generated packages may change.

`generate()` also generates the [FastAPI server](fastapi-server.md) or the [HTTPX2 client](python-client.md) package
of an OpenAPI document together with its models. `generate_server` or `generate_client` selects the package, and
the `server_*` or `client_*` options configure it. They are the options of `--generate-server`, `--generate-client`,
and the `--server-*` and `--client-*` flags under their Python names, as for model options: choices are enums such as
`ServerType` (their string values also work), and the options that take JSON on the command line take mappings.

```python
from pathlib import Path

from datamodel_code_generator import InputFileType, OpenAPIScope, ServerHandlerMode, ServerType, generate

generate(
    Path("openapi.yaml"),
    input_file_type=InputFileType.OpenAPI,
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    target_python_version="3.11",
    output=Path("models.py"),
    generate_server=ServerType.FastAPI,
    server_output=Path("server"),
    server_package="server",
    server_model_package="models",
    server_handler_mode=ServerHandlerMode.Async,
    server_body_modes={"/paths/~1upload/post": "request"},
)
```

With an `output`, `generate()` writes the models and the package together and returns `None`. Without one, it writes
nothing but the model metadata and a remote lock update, and returns `GeneratedModules` with every model and package
file, each under the path its import path implies: `server_model_package="models"` gives `("models.py",)`, or
`("models", "__init__.py")` and the other modules of modular models, and `server_package="server"` gives
`("server", "application.py")`, `("server", "README.md")`, and so on. These are the files a run with `output` and
`server_output` in the working directory writes; `server_output` is not used. `load_pyproject_config()` takes the
server and client keys of `pyproject.toml` too, so a config it loads with `overrides={"output": None}` returns the
files of the configured run.

The selector requires `server_package` and `server_model_package` (or the client ones), and `server_output` with an
`output`. A missing option, an option the target cannot use, and a failure of the package raise
`datamodel_code_generator.Error` with the message the command line prints. The `server_*` and `client_*` options have
no effect without their selector, as their `pyproject.toml` keys do.

---

## 🔧 Using the Parser Directly

You can also call the parser directly for more control. Parser classes also support the `config` parameter similar to `generate()`.

### Using `config` Parameter (Recommended)

```python
from datamodel_code_generator import DataModelType, PythonVersion
from datamodel_code_generator.config import JSONSchemaParserConfig
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

json_schema: str = """{
    "type": "object",
    "properties": {
        "number": {"type": "number"},
        "street_name": {"type": "string"},
        "street_type": {"type": "string",
                        "enum": ["Street", "Avenue", "Boulevard"]
                        }
    }
}"""

data_model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
config = JSONSchemaParserConfig(
    data_model_type=data_model_types.data_model,
    data_model_root_type=data_model_types.root_model,
    data_model_field_type=data_model_types.field_model,
    data_type_manager_type=data_model_types.data_type_manager,
    dump_resolve_reference_action=data_model_types.dump_resolve_reference_action,
)
parser = JsonSchemaParser(json_schema, config=config)
result = parser.parse()
print(result)
```

### Using Keyword Arguments (Backward Compatible)

```python
from datamodel_code_generator import DataModelType, PythonVersion
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

json_schema: str = """{
    "type": "object",
    "properties": {
        "number": {"type": "number"},
        "street_name": {"type": "string"},
        "street_type": {"type": "string",
                        "enum": ["Street", "Avenue", "Boulevard"]
                        }
    }
}"""

data_model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersion.PY_311)
parser = JsonSchemaParser(
    json_schema,
    data_model_type=data_model_types.data_model,
    data_model_root_type=data_model_types.root_model,
    data_model_field_type=data_model_types.field_model,
    data_type_manager_type=data_model_types.data_type_manager,
    dump_resolve_reference_action=data_model_types.dump_resolve_reference_action,
)
result = parser.parse()
print(result)
```

### Available Parser Config Classes

Each parser type has its own config class:

| Parser | Config Class |
|--------|-------------|
| `JsonSchemaParser` | `JSONSchemaParserConfig` |
| `OpenAPIParser` | `OpenAPIParserConfig` |
| `AvroParser` | `AvroParserConfig` |
| `GraphQLParser` | `GraphQLParserConfig` |

All config classes inherit from `ParserConfig` and include additional parser-specific options.

**✨ Output:**
```python
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class StreetType(Enum):
    Street = "Street"
    Avenue = "Avenue"
    Boulevard = "Boulevard"


class Model(BaseModel):
    number: Optional[float] = None
    street_name: Optional[str] = None
    street_type: Optional[StreetType] = None
```

---

## 📋 Return Value Summary

| `output` Parameter | Single Module | Multiple Modules |
|-------------------|---------------|------------------|
| `None` (default)  | `str`         | `GeneratedModules` (dict) |
| `Path` (file)     | `None`        | Error |
| `Path` (directory)| `None`        | `None` |

📌 **Note:** When `output` is a file path and multiple modules would be generated, `generate()` raises a `datamodel_code_generator.Error` exception. Use a directory path instead.

With `generate_server` or `generate_client`, `generate()` returns `None` with an `output` and `GeneratedModules` with
every model and package file without one (see [Generating a Server or Client Package](#generating-a-server-or-client-package)).

---

## 📖 See Also

- [Dynamic Model Generation](dynamic-model-generation.md) - Generate Python classes at runtime
- [CLI Reference](cli-reference/index.md) - Complete CLI options (same parameters as module)
- [Generate from JSON Schema](jsonschema.md) - JSON Schema examples
