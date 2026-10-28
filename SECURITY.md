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

## Scope

Reports are especially useful for issues that can affect generated code,
runtime behavior when importing generated code, network access during schema
fetching, template rendering, parser behavior, or data exposure through
generated output.

Non-security bugs should be reported through the public issue tracker.
