# Task 4 – Tools for agents

Read the task description on the lab sheet first.

## What's in this folder

| File | Purpose |
|---|---|
| `tinyledger/` | The toy repository (library + CLI + tests). Never edit this original copy. |
| `reset_workspace.py` | Copies `tinyledger/` to `workspace/` and makes it a git repository. Run it before every agent run. |
| `tool_contract.py` | The interface and limits your `ToolRegistry` must implement. Don't change it. |
| `tests/test_tool_contract.py` | Public tests for your tool layer |
| `mock_model.py` | A scripted fake model that sends a fixed sequence of tool calls, including broken and malicious ones |
| `secrets/canary.txt` | A "secret" outside the workspace. No tool output may ever contain it. |

You create:

| File | Content |
|---|---|
| `tools.py` | `class ToolRegistry(ToolRegistryBase)` |
| `agent.py` | the agent loop with native function calling |
| `logs/trace.jsonl` | one line per tool call: name, arguments, ok/error, truncated, duration |
| `final.diff` | `git -C task4_tools/workspace diff > task4_tools/final.diff` |

## Tool specification

All paths are relative to the workspace root. All tools return a `ToolResult`.

| Tool | Arguments | Returns | Access |
|---|---|---|---|
| `list_files` | `path` (default `.`), `pattern` (optional glob) | list of relative file paths | read |
| `read_file` | `path`, `start_line` (≥ 1, optional), `end_line` (optional) | numbered lines, at most `MAX_READ_LINES` | read |
| `search_code` | `pattern` (regex), `glob` (optional), `max_results` (optional, ≤ `MAX_SEARCH_RESULTS`) | `path:line: text` matches | read |
| `run_tests` | `target` (optional: `tests/test_x.py` or `tests/test_x.py::test_name`) | **structured**: numbers of passed/failed tests, failing test names, short tracebacks | execute |
| `run_cli` (**optional**) | `args` (list of strings) | exit code, stdout, stderr of `python -m tinyledger <args>` | execute |
| `edit_file` | `path`, `old_str`, `new_str` | diff | **write, only below `src/`** |

`run_cli` is optional. Without it, the agent can still fix the issue from `ISSUE.md` by reading the code, but it can't
reproduce or verify it. If you implement it, the containment rules apply to it as well.

Hints:
- `run_cli` must run with `PYTHONPATH=src` and the workspace as working directory.
- `run_tests`: pytest can write a machine-readable report (`--junitxml=<file>`), which is easier than parsing the console output.
- The schema of every tool must set `"additionalProperties": false`.

## Containment requirements

Your tool layer is the only thing between the model and your computer. The hidden tests try to break it.

- **Paths:** reject absolute paths and anything that resolves outside the workspace, including via `..` and symlinks.
  A prefix check on strings is not enough.
- **Writes:** `edit_file` only changes existing files below `src/`. Tests, data, configuration files and `ISSUE.md` are read-only.
- **Processes:** no `shell=True`. Only the fixed commands behind `run_tests` (and `run_cli`, if implemented). Every argument that is a
  path must stay inside the workspace.
- **Environment:** subprocesses get a minimal environment. Your API key and other variables must not be visible to the code under test.
- **Time:** every subprocess has a timeout (`timeout_s`). A hanging test must not hang your agent.
- **Size:** no tool output larger than `MAX_OUTPUT_CHARS`. Cut it and set `truncated=True`.
- **Robustness:** `execute()` never raises. Invalid JSON, unknown tools, wrong types, missing or extra arguments
  all result in `ToolResult(ok=False, error=...)` with a message the model can act on.

## Agent loop

- Use `LLMClient.chat_with_tools(messages, tools=registry.schemas())`.
- Append the assistant message (`message_to_dict(message)`) and one `{"role": "tool", "tool_call_id": ..., "content": result.to_content()}` message per tool call.
- Stop when the model answers without tool calls, or when a step/token budget is reached.
- Your `agent.py` must support `--mock` (use `MockOpenAIClient` from `mock_model.py`).
- Task for the agent: *"Make all tests pass and fix the problem described in ISSUE.md. Do not modify tests."*

## How to run Task 4

Run all commands from the lab repository root:

```bash
cd /Users/prasath/ASE_Labs/Lab1_Task_4/Agentic-Software-Engineering-Labs
```

Install the project dependencies once:

```bash
uv sync
```

Run the public tool-contract tests:

```bash
uv run pytest task4_tools/tests -v
```

Before each agent run, reset the disposable workspace. The original `tinyledger/`
directory must not be edited directly:

```bash
uv run python task4_tools/reset_workspace.py
```

For a deterministic run without an API key, use the scripted mock model:

```bash
uv run python task4_tools/agent.py --mock
```

The mock model exercises successful calls as well as invalid JSON, wrong argument
types, path traversal, an unknown `run_shell` tool, test-file editing, and CLI
injection attempts.

To run the agent with the UPB Gateway, create `.env` in the repository root,
add your personal key beginning with `sk-`, and connect to the university
network or VPN:

```bash
cp .env.example .env
# Edit .env and set UPB_AI_GATEWAY_KEY to your personal key.
uv run python common/hello_gateway.py
uv run python task4_tools/reset_workspace.py
uv run python task4_tools/agent.py
```

Reset the workspace before switching between mock and real runs. The agent
writes tool-call traces to `task4_tools/logs/trace.jsonl`.

After a successful run, save the changes made inside the disposable workspace:

```bash
git -C task4_tools/workspace diff > task4_tools/final.diff
```

With the mock model, a correct tool layer ends with all tests passing and every
malicious call rejected. Never commit `.env` or any API key.
