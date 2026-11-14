"""Task 2 – Agent loop for building a team website.

Run from the repository root:
uv run python task2_own_agent/agent.py \
  --content task1_opencode/content \
  --out task2_own_agent/site/index.html

Options:
    --content DIR   directory with <firstname>.md profiles (default: task1_opencode/content)
    --out FILE      output HTML path (default: task2_own_agent/site/index.html)
    --max-steps N   maximum agent steps (default: 25)
    --k N           chunks returned per style-guide search (default: 5)
    --model NAME    override the chat model
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from common.llm_client import LLMClient, count_tokens  # noqa: E402
from common.site_checker import check_site, member_names_from_content  # noqa: E402
from common.embed_images import embed_images  # noqa: E402

# rag.py lives in the same directory; Python adds it to sys.path automatically
from rag import RAG, Chunk  # noqa: E402

# Constants

STYLE_GUIDE_DIR = Path(__file__).parent / "style_guide"
LOG_DIR = Path(__file__).parent / "logs"
MAX_OBS_TOKENS = 1000  # style-guide observation budget per search

SYSTEM_PROMPT = """\
You are an agent that builds a single-file team website for a university course.

GOAL
Produce one HTML file (HTML + CSS + JS, nothing loaded from the internet) that:
- Introduces every team member with their profile and photo.
- Follows every rule in the "UPB Agentic SE Web Style Guide" exactly.
- Passes all automated checks (you will see the checker report after write_site).

PROTOCOL
Reply with EXACTLY ONE JSON object per step — no text before or after it:
  {"thought": "<brief reasoning>", "action": "<action>", "args": {<args>}}

For write_site, append the full HTML in a ```html code block DIRECTLY after the JSON
on the same response (no blank line between them):
  {"thought": "...", "action": "write_site", "args": {}}
  ```html
  <!DOCTYPE html>
  ...
  ```

ACTIONS
  search_style_guide  args: {"query": "<topic>"}
      Searches the style guide. Returns up to 1000 tokens of matching rules.
      Call this multiple times for different topics before writing HTML.

  read_profiles       args: {}
      Returns all team member profiles and the list of available image files.

  write_site          args: {}  (HTML in ```html block after JSON)
      Embeds images, saves the file, runs the checker. Returns the full report.
      Repeat until all checks pass.

  finish              args: {"summary": "<what you built>"}
      Accepted only if the last write_site passed all checks.

RULES
- Images: use <img src="{{image:filename.svg}}" alt="...">  — NEVER write base64 yourself.
- Search the style guide BEFORE writing HTML. Good queries: colors day mode, colors night mode,
  accent color, theme toggle, theme persistence, member cards markup, hot take badge,
  footer text, page title format, fonts, breakpoints, page sections, heading hierarchy,
  reduced motion, navigation, single file delivery.
- Some style guide documents are DEPRECATED. If you find a contradiction, the ACTIVE document wins.
- The checker is strict. Fix every FAIL in the report before calling finish.
"""

# Helpers


def _next_log_path() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(LOG_DIR.glob("run_*.jsonl"))
    n = len(existing) + 1
    return LOG_DIR / f"run_{n:02d}.jsonl"


def _parse_response(text: str) -> tuple[dict | None, str | None]:
    """Extract the first JSON action dict and optional HTML from the model response.

    Returns (action_dict, html_string).  Either may be None.
    """
    decoder = json.JSONDecoder()
    action_dict: dict | None = None
    json_end = -1

    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, length = decoder.raw_decode(text, i)
            if isinstance(obj, dict) and "action" in obj:
                action_dict = obj
                json_end = i + length
                break
        except json.JSONDecodeError:
            continue

    if action_dict is None:
        return None, None

    # HTML from args.html (alternative format)
    html: str | None = None
    args = action_dict.get("args")
    if isinstance(args, dict):
        html = args.get("html")

    # HTML from ```html ... ``` code block after the JSON
    after = text[json_end:]
    block = re.search(r"```html\s*\n(.*?)\n```", after, re.DOTALL)
    if block:
        html = block.group(1).strip()

    return action_dict, html


def _format_chunks(chunks: list[Chunk]) -> str:
    """Format retrieved chunks, capped at MAX_OBS_TOKENS total tokens."""
    parts: list[str] = []
    total = 0
    for chunk in chunks:
        entry = f"[{chunk.doc_id}]\n{chunk.text}"
        t = count_tokens(entry)
        if total + t > MAX_OBS_TOKENS:
            break
        parts.append(entry)
        total += t
    if not parts and chunks:
        # Include at least a truncation of the top chunk
        entry = f"[{chunks[0].doc_id}]\n{chunks[0].text}"
        # Approximate truncation to MAX_OBS_TOKENS
        while count_tokens(entry) > MAX_OBS_TOKENS and len(entry) > 20:
            entry = entry[: int(len(entry) * 0.85)]
        parts.append(entry + "\n[truncated]")
        total = count_tokens(parts[0])
    return "\n\n---\n\n".join(parts) + f"\n\n({len(parts)} chunk(s), ~{total} tokens)"


# Main loop


def run(
    content_dir: Path,
    out_path: Path,
    max_steps: int = 25,
    k: int = 5,
    model: str | None = None,
) -> None:
    log_path = _next_log_path()
    llm = LLMClient(model=model, log_path=log_path)

    print(f"Log: {log_path}")
    print("Building style-guide index (embedding ~55 documents)...")
    rag = RAG(STYLE_GUIDE_DIR, llm)
    print(f"  {len(rag.chunks)} chunks indexed.")

    member_names = member_names_from_content(content_dir)
    images_dir = content_dir / "images"
    print(f"Members: {member_names}")

    llm.log_event(
        {"type": "agent_start", "members": member_names, "out": str(out_path)}
    )

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Build the team website.\n"
                f"Team members: {', '.join(member_names)}.\n"
                f"Output file: {out_path}"
            ),
        },
    ]

    last_check_passed = False
    last_report_summary = ""

    for step in range(1, max_steps + 1):
        print(f"\n=== Step {step}/{max_steps} ===")

        print("  Calling model...")
        response = llm.chat(messages)
        print(f"  Response length: {len(response)} chars")
        messages.append({"role": "assistant", "content": response})

        action_dict, html = _parse_response(response)

        # parse failure
        if action_dict is None:
            obs = (
                "ERROR: could not find a valid JSON action in your response. "
                'Reply with exactly one JSON object: {"thought": "...", "action": "...", "args": {...}}'
            )
            print("  [parse error]")
            llm.log_event(
                {
                    "type": "observation",
                    "step": step,
                    "action": "PARSE_ERROR",
                    "obs": obs,
                }
            )
            messages.append(
                {"role": "user", "content": f"Observation (step {step}):\n{obs}"}
            )
            continue

        action = action_dict.get("action", "")
        args = action_dict.get("args") or {}
        thought = action_dict.get("thought", "")
        print(f"  action={action}  thought={thought[:100]}")

        # ---- execute action -----------------------------------------------
        if action == "search_style_guide":
            query = args.get("query", "")
            if not isinstance(query, str) or not query.strip():
                obs = "ERROR: args must contain 'query' (non-empty string)."
            else:
                chunks = rag.search(query.strip(), k=k)
                obs = _format_chunks(chunks)
                print(f"  Retrieved {len(chunks)} chunks for '{query}'")

        elif action == "read_profiles":
            profiles: list[str] = []
            for md in sorted(content_dir.glob("*.md")):
                if md.name.startswith("_"):
                    continue
                profiles.append(f"=== {md.stem} ===\n{md.read_text(encoding='utf-8')}")
            available_images = (
                sorted(p.name for p in images_dir.iterdir())
                if images_dir.exists()
                else []
            )
            obs = "\n\n".join(profiles) + f"\n\nAvailable images: {available_images}"
            print(
                f"  Read {len(profiles)} profile(s), {len(available_images)} image(s)"
            )

        elif action == "write_site":
            if not html:
                obs = (
                    "ERROR: no HTML found. For write_site, append the full HTML in a "
                    "```html code block directly after the JSON object."
                )
            else:
                try:
                    html_embedded = embed_images(html, images_dir)
                except (FileNotFoundError, ValueError) as exc:
                    obs = f"ERROR embedding images: {exc}"
                    html_embedded = None

                if html_embedded is not None:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(html_embedded, encoding="utf-8")
                    report = check_site(
                        html_embedded,
                        mode="full",
                        member_names=member_names,
                        file_size=len(html_embedded.encode()),
                    )
                    last_check_passed = report.passed
                    last_report_summary = report.summary()
                    obs = last_report_summary
                    print(f"  Checker: {'PASSED' if report.passed else 'FAILED'}")

        elif action == "finish":
            summary = args.get("summary", "")
            if not last_check_passed:
                obs = (
                    "REJECTED: finish is only accepted after write_site passes all checks.\n"
                    + (
                        f"Last report:\n{last_report_summary}"
                        if last_report_summary
                        else "You have not run write_site yet."
                    )
                )
                print("  [finish rejected — checks not passed]")
            else:
                obs = f"ACCEPTED: {summary}"
                llm.log_event({"type": "finish", "step": step, "summary": summary})
                messages.append(
                    {"role": "user", "content": f"Observation (step {step}):\n{obs}"}
                )

                # Human in the loop
                print("\n--- Agent finished ---")
                try:
                    feedback = input(
                        "Your feedback (press Enter to accept, or type to continue): "
                    ).strip()
                except EOFError:
                    feedback = ""
                llm.log_event(
                    {"type": "human_feedback", "step": step, "feedback": feedback}
                )
                if not feedback:
                    print(f"\nDone. Site saved to {out_path}")
                    print(llm.usage_summary())
                    return
                # Non-empty feedback: continue the loop
                messages.append({"role": "user", "content": feedback})
                continue

        else:
            known = ["search_style_guide", "read_profiles", "write_site", "finish"]
            obs = f"ERROR: unknown action '{action}'. Valid actions: {', '.join(known)}"
            print(f"  [unknown action '{action}']")

        llm.log_event(
            {"type": "observation", "step": step, "action": action, "obs": obs[:500]}
        )
        messages.append(
            {"role": "user", "content": f"Observation (step {step}):\n{obs}"}
        )

    # Reached max steps
    print(f"\nMax steps ({max_steps}) reached.")
    print(llm.usage_summary())


# CLI


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--content",
        type=Path,
        default=Path("task1_opencode/content"),
        help="directory with <firstname>.md profiles",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("task2_own_agent/site/index.html"),
        help="output HTML file",
    )
    parser.add_argument("--max-steps", type=int, default=25)
    parser.add_argument(
        "--k", type=int, default=5, help="chunks per style-guide search"
    )
    parser.add_argument("--model", type=str, default=None, help="override chat model")
    args = parser.parse_args()
    run(args.content, args.out, args.max_steps, args.k, args.model)


if __name__ == "__main__":
    main()
