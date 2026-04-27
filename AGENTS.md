# Repository Guidelines

## Project Structure & Module Organization

This Python 3.11+ package exposes IDA Pro and idalib through MCP. Source lives in `src/ida_pro_mcp/`. Main entry points are `server.py` for the MCP server, `idalib_server.py` for headless idalib, and `ida_mcp/` for IDA/plugin-side APIs. API modules follow `api_*.py`; shared helpers live in `utils.py`, `rpc.py`, `sync.py`, and `framework.py`. IDA-facing tests are in `src/ida_pro_mcp/ida_mcp/tests/`; top-level `tests/` contains transport/spec tests and fixture binaries. Contributor docs, profiles, and agent skills are in `devdocs/`, `profiles/`, and `skills/`.

## Build, Test, and Development Commands

- `uv run ida-pro-mcp`: run the standard MCP server.
- `uv run ida-pro-mcp --transport http://127.0.0.1:8744/sse`: run the SSE transport.
- `uv run idalib-mcp --host 127.0.0.1 --port 8745 path/to/binary`: run headless idalib.
- `uv run ida-pro-mcp --install`: install/configure the IDA plugin and MCP server.
- `uv run mcp dev src/ida_pro_mcp/server.py`: launch the MCP inspector for local server work.
- `uv run python -m unittest discover -s tests`: run top-level Python unit tests.

## Coding Style & Naming Conventions

Use 4-space indentation, type hints, and concise docstrings. MCP tools should use `@tool`, IDA SDK access should run under `@idasync`, and destructive/debugger operations should be marked `@unsafe`. Prefer batch-first APIs that accept lists or comma-separated strings. Reuse helpers such as `parse_address()`, `normalize_list_input()`, and `normalize_dict_list()`. New API files should follow `api_<domain>.py`; test files should follow `test_<module>.py`.

## Testing Guidelines

IDA-facing tests use `ida_mcp/framework.py` and the `@test` decorator. Prefer semantic assertions and round-trip checks over weak field-existence tests. Use `@test(binary="crackme03.elf")` when fixture behavior matters. Run maintained fixtures with:

```bash
uv run ida-mcp-test tests/crackme03.elf -q
uv run ida-mcp-test tests/typed_fixture.elf -q
```

For coverage:

```bash
uv run coverage erase
uv run coverage run -m ida_pro_mcp.test tests/crackme03.elf -q
uv run coverage run --append -m ida_pro_mcp.test tests/typed_fixture.elf -q
uv run coverage report --show-missing
```

## Commit & Pull Request Guidelines

Recent history uses short, imperative commit subjects such as `Add tests for api_sigmaker tools` and `Derive download URLs from request base across proxies`. Keep commits focused and mention tests when behavior changes. Pull requests should describe the affected API/server area, link issues, note IDA/idapython assumptions, and include exact test commands run.

## Security & Configuration Tips

IDA Free is not supported; IDA Pro 8.3+ is required and 9.x is recommended. Avoid enabling unsafe tools by default; use `--unsafe` only when debugger or destructive operations are intentionally needed. Never commit local MCP client secrets, generated IDBs, or machine-specific plugin paths.
