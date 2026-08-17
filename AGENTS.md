# Agent Instructions

## Scope

- Read `docs/project.md` and the relevant implementation and tests before changing behavior.
- Make the smallest focused change and preserve the existing architecture and conventions.
- Do not modify unrelated user changes in a dirty worktree.

## Security

- Never print, log, paste, or return credentials, tokens, cookies, authorization headers, signed media URLs, `.env` contents, or resolved secret-bearing configuration.
- Treat terminal output, tool results, CI logs, generated artifacts, and chat as disclosure surfaces, even when commands run on an owned machine.
- Do not run `docker compose config`, `env`, `printenv`, shell tracing, or similar commands that can expand secrets unless output is safely filtered before execution. Prefer targeted checks that report only whether a value is set.
- Do not read `.env.amp` unless the task explicitly requires it. Never include its values in diagnostics.
- Log only allowlisted metadata such as IDs, hostnames, format identifiers, counts, return codes, and exception class names. Never log raw exception text when it may contain request data.
- Redact sensitive values at centralized output boundaries and add regression tests whenever logging or diagnostics change.

## Engineering

- Investigate and reproduce failures before implementing a fix.
- Avoid blocking network, extraction, or subprocess work on the asyncio event loop.
- Keep user input out of shell command strings; use validated values and argument arrays.
- Keep dependencies constrained and do not add one when the standard library is sufficient.

## Verification

- Run `python -m ruff check .`, `python -m mypy src`, and `python -m pytest` after code changes.
- Run `docker build -t amp:local .` when container or dependency behavior changes and Docker is available.
- Report any verification that could not be run and why.
