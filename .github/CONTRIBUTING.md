# Contributing

Thank you for helping datamodel-code-generator. The development guide (setup, tests, docs) is
[docs/development-contributing.md](../docs/development-contributing.md).

## What we value

- **Problems from real use.** The most valuable contribution is a report of a problem you met while generating code
  from your own schemas: the schema (or a minimal one that shows it), the exact command, what you got and what you
  expected. Reports like that are often fixed quickly.
- **Changes from reading the code.** A pull request is welcome when it fixes a problem you met, or one you found by
  reading the code and reproduced on the current `main`. Say which in the description, in your own words.
- **Your use case as a test.** A pull request may add your use case as an end-to-end test: a schema (a minimal one
  that shows the case; share only what you may publish), the command, and the output it generates (see
  [E2E test assertions](../docs/development-contributing.md#-e2e-test-assertions)). From then on, every change is
  tested against it. We can't promise to support every use case forever, but we will do our best to keep it working.

## AI tools

You may use AI tools for any part of your contribution, and you don't need to label it. You are responsible for what
you submit, however it was produced: read and run it, and be ready to explain every line. Do not let an agent open
issues or pull requests without your review.

## Pull requests

- **Link each pull request to an issue** (`Fixes #123` in the description). If there is none, open the issue first,
  with the problem and how you met it. Small documentation fixes may skip the issue.
- **Planned work stays with the maintainers.** Issues that a maintainer filed and assigned to themselves are planned
  work. Please don't open pull requests for them unless a maintainer asks for one in the issue; comment there instead
  if the problem affects you, with your use case.

Pull requests without a use case or a reproduction, and pull requests for planned work, may be closed without comment.
This keeps the maintainers' time for the reviews that help users.
