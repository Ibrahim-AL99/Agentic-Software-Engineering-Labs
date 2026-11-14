# Task 2 – Our Own Coding Agent

## How to run

From the repository root:

```bash
uv run python task2_own_agent/agent.py \
    --content task1_opencode/content \
    --out task2_own_agent/site/index.html
```

Available options:

| Option | Default | Description |
|---|---|---|
| `--content DIR` | `task1_opencode/content` | Directory with `<firstname>.md` profiles |
| `--out FILE` | `task2_own_agent/site/index.html` | Output HTML file |
| `--max-steps N` | `25` | Maximum agent steps before stopping |
| `--k N` | `5` | Number of chunks returned per style-guide search |
| `--model NAME` | gateway default | Override the chat model |

To verify the generated site:

```bash
uv run python task2_own_agent/check_site.py task2_own_agent/site/index.html --content task1_opencode/content
```

---

## Design decisions

### Chunking strategy

Documents are split on double newlines (paragraph boundaries). Short consecutive paragraphs are merged until the chunk reaches 200 tokens, measured with `count_tokens` from `common/llm_client.py`. This keeps chunks semantically coherent — a heading stays with the paragraph below it — while staying within a predictable size. Single paragraphs that exceed 200 tokens are kept as one chunk rather than split mid-sentence.

### Embedding model

We use the gateway embedding model (`kiconnect.qwen3-embedding-8b`) via `llm.embed()`, which is free. Vectors are L2-normalised and stored in a NumPy float32 matrix. Query vectors are normalised the same way, so retrieval is a simple matrix–vector dot product (cosine similarity). No external vector database is used.

### k = 5

Each `search_style_guide` call returns 5 chunks, capped at 1 000 tokens total. With 55 style guide documents chunked into ~55 pieces, k = 5 gives enough coverage per query without flooding the context window. The agent makes multiple targeted searches (colors, theme, typography, layout, etc.) rather than one broad search, so a small k per query is sufficient.

### Prompt design

The system prompt explains the goal, the JSON action protocol, and the four available actions. It deliberately contains no style guide text — the agent must discover all rules via `search_style_guide`. Good search queries are suggested (e.g. "colors day mode", "theme persistence", "hot take badge") to guide the agent toward the right documents without giving away the answers. The `finish` action is blocked until `write_site` passes all checks, which forces the agent to fix failures rather than skip them.

Images are handled with `{{image:filename.svg}}` placeholders in the HTML. The agent is explicitly told never to write base64 data itself; `embed_images()` resolves the placeholders after the HTML is written.

### Error handling

- **Invalid JSON**: if no valid JSON action is found in the model response, an error observation is sent back and the loop continues. The step is counted.
- **Unknown action**: an error observation lists the valid actions.
- **Missing HTML for `write_site`**: an error observation explains the expected code-block format.
- **Image not found**: `embed_images()` raises `FileNotFoundError`; the error message is returned as the observation so the agent can correct the filename.
- **`finish` before passing checks**: rejected with the last checker report so the agent knows what to fix.
- **Non-interactive stdin** (e.g. piped input): `EOFError` on the human-feedback prompt is caught and treated as empty input (accept).

---

## Known limitations

- **No chunk overlap**: paragraphs are not duplicated across chunk boundaries. A rule that spans two paragraphs might be split, reducing retrieval accuracy for that rule.
- **Single-pass embedding**: the index is built once at startup. If the style guide changes during a run, the index is stale.
- **No re-ranking**: retrieval is pure cosine similarity. A cross-encoder re-ranker would improve precision but was not used to keep dependencies minimal.
- **Context grows with steps**: the full message history is kept in memory and sent to the model every step. Very long runs (many write-fix cycles) can approach the context limit.
- **Placeholder content**: member profiles currently contain placeholder text. The site must be regenerated after teammates fill in their real information.
