# Group 14 – Lab 1: From using agents to building them

Agentic Software Engineering, WS 2026/27, Paderborn University. Group name: *Humans in the Loop*.

## Group members

| Name | Matriculation number |
|---|---|
| Aryan | 4116442 |
| Cemon | 7398388 |
| Rachana | 4109798 |
| Shrividya | 7397075 |
| Srajan | 4108654 |

Who did what: [CONTRIBUTIONS.md](CONTRIBUTIONS.md).

## Setup

Python 3.11+ and [uv](https://docs.astral.sh/uv/). All commands are run from this directory (the repository root).
The UPB AI Gateway is only reachable from the university network or the university VPN.

```bash
uv sync                    # or: python -m venv .venv && pip install -r requirements.txt
uv sync --extra local      # only needed for Task 3 R5 (local cross-encoder, large download)

cp .env.example .env       # put your own gateway key into .env (model names are already filled in)
uv run python common/hello_gateway.py   # connection check
```

`.env` is not part of the submission. Models used: `openai.gpt-6-luna` for all chat, agent and judge calls,
`kiconnect.qwen3-embedding-8b` for embeddings. The free fallback model was not used.

## Repository layout

| Folder | Content |
|---|---|
| `common/` | shared course code: `llm_client.py` (gateway client with logging), `site_checker.py`, `embed_images.py` |
| `task1_opencode/` | profiles + images (`content/`), hello-world screenshots and site screenshots (`screenshots/`), the site built by OpenCode (`site/index.html`), exported OpenCode session (`conversation/`) |
| `task2_own_agent/` | our coding agent (`agent.py`), retriever (`rag.py`), style guide, tests, run log (`logs/run_01.jsonl`), the generated site (`site/index.html`), screenshots, `README.md` |
| `task3_rag/` | retrievers R1–R5, generation, evaluation, judge runs; all outputs in `results/`; `predictions_test.jsonl`; the report `REPORT.md` |
| `task4_tools/` | contained tool layer (`tools.py`), function-calling agent (`agent.py`), adversarial tests, traces (`logs/trace.jsonl`, `logs/mock_trace.jsonl`), `final.diff`, `README.md` |

## How to run

### Task 1 – Using an agent (OpenCode)

The site was written entirely by OpenCode (`gpt-6-luna`), see `task1_opencode/conversation/session_01.md`.
Nothing needs to be run to view it: open `task1_opencode/site/index.html` in a browser.

```bash
# check the site (Task 1 requirements: 7 basic checks; --full adds the Task 2 style-guide rules)
uv run python task1_opencode/check_site.py task1_opencode/site/index.html --content task1_opencode/content

# rebuild index.html from the placeholder source (this is what the agent ran)
cd task1_opencode
uv run python embed_images.py site/index.src.html --images content/images --out site/index.html
cd ..
```

OpenCode setup (install, `opencode.json`, gateway key): `task1_opencode/README.md`.

### Task 2 – Our own coding agent

Details: `task2_own_agent/README.md`.

```bash
# run the agent (asks for human feedback when it finishes; writes logs/run_XX.jsonl)
uv run python task2_own_agent/agent.py --content task1_opencode/content --out task2_own_agent/site/index.html

uv run python task2_own_agent/rag.py          # retrieval sanity check: 12 queries with known answers
uv run pytest task2_own_agent/tests -q        # offline tests with a fake model (no gateway needed)
uv run python task2_own_agent/check_site.py task2_own_agent/site/index.html --full --content task1_opencode/content
```

### Task 3 – RAG under the microscope

Results and discussion: `task3_rag/REPORT.md`. All numbers come from files in `task3_rag/results/`.
The embedding cache (`task3_rag/cache/`) is not submitted; the first run rebuilds it (about 2–3 minutes).

```bash
# 1. chunking, chunk-size and alpha sweeps, R1–R5 on dev and test, metrics, retrieval cost
uv run python task3_rag/run_retrieval.py

# 2. generation on the 50-question judge sample (whole-document context, and chunk-only context for comparison)
uv run python task3_rag/generate.py r4 --split dev --sample 50
uv run python task3_rag/generate.py r3 --split dev --sample 50
uv run python task3_rag/generate.py r4 --split dev --sample 50 --context chunks
uv run python task3_rag/generate.py r3 --split dev --sample 50 --context chunks

# 3. LLM judge (faithfulness, answer relevance, correctness)
uv run python task3_rag/judge.py task3_rag/results/answers_r4_dev_sample50.jsonl --sample 50 --out task3_rag/results/judge_r4.jsonl
uv run python task3_rag/judge.py task3_rag/results/answers_r3_dev_sample50.jsonl --sample 50 --out task3_rag/results/judge_r3.jsonl
uv run python task3_rag/judge.py task3_rag/results/answers_r4_dev_sample50_chunkctx.jsonl --sample 50 --out task3_rag/results/judge_r4_chunkctx.jsonl
uv run python task3_rag/judge.py task3_rag/results/answers_r3_dev_sample50_chunkctx.jsonl --sample 50 --out task3_rag/results/judge_r3_chunkctx.jsonl

# 4. test predictions with the recommended config (writes task3_rag/predictions_test.jsonl)
uv run python task3_rag/generate.py r4 --split test --predictions

# 5. summary tables (no LLM calls)
uv run python task3_rag/make_tables.py

# helpers
uv run python task3_rag/evaluate.py task3_rag/results/retrieval_r4_dev.jsonl --k 5   # metrics for one run
uv run python task3_rag/show_case.py dev062 --config r4                              # one question side by side
```

### Task 4 – Tools for agents

Details, threat model and the adversarial content we found: `task4_tools/README.md`.

```bash
uv run pytest task4_tools/tests -v                  # public contract tests + our adversarial tests
uv run python task4_tools/reset_workspace.py
uv run python task4_tools/agent.py --mock           # scripted fake model -> logs/mock_trace.jsonl
uv run python task4_tools/reset_workspace.py
uv run python task4_tools/agent.py                  # real run -> logs/trace.jsonl
git -C task4_tools/workspace diff > task4_tools/final.diff
```

`task4_tools/workspace/` is generated by `reset_workspace.py` and is not part of the submission.
