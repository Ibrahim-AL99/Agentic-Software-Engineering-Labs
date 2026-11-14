"""Tool layer implementation for Task 4.

Provides the ToolRegistry implementing ToolRegistryBase with JSON schema validation,
strict containment, least privilege enforcement, and bounded structured outputs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import jsonschema

from tool_contract import (
    DEFAULT_TIMEOUT_S,
    MAX_LIST_ENTRIES,
    MAX_OUTPUT_CHARS,
    MAX_READ_LINES,
    MAX_SEARCH_RESULTS,
    REQUIRED_TOOLS,
    ToolRegistryBase,
    ToolResult,
)


class ToolRegistry(ToolRegistryBase):
    """Tool registry providing sandboxed, validated tool execution."""

    def __init__(self, workspace: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        super().__init__(workspace=workspace, timeout_s=timeout_s)
        self.workspace = self.workspace.resolve()
        self._init_schemas()

    def _init_schemas(self) -> None:
        self._schemas: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List files in the workspace directory with optional glob pattern.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative directory path inside workspace (default: '.').",
                            },
                            "pattern": {
                                "type": "string",
                                "description": "Optional glob pattern to filter files, e.g. '*.py' or '**/*.py'.",
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read content from a file with line numbers and optional line range.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path inside workspace.",
                            },
                            "start_line": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "1-based starting line number (optional).",
                            },
                            "end_line": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "1-based ending line number, inclusive (optional).",
                            },
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "Search code files in the workspace using regular expressions.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "Regular expression pattern to search for.",
                            },
                            "glob": {
                                "type": "string",
                                "description": "Optional glob pattern to limit files searched, e.g. '**/*.py'.",
                            },
                            "max_results": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": MAX_SEARCH_RESULTS,
                                "description": f"Maximum number of matches to return (at most {MAX_SEARCH_RESULTS}).",
                            },
                        },
                        "required": ["pattern"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_tests",
                    "description": "Run pytest test suite in the workspace and return structured results.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Optional test file or node id, e.g. 'tests/test_ledger.py'.",
                            },
                        },
                        "required": [],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Replace an exact unique string in a file. Only permitted for files under 'src/'.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "Relative file path under src/.",
                            },
                            "old_str": {
                                "type": "string",
                                "description": "Exact text to find and replace. Must occur exactly once.",
                            },
                            "new_str": {
                                "type": "string",
                                "description": "Replacement text.",
                            },
                        },
                        "required": ["path", "old_str", "new_str"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_cli",
                    "description": "Run the tinyledger CLI module: python -m tinyledger <args>.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "args": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of command line arguments for tinyledger.",
                            },
                        },
                        "required": ["args"],
                        "additionalProperties": False,
                    },
                },
            },
        ]
        self._schema_map = {s["function"]["name"]: s["function"]["parameters"] for s in self._schemas}
        self._validators = {name: jsonschema.Draft202012Validator(param) for name, param in self._schema_map.items()}

    def schemas(self) -> list[dict]:
        """Return OpenAI function tool definitions."""
        return self._schemas

    def _resolve_safe_path(self, path_str: str) -> Path:
        """Resolve a path relative to workspace, ensuring strict containment."""
        if not isinstance(path_str, str):
            raise ValueError(f"Path must be a string, got {type(path_str).__name__}")
        if "\0" in path_str:
            raise ValueError("Null bytes not permitted in path")
        if path_str.startswith("/") or path_str.startswith("\\") or path_str.startswith("~"):
            raise ValueError("Absolute paths are not permitted")
        if Path(path_str).is_absolute():
            raise ValueError("Absolute paths are not permitted")
        if ".." in Path(path_str).parts:
            raise ValueError("Path traversal ('..') is not permitted")

        resolved = (self.workspace / path_str).resolve()
        try:
            resolved.relative_to(self.workspace)
        except ValueError:
            raise ValueError("Path escapes workspace boundary")

        return resolved

    def _bound_result(self, result: ToolResult) -> ToolResult:
        """Ensure result payload does not exceed MAX_OUTPUT_CHARS."""
        content = result.to_content()
        if len(content) > MAX_OUTPUT_CHARS:
            result.truncated = True
            if isinstance(result.output, str):
                allowed = max(0, MAX_OUTPUT_CHARS - 200)
                result.output = result.output[:allowed] + "\n... [truncated]"
            elif isinstance(result.output, list):
                result.output = result.output[:50]
                content = result.to_content()
                if len(content) > MAX_OUTPUT_CHARS:
                    result.output = json.dumps(result.output)[:MAX_OUTPUT_CHARS - 200] + "... [truncated]"
            elif isinstance(result.output, dict):
                content_str = json.dumps(result.output, ensure_ascii=False)
                allowed = max(0, MAX_OUTPUT_CHARS - 200)
                result.output = content_str[:allowed] + "... [truncated]"
        return result

    def _minimal_env(self) -> dict[str, str]:
        """Create a minimal, sanitized environment for subprocesses."""
        clean: dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"),
            "PYTHONPATH": str(self.workspace / "src"),
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "HOME": str(self.workspace),
        }
        if "VIRTUAL_ENV" in os.environ:
            clean["VIRTUAL_ENV"] = os.environ["VIRTUAL_ENV"]
        return clean

    # ---------------------------------------------------------------- Tool implementations

    def _tool_list_files(self, path: str = ".", pattern: str | None = None) -> ToolResult:
        try:
            target_dir = self._resolve_safe_path(path)
        except ValueError as err:
            return ToolResult(ok=False, error=str(err))

        if not target_dir.exists():
            return ToolResult(ok=False, error=f"Directory '{path}' does not exist")
        if not target_dir.is_dir():
            return ToolResult(ok=False, error=f"'{path}' is not a directory")

        ignored = {".git", ".venv", "__pycache__", ".pytest_cache"}
        entries: list[str] = []

        if pattern:
            files_gen = target_dir.glob(pattern)
        else:
            files_gen = target_dir.rglob("*")

        for p in files_gen:
            try:
                rel = p.resolve().relative_to(self.workspace)
            except ValueError:
                continue
            if any(part in ignored for part in rel.parts):
                continue
            if p.is_file():
                entries.append(str(rel))

        entries.sort()
        truncated = False
        if len(entries) > MAX_LIST_ENTRIES:
            entries = entries[:MAX_LIST_ENTRIES]
            truncated = True

        return ToolResult(ok=True, output=entries, truncated=truncated)

    def _tool_read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        try:
            file_path = self._resolve_safe_path(path)
        except ValueError as err:
            return ToolResult(ok=False, error=str(err))

        if not file_path.exists():
            return ToolResult(ok=False, error=f"File '{path}' does not exist")
        if not file_path.is_file():
            return ToolResult(ok=False, error=f"'{path}' is not a regular file")

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ToolResult(ok=False, error=f"Failed to read file: {exc}")

        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return ToolResult(ok=True, output="", truncated=False)

        start = start_line if start_line is not None else 1
        if start < 1:
            return ToolResult(ok=False, error="start_line must be >= 1")
        if start > total_lines:
            return ToolResult(ok=True, output="", truncated=False)

        if end_line is not None:
            if end_line < start:
                return ToolResult(ok=False, error=f"end_line ({end_line}) cannot be less than start_line ({start})")
            end = min(end_line, total_lines)
        else:
            end = total_lines

        count = end - start + 1
        truncated = False
        if count > MAX_READ_LINES:
            end = start + MAX_READ_LINES - 1
            truncated = True
        elif end_line is None and total_lines > MAX_READ_LINES:
            truncated = True

        numbered = [f"{i:4d} | {lines[i - 1]}" for i in range(start, end + 1)]
        output_str = "\n".join(numbered)
        return ToolResult(ok=True, output=output_str, truncated=truncated)

    def _tool_search_code(self, pattern: str, glob: str | None = None, max_results: int | None = None) -> ToolResult:
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(ok=False, error=f"Invalid regex pattern: {exc}")

        limit = max_results if max_results is not None else MAX_SEARCH_RESULTS
        limit = min(limit, MAX_SEARCH_RESULTS)

        ignored = {".git", ".venv", "__pycache__", ".pytest_cache"}
        matches: list[str] = []
        truncated = False

        if glob:
            candidates = self.workspace.glob(glob)
        else:
            candidates = self.workspace.rglob("*")

        for file_path in candidates:
            try:
                rel = file_path.resolve().relative_to(self.workspace)
            except ValueError:
                continue
            if any(part in ignored for part in rel.parts):
                continue
            if not file_path.is_file():
                continue

            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line_idx, line in enumerate(f, start=1):
                        if regex.search(line):
                            clean_line = line.rstrip("\r\n")
                            matches.append(f"{rel}:{line_idx}: {clean_line}")
                            if len(matches) >= limit:
                                truncated = True
                                break
            except Exception:
                continue

            if truncated:
                break

        return ToolResult(ok=True, output="\n".join(matches), truncated=truncated)

    def _tool_run_tests(self, target: str | None = None) -> ToolResult:
        if target:
            target_path_str = target.split("::")[0]
            try:
                safe_target = self._resolve_safe_path(target_path_str)
                if not safe_target.exists():
                    return ToolResult(ok=False, error=f"Target file '{target_path_str}' does not exist")
            except ValueError as err:
                return ToolResult(ok=False, error=str(err))

        with tempfile.NamedTemporaryFile(suffix=".xml") as xml_file:
            cmd = [sys.executable, "-m", "pytest", f"--junitxml={xml_file.name}", "-q", "--tb=short"]
            if target:
                cmd.append(target)

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=self.workspace,
                    env=self._minimal_env(),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(ok=False, error=f"Tests timed out after {self.timeout_s}s")
            except Exception as exc:
                return ToolResult(ok=False, error=f"Failed to execute pytest: {exc}")

            try:
                tree = ET.parse(xml_file.name)
                root = tree.getroot()
                suite = root.find("testsuite") if root.tag == "testsuites" else root
                if suite is None:
                    suite = root

                tests = int(suite.attrib.get("tests", 0))
                failures = int(suite.attrib.get("failures", 0))
                errors = int(suite.attrib.get("errors", 0))
                skipped = int(suite.attrib.get("skipped", 0))
                failed = failures + errors
                passed = max(0, tests - failed - skipped)

                failure_items: list[dict[str, Any]] = []
                for tc in root.iter("testcase"):
                    for child in tc:
                        if child.tag in ("failure", "error"):
                            cname = tc.attrib.get("classname", "")
                            tname = tc.attrib.get("name", "")
                            msg = child.attrib.get("message", "")
                            tb = (child.text or "").strip()
                            failure_items.append({
                                "test": f"{cname}.{tname}" if cname else tname,
                                "name": tname,
                                "message": msg,
                                "traceback": tb[-500:] if len(tb) > 500 else tb,
                            })

                structured_summary = {
                    "total": tests,
                    "passed": passed,
                    "failed": failed,
                    "skipped": skipped,
                    "failures": failure_items,
                }
                return ToolResult(ok=True, output=structured_summary)
            except Exception as exc:
                # If XML parsing failed, return raw output summary
                return ToolResult(
                    ok=True,
                    output={"exit_code": proc.returncode, "stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:], "error": str(exc)},
                )

    def _tool_edit_file(self, path: str, old_str: str, new_str: str) -> ToolResult:
        try:
            file_path = self._resolve_safe_path(path)
        except ValueError as err:
            return ToolResult(ok=False, error=str(err))

        src_dir = (self.workspace / "src").resolve()
        try:
            file_path.relative_to(src_dir)
        except ValueError:
            return ToolResult(ok=False, error="Security violation: edit_file is only permitted for files inside 'src/'")

        if file_path == src_dir:
            return ToolResult(ok=False, error="Cannot edit directory")
        if not file_path.exists():
            return ToolResult(ok=False, error=f"File '{path}' does not exist")
        if not file_path.is_file():
            return ToolResult(ok=False, error=f"'{path}' is not a regular file")

        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(ok=False, error=f"Could not read '{path}': {exc}")

        occurrences = content.count(old_str)
        if occurrences == 0:
            return ToolResult(ok=False, error=f"old_str was not found in '{path}'")
        if occurrences > 1:
            return ToolResult(
                ok=False,
                error=f"old_str occurs {occurrences} times in '{path}'; must occur exactly once to ensure unique replacement",
            )

        new_content = content.replace(old_str, new_str, 1)
        try:
            file_path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(ok=False, error=f"Failed to write file '{path}': {exc}")

        return ToolResult(ok=True, output=f"Successfully edited '{path}'.")

    def _tool_run_cli(self, args: list[str]) -> ToolResult:
        if not isinstance(args, list):
            return ToolResult(ok=False, error="args must be a list of strings")

        disallowed_chars = {";", "&", "|", "`", "$", "\n", "\r"}
        for arg in args:
            if not isinstance(arg, str):
                return ToolResult(ok=False, error=f"All arguments must be strings, got {type(arg).__name__}")
            if any(char in arg for char in disallowed_chars):
                return ToolResult(ok=False, error=f"Disallowed metacharacter in CLI argument: '{arg}'")
            if arg.startswith("/") or arg.startswith("\\") or arg.startswith("~"):
                return ToolResult(ok=False, error=f"Absolute path in argument not permitted: '{arg}'")
            if ".." in Path(arg).parts:
                return ToolResult(ok=False, error=f"Path traversal in argument not permitted: '{arg}'")

        cmd = [sys.executable, "-m", "tinyledger"] + args
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.workspace,
                env=self._minimal_env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error=f"CLI command timed out after {self.timeout_s}s")
        except Exception as exc:
            return ToolResult(ok=False, error=f"Failed to execute CLI command: {exc}")

        if proc.returncode == 0:
            return ToolResult(ok=True, output={"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr})
        return ToolResult(ok=False, error=f"CLI failed with exit code {proc.returncode}:\n{proc.stderr or proc.stdout}")

    # ---------------------------------------------------------------- Dispatch

    def execute(self, name: str, arguments: str) -> ToolResult:
        """Validate and run one tool call. Never raises."""
        try:
            if name not in self._schema_map:
                return ToolResult(
                    ok=False,
                    error=f"Unknown tool: '{name}'. Available tools: {list(self._schema_map.keys())}",
                )

            if not isinstance(arguments, str):
                arguments = str(arguments)

            try:
                parsed_args = json.loads(arguments)
            except json.JSONDecodeError as exc:
                return ToolResult(ok=False, error=f"Invalid JSON in arguments: {exc}")

            if not isinstance(parsed_args, dict):
                return ToolResult(ok=False, error="Arguments must be a JSON object (key-value dictionary)")

            validator = self._validators[name]
            errors = list(validator.iter_errors(parsed_args))
            if errors:
                err_messages = [
                    f"'{'/'.join(map(str, e.path)) or 'root'}': {e.message}"
                    for e in errors
                ]
                return ToolResult(ok=False, error=f"Schema validation error: {'; '.join(err_messages)}")

            # Dispatch to tool function
            if name == "list_files":
                result = self._tool_list_files(**parsed_args)
            elif name == "read_file":
                result = self._tool_read_file(**parsed_args)
            elif name == "search_code":
                result = self._tool_search_code(**parsed_args)
            elif name == "run_tests":
                result = self._tool_run_tests(**parsed_args)
            elif name == "edit_file":
                result = self._tool_edit_file(**parsed_args)
            elif name == "run_cli":
                result = self._tool_run_cli(**parsed_args)
            else:
                result = ToolResult(ok=False, error=f"Unhandled tool: '{name}'")

            return self._bound_result(result)
        except Exception as exc:
            # Catch-all to guarantee execute() never raises
            return ToolResult(ok=False, error=f"Internal tool error: {exc}")
