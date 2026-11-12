# Task 2 – Our own coding agent (Group 14)

## How to run

From the repository root, with `.env` set up and connected to the university network or VPN:

```bash
uv run python task2_own_agent/agent.py --content task1_opencode/content --out task2_own_agent/site/index.html
```

When the agent's `finish` is accepted, the terminal asks for feedback. Type feedback to start
another round, or press Enter to end the run.

| Option | Default | Meaning |
|---|---|---|
| `--content` | `task1_opencode/content` | profiles (`*.md`, `_template.md` is skipped) and `images/` |
| `--out` | `task2_own_agent/site/index.html` | where the site is written |
| `--team` | `Group 14` | team name used in the task prompt |
| `--model` | `$LAB_CHAT_MODEL` | chat model (e.g. `kiconnect.qwen3.8-27b` if the budget is used up) |
| `--max-steps` | `25` | step limit **per round** (the counter resets after human feedback) |
| `--k` | `5` | chunks per `search_style_guide` call |
| `--max-tokens` | `32000` | max output tokens per call (page + hidden reasoning tokens) |
| `--log-dir` | `task2_own_agent/logs` | each run writes the next free `run_XX.jsonl` |
| `--no-feedback` | off | skip the human feedback prompt (unattended runs) |

Other commands:

```bash
uv run python task2_own_agent/rag.py          # retrieval sanity check: 12 queries with known answers
uv run pytest task2_own_agent/tests -q        # offline tests with a fake model (no gateway needed)
uv run python task2_own_agent/check_site.py task2_own_agent/site/index.html --full --content task1_opencode/content
```

## Files

| File | Content |
|---|---|
| `rag.py` | loading, chunking, embedding (+ disk cache), `search(query, k)`, observation formatting with the token budget |
| `agent.py` | JSON protocol parser and validator, the four actions, the loop, human feedback, CLI |
| `tests/test_agent.py` | 21 offline tests: parser errors, token budget, deprecated docs, full loop with feedback, step limit, truncation, repetition, context compaction |
| `logs/run_XX.jsonl` | complete log of every run (see "Logs" below) |

## Design decisions

**Chunking.** The 55 documents are short (24–169 tokens, 4k words in total). We split each document
into blocks: paragraphs, with headings starting a new block, and code blocks and lists kept whole. Blocks
are merged greedily up to **120 tokens**, with one short block of overlap between chunks. Result:
55 documents → 63 chunks.
- At 120 tokens almost every rule document stays one chunk, so a rule stays together with its exact
  values. At 80 tokens, documents like SG-13 (night palette) were split apart from their colour values.
- Only the multi-topic documents are split (FAQ, changelog, review checklist, page structure,
  glossary). These would otherwise match many unrelated queries.
- Each chunk starts with a header `[SG-13 | Night mode palette (version 2) | Status: ACTIVE (version 2)]`.
  The header is embedded together with the text, so a fragment still knows its rule, and the model
  always sees the source id and the status.

**Embeddings.** We use `kiconnect.qwen3-embedding-8b` through `llm.embed()`. It is free and needs no local model.
Queries get the Qwen3 instruction prefix (documents don't). Vectors are L2-normalised and kept in
a numpy matrix, so cosine similarity is a single matrix-vector product. The matrix is cached in
`task2_own_agent/.cache/` (gitignored), keyed by a hash of the chunk texts and the model name.

**k = 5.** Five chunks of at most about 150 tokens (with header) use about 350–600 tokens, which stays below
the 1,000-token limit with room to spare. A typical topic touches 2–3 documents (for example the active
rule, its deprecated predecessor, and the FAQ), so k = 3 would often miss one of them.

**Token budget.** `format_observation()` adds chunks in rank order and measures the whole
observation text with `count_tokens`. A chunk that would push it over 1,000 tokens is dropped and its
id is listed, so the model can search more specifically. The largest observation in our runs was 609 tokens.

**Deprecated rules.** Five documents are deprecated (SG-10, 12, 19, 39, 49). Retrieval often ranks
them highly; for "day mode background color", the deprecated SG-10 ranks *above* the active SG-11.
We handle this at two levels:
1. Every chunk shows its status. The system prompt says to follow ACTIVE documents only.
2. If a deprecated chunk is retrieved and its replacement (`replaced by SG-xx`) is not in the
   results, `search()` appends the best chunk of the replacement and marks it
   `(added because SG-12 is deprecated)`. We keep the deprecated chunk on purpose: the model should
   see that a rule changed instead of missing it.

**Prompt design.** The system prompt has the protocol, the action list, and generic rules:
search before writing, never guess values, ACTIVE beats DEPRECATED, use image placeholders, always write the complete
page. The task prompt has the Task 1 requirements. **Neither contains style guide text.** The
agent finds every rule (title format, colours, ids, footer, …) through `search_style_guide`. The
checker messages are deliberately vague, so failing checks also have to be looked up.

**Protocol.** One JSON object per reply, `{"thought", "action", "args"}`. For `write_site` the HTML goes in
a ```` ```html ```` block after the JSON (`args.html` is accepted too). The parser takes the first JSON
object *before* the HTML block, so fences and text around it are tolerated. The HTML block ends at the
*last* ```` ``` ````, so code fences inside the page don't cut it short.

**Invalid output.** This never crashes the loop. Each case below sends an `ERROR:` observation back to the
model and counts as a step:

| Problem | Reaction |
|---|---|
| empty reply, no JSON, no `action` | error + a reminder of the expected format |
| unknown action | error + list of valid actions |
| missing, unknown, wrong-typed or empty args | error naming the problem and the expected args |
| `write_site` without HTML, or an unclosed HTML block | error (an unclosed block usually means the page was cut off) |
| `finish_reason == "length"` | error: "reply was cut off, keep the page compact" |
| base64 image data written by the model | rejected, nothing saved, model told to use `{{image:...}}` |
| unknown image file | error from `embed_images` listing the available files, nothing saved |
| an exception inside an action | caught, returned as an error |
| the same action + args 3× in a row | a warning is appended to the observation |
| `finish` before any write, after a failing report, or before answering human feedback | rejected with the reason (for failing checks, the list of failures) |
| no accepted `finish` after `--max-steps` | the loop stops with a clear message; the last page stays on disk |

**Context growth.** Every request re-sends the whole history. In our test run the prompt grew by
about 600 tokens per step (search results). It jumped by about 4.5k tokens after the first
`write_site`, because the page itself is in the history (498 → 12,666 prompt tokens over 13 steps). To keep this from adding up,
**only the newest page is kept in the history**. Older `write_site` messages are replaced by a short note
(the full HTML stays in the log). Without this, every rewrite would add another 4–5k tokens to every later request.

**Human in the loop.** After an accepted `finish`, the user is asked for feedback on the terminal.
Feedback is appended as a user message and the loop continues. `finish` is rejected until the agent has
written a new version that passes the checks, so feedback can't just be acknowledged and ignored.
Empty input (or EOF) ends the run.

## Logs

`LLMClient(log_path=logs/run_XX.jsonl)` logs every request (the full message list) and response with
usage and cost. We add our own `event` entries with `log_event()`: `run_start`, `observation` (step
number, action, error flag, token count, full text), `retrieval` (query, doc ids, scores, tokens
used), `checker` (full report per page version), `human_feedback`, `step_limit`, `run_end` (usage
summary).

## Known limitations

- **The checker is the only quality gate.** `finish` requires `check_site` to pass, but the checker
  tests specific strings and selectors. Rules it doesn't test (bio length, tone, spacing scale, touch
  targets, contrast) are only followed if the model retrieved them and chose to apply them.
- **Retrieval is purely dense.** Exact identifiers (`#14142B`, `SG-13`) may work better with BM25 or
  hybrid search (see Task 3). The replacement mechanism relies on the `replaced by SG-xx` status line.
  A deprecation that is only mentioned in the text (for example in the changelog) is not linked.
- **Full rewrites.** Every `write_site` re-sends the complete page (about 4–5k output tokens). An
  edit/patch action would be cheaper but adds failure modes (patches that don't apply).
- **Context still grows linearly** with search results. With 100 steps the prompt would reach about
  60–70k tokens. That still fits the context window but is expensive and dilutes attention. Summarising
  old observations would be the next step.
- **No native tool calling** (by design): the format is enforced by validation and retries, not
  guaranteed by constrained decoding, so malformed replies cost a step.
- `count_tokens` uses `cl100k_base`, which is only an estimate of the gateway models' tokenizers.
- `_call_model` uses `LLMClient._chat_request` (a private method) because `chat()` does not return
  `finish_reason`, which we need to detect cut-off replies.

---

## Provided task specification

There is no starter code for this task, only this specification and some helpers.
Read the task description on the lab sheet first.

### What's in this folder

| File | Purpose |
|---|---|
| `style_guide/` | The fictional "UPB Agentic SE Web Style Guide" (55 Markdown documents) |
| `check_site.py` | Checks a website against the basic requirements AND the style guide (`--basic` for basic only) |
| `embed_images.py` | Replaces `{{image:<file>}}` placeholders with embedded base64 images |

Helpers you can import from `common/` (run your code from the repository root, or add it to `sys.path`):

```python
from common.llm_client import LLMClient, count_tokens
from common.site_checker import check_site, member_names_from_content
from common.embed_images import embed_images
```

### Specification

### Allowed dependencies
Python standard library, `numpy`, `rank-bm25`, `tiktoken`, the provided `common/` helpers,
optionally `sentence-transformers` for local embeddings.
Not allowed: LangChain, LlamaIndex, Haystack, agent SDKs, vector databases.

### RAG component – `rag.py`
- Loads all documents from `style_guide/` and splits them into chunks.
- Embeds the chunks, keeps the vectors in memory (a numpy matrix is enough).
- Provides `search(query: str, k: int) -> list[Chunk]`, returning the most similar chunks by cosine similarity.
  Each chunk carries its text and the id of its source document (e.g. `SG-13`).
- Context budget rules:
  - one `search_style_guide` observation may contain at most **1,000 tokens** of style guide text
    (measure with `count_tokens` from `common/llm_client.py`)
  - your system prompt and task description must not contain style guide text

### Agent loop – `agent.py`
The model answers with exactly one JSON object per step:

```json
{"thought": "short reasoning", "action": "<action name>", "args": {}}
```

Required actions:

| Action | Args | What your code does | Observation returned to the model |
|---|---|---|---|
| `search_style_guide` | `{"query": str}` | calls `rag.search` | retrieved chunks with document ids (≤ 1,000 tokens) |
| `read_profiles` | `{}` | reads all `content/*.md` files (not `_template.md`) | profile texts + list of available image files |
| `write_site` | `{}` + the HTML in a ` ```html ` code block directly after the JSON (or `{"html": str}`) | resolves `{{image:...}}` placeholders, saves the file, runs `check_site(..., mode="full")` | the checker report (or an error, e.g. unknown image) |
| `finish` | `{"summary": str}` | accepted only if the last checker report passed | if rejected: the failing checks |

Why the extra code block for `write_site`? A whole HTML page inside a JSON string needs thousands of escaped
quotes and newlines, and models get that wrong surprisingly often. You may support both variants.

Behaviour your loop must have:
- A maximum number of steps (e.g. 25). Stop with a clear message when it is reached.
- If the model output is not valid JSON or uses an unknown action or wrong args: don't crash.
  Send an error observation back and continue. Count it as a step.
- The model must never write base64 image data. It uses placeholders like `<img src="{{image:anna.jpg}}" alt="...">`.
- Human in the loop: after an accepted `finish`, ask the user for feedback on the terminal.
  Non-empty feedback is appended as a new user message and the loop continues. Empty input ends the run.
- Don't use the native function-calling / `tools` API. You build the protocol yourselves.
- A complete website is several thousand output tokens, plus the model's hidden reasoning tokens. Set `max_tokens` high
  enough (or don't set it), otherwise the answer is cut off (the log shows `"finish_reason": "length"`).
- Embeddings: use the gateway embedding model via `llm.embed(...)` (free), or a local model.

### Command line
From the repository root:

```bash
uv run python task2_own_agent/agent.py --content task1_opencode/content --out task2_own_agent/site/index.html
```

You may add options (model, max steps, k, ...). Document them in your README.

### Logs
- Store every message sent to and received from the model, with step numbers, in `task2_own_agent/logs/run_XX.jsonl`.
- The simplest way: use `LLMClient(log_path=...)` and add your own entries (observations, human feedback) with `llm.log_event({...})`.
- Submit the logs of at least one complete run that produced your submitted `site/index.html`.

### What your README must contain
- how to run the agent
- your design decisions: chunking, embedding model, k, prompt design, how you handle invalid output
- known limitations
