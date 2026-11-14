"""Autonomous debugging agent with native function calling for Task 4.

Interacts with the tool registry to diagnose and fix bugs in tinyledger.
Supports running with --mock (MockOpenAIClient) or with the UPB Gateway LLM.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add repo root and task4_tools to sys.path
TASK_DIR = Path(__file__).resolve().parent
REPO_ROOT = TASK_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(TASK_DIR) not in sys.path:
    sys.path.insert(0, str(TASK_DIR))

from common.llm_client import LLMClient, message_to_dict
from tools import ToolRegistry


def log_trace(trace_file: Path, name: str, arguments: str, result, duration_s: float) -> None:
    """Append a structured record for each tool call to trace.jsonl."""
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "name": name,
        "arguments": arguments,
        "ok": result.ok,
        "error": result.error,
        "truncated": result.truncated,
        "duration_s": round(duration_s, 4),
    }
    with trace_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_agent(mock: bool = False, max_steps: int = 30, workspace_path: Path | None = None) -> None:
    ws = Path(workspace_path or (TASK_DIR / "workspace")).resolve()
    if not ws.exists():
        print(f"Workspace not found at {ws}. Running reset_workspace.py...")
        from reset_workspace import main as reset_main
        reset_main()

    trace_file = TASK_DIR / "logs" / "trace.jsonl"
    # Ensure logs folder exists
    (TASK_DIR / "logs").mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry(workspace=ws)

    if mock:
        from mock_model import MockOpenAIClient
        print("Initializing agent with MockOpenAIClient (--mock mode)...")
        llm = LLMClient(
            model="mock",
            log_path=TASK_DIR / "logs" / "mock_llm.jsonl",
            openai_client=MockOpenAIClient(),
        )
    else:
        print("Initializing agent with Gateway LLM...")
        llm = LLMClient(log_path=TASK_DIR / "logs" / "agent_llm.jsonl")

    system_prompt = (
        "You are an expert autonomous software engineering agent tasked with debugging a Python codebase.\n"
        "Your task: Make all tests pass and fix the problem described in ISSUE.md. Do not modify tests.\n"
        "Follow these rules:\n"
        "1. Never edit tests, data, or config files. Only edit files under src/.\n"
        "2. Inspect files, read code, run tests, and locate failure points before applying edits.\n"
        "3. Use edit_file with precise old_str and new_str strings that occur exactly once.\n"
        "4. Verify that all tests pass and verify that the issue in ISSUE.md is resolved before finishing."
    )
    user_prompt = "Make all tests pass and fix the problem described in ISSUE.md. Do not modify tests."

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    print(f"Starting agent loop in workspace: {ws}")
    print(f"Step limit: {max_steps}")
    print("=" * 60)

    for step in range(1, max_steps + 1):
        print(f"\n[Step {step}] Requesting LLM action...")
        message = llm.chat_with_tools(messages, tools=registry.schemas())
        messages.append(message_to_dict(message))

        if message.content:
            print(f"Assistant: {message.content.strip()}")

        tool_calls = getattr(message, "tool_calls", None)
        if not tool_calls:
            print(f"\n[Done] Agent terminated gracefully with final answer at step {step}.")
            break

        print(f"Received {len(tool_calls)} tool call(s):")
        for tc in tool_calls:
            fn_name = tc.function.name
            fn_args = tc.function.arguments
            print(f"  -> Tool: {fn_name}({fn_args[:100]}{'...' if len(fn_args) > 100 else ''})")

            t0 = time.time()
            result = registry.execute(fn_name, fn_args)
            duration = time.time() - t0

            log_trace(trace_file, fn_name, fn_args, result, duration)

            status = "OK" if result.ok else f"ERR: {result.error}"
            print(f"     Result [{status}] ({duration:.3f}s, truncated={result.truncated})")

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result.to_content(),
            })
    else:
        print(f"\n[Warning] Agent reached max steps limit ({max_steps}).")

    print("\n" + "=" * 60)
    print("Agent Run Summary:")
    print(f"Total API calls: {llm.n_calls}")
    print(f"Total prompt tokens: {llm.total_prompt_tokens}")
    print(f"Total completion tokens: {llm.total_completion_tokens}")
    print(f"Total cost USD: ${llm.total_cost_usd:.4f}")
    print(f"Trace log written to: {trace_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Task 4 Coding Agent")
    parser.add_argument("--mock", action="store_true", help="Run with scripted MockOpenAIClient")
    parser.add_argument("--max-steps", type=int, default=30, help="Maximum number of turns")
    parser.add_argument("--workspace", type=str, default=None, help="Path to tinyledger workspace")
    args = parser.parse_args()

    workspace_path = Path(args.workspace) if args.workspace else None
    run_agent(mock=args.mock, max_steps=args.max_steps, workspace_path=workspace_path)


if __name__ == "__main__":
    main()
