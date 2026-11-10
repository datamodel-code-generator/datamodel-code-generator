# Security Policy

## Supported versions

Security fixes are released for the latest stable version of
`datamodel-code-generator`.

| Version | Supported |
| ------- | --------- |
| Latest stable release | Yes |
| Older releases | No |

If a security fix is needed, update to the latest release listed on PyPI or in
the GitHub releases page. Security advisories may list the exact affected and
patched version ranges for each issue.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub Private
Vulnerability Reporting:

https://github.com/datamodel-code-generator/datamodel-code-generator/security/advisories/new

Do not publish exploit details in a public issue, pull request, discussion, or
social media post before a fix is available and the advisory has been published.

If GitHub Private Vulnerability Reporting is unavailable, contact the maintainer
at `koxudaxi@gmail.com` with enough detail to reproduce and assess the issue.

Useful reports include:

- affected versions;
- installation extras involved, such as `http`, `graphql`, or `protobuf`;
- input file type and command-line options;
- a minimal proof of concept or reproducer;
- expected and actual behavior;
- whether the issue affects confidentiality, integrity, or availability.

## Coordinated disclosure

After a report is received, maintainers will triage the issue and, when
appropriate, create or continue a GitHub Security Advisory. Fixes may be
developed in the advisory's temporary private fork before being merged into the
public repository.

The usual workflow is:

1. Acknowledge and triage the report.
2. Reproduce the issue and determine affected versions.
3. Prepare and test a patch privately when needed.
4. Release a patched version.
5. Publish or update the GitHub Security Advisory.

Please wait to publish write-ups, proof-of-concept details, or exploit analysis
until the patched version is available and the advisory is public.

## Threat model

datamodel-code-generator writes Python source code from schemas, and projects
import that code. Whether a report is a vulnerability depends on who controls
the input.

### Trusted input (the default)

By default, everything given to the generator is treated like code the project
runs:

- the schema and every document it references;
- the configuration: `[tool.datamodel-codegen]` in `pyproject.toml`,
  command-line options and `GenerateConfig`;
- custom templates (`--custom-template-dir`), `--extra-template-data`, custom
  formatters, and the values of `--url` and the HTTP options.

Some features exist to write Python names, imports or file locations into the
generated code, or to read other files and URLs. They do what their
documentation says, and a report that shows only this is not a vulnerability:

- the schema keywords `x-python-import`, `x-python-type`, `customTypePath` and
  `customBasePath`;
- `$ref` to other files and URLs, XML Schema includes and imports, and Protocol
  Buffers imports;
- custom templates, which decide how they write the values they receive;
  `--extra-template-data`; custom formatters; `--url`.

The defaults for remote references (`--allow-remote-refs` unset,
`--no-allow-remote-refs`, the check that refuses private network addresses)
reduce mistakes with trusted input. They are not a security boundary for input
the project does not control.

### Untrusted input

When a schema comes from a source the project does not control, such as an HTTP
request, a user upload or the tool schema of an LLM, and the generated code is
imported or executed, pass `--untrusted-input` (`untrusted_input=True` in
Python; experimental). The generator then reads no file or URL but the input,
refuses the schema keywords that write Python code, and refuses Protocol Buffers
input and `--validation`. Configuration, custom templates, extra template data
and custom formatters still come from the project and stay trusted.

### What is a vulnerability

- A data value of a schema (a description, title, default, example, enum value,
  name or pattern) becoming code in the code written by the built-in templates,
  with any options. For example, a value that is not escaped.
- Anything that gets past `--untrusted-input`: reading a file or URL other than
  the input, writing an import or type expression the schema controls, or
  running code from the input.

Other defects, including a default safety setting that a code path does not
apply, are bugs: please report them in the public issue tracker.

## Scope

Within the threat model above, reports are especially useful for issues that can
affect generated code, runtime behavior when importing generated code, network
access during schema fetching, template rendering, parser behavior, or data
exposure through generated output.

Non-security bugs should be reported through the public issue tracker.
