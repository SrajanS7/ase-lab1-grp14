"""Task 2 – a small coding agent that builds the team website.

Protocol (our own, no native tool calling): every model reply is ONE JSON object
    {"thought": "...", "action": "<name>", "args": {...}}
For write_site the HTML follows the JSON in a ```html code block (args.html is accepted too).

Loop:  model reply -> parse + validate -> run action -> observation (as a user message) -> repeat
until an accepted `finish` or the step limit. After an accepted finish a human can give feedback
on the terminal; non-empty feedback starts a new round.

Run from the repository root:
    uv run python task2_own_agent/agent.py --content task1_opencode/content --out task2_own_agent/site/index.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for p in (str(REPO_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common.embed_images import embed_images  # noqa: E402
from common.llm_client import LLMClient, count_tokens  # noqa: E402
from common.site_checker import check_site, member_names_from_content  # noqa: E402
from rag import StyleGuideIndex, format_observation  # noqa: E402

DEFAULT_MAX_STEPS = 25
DEFAULT_K = 5
DEFAULT_MAX_TOKENS = 32_000  # a full page + hidden reasoning tokens; too low -> finish_reason "length"
REPEAT_LIMIT = 3            # identical action+args this many times in a row -> warning

# ------------------------------------------------------------------ prompts
# Neither prompt contains style guide text: the agent has to retrieve every rule.

SYSTEM_PROMPT = """You are a web developer agent. You build a single-file team website by calling actions.

## Protocol
Answer with EXACTLY ONE JSON object per message and nothing else:
{"thought": "<one or two sentences>", "action": "<action name>", "args": {...}}

Exception: for write_site, put the complete HTML document in a ```html code block DIRECTLY AFTER the JSON object.

## Actions
- search_style_guide  args: {"query": "<what you want to know>"}
    Semantic search in the team's web style guide. Returns the most relevant passages with document ids.
    One topic per query works best.
- read_profiles  args: {}
    Returns every team member's profile text and the list of available image files.
- write_site  args: {}   (+ the ```html block after the JSON)
    Saves the page and runs the automatic checker. Returns the checker report.
- finish  args: {"summary": "<what you built>"}
    Ends the task. Only accepted if the LAST write_site report passed all checks.

## Rules
- The style guide is binding and you do not know it in advance. Search it before writing the page
  and look up every rule a failing check refers to. Never guess colors, texts, ids or other values.
- Each document has a Status. Follow ACTIVE documents. DEPRECATED documents are outdated:
  never use their values.
- Images: never write image data yourself. Use placeholders exactly like
  <img src="{{image:<filename>}}" alt="...">  with a filename from read_profiles. Your code replaces them.
- The page must be one self-contained HTML file (inline CSS and JS, nothing loaded from the internet).
- Each write_site must contain the COMPLETE page, not a diff.
"""

TASK_PROMPT = """Build the website of our lab group (team name: {team}).

Requirements:
- one standalone HTML file: HTML + CSS + JavaScript only, nothing loaded from the internet
- introduces the team and every member, with everyone's picture
- responsive (phone and desktop) with a day/night mode toggle
- a section with the team's shared view on AI and the future of software engineering
  (synthesise it from the members' opinions)
- funny and creative
- follows the style guide in every point

Start by reading the profiles and searching the style guide. When the checker passes, call finish."""


# ----------------------------------------------------------------- protocol

ACTIONS: dict[str, dict[str, type]] = {
    # action -> {arg name: type}; write_site may have "html" optionally (see validate_args)
    "search_style_guide": {"query": str},
    "read_profiles": {},
    "write_site": {},
    "finish": {"summary": str},
}
OPTIONAL_ARGS = {"write_site": {"html": str}}

FORMAT_REMINDER = (
    'Reply with exactly one JSON object: {"thought": "...", "action": "<one of '
    + ", ".join(ACTIONS)
    + '>", "args": {...}}'
)


class ProtocolError(Exception):
    """Invalid model output. The message is sent back to the model as the observation."""


@dataclass
class Action:
    name: str
    args: dict
    thought: str = ""
    html: str | None = None  # write_site only


def _first_json_object(text: str) -> dict:
    """Find the first JSON object in `text`. Tolerates ```json fences and text around the object."""
    decoder = json.JSONDecoder()
    # Only look before the html block, so we never pick up something from inside the page.
    head = text.split("```html", 1)[0]
    for m in re.finditer(r"\{", head):
        try:
            obj, _ = decoder.raw_decode(head, m.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ProtocolError("Your reply contains no valid JSON object. " + FORMAT_REMINDER)


def _html_block(text: str) -> str | None:
    """Content of the ```html block. Uses the LAST closing fence so ``` inside the page does not cut it."""
    start = text.find("```html")
    if start < 0:
        return None
    body = text[start + len("```html"):]
    end = body.rfind("```")
    if end < 0:
        raise ProtocolError(
            "The ```html block is not closed. The page was probably cut off. Write a shorter page "
            "(less repeated CSS, shorter texts) and close the block with ```."
        )
    return body[:end].strip()


def parse_action(text: str) -> Action:
    """Model text -> validated Action. Raises ProtocolError with a message for the model."""
    if not text or not text.strip():
        raise ProtocolError("Your reply was empty. " + FORMAT_REMINDER)
    obj = _first_json_object(text)

    name = obj.get("action")
    if not isinstance(name, str) or not name:
        raise ProtocolError('The JSON object has no "action" field. ' + FORMAT_REMINDER)
    if name not in ACTIONS:
        raise ProtocolError(f"Unknown action {name!r}. Valid actions: {', '.join(ACTIONS)}.")

    args = obj.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError(f'"args" must be a JSON object, got {type(args).__name__}.')
    validate_args(name, args)

    action = Action(name, args, thought=str(obj.get("thought", "")))
    if name == "write_site":
        html = _html_block(text) or args.get("html")
        if not html or not html.strip():
            raise ProtocolError(
                "write_site needs the complete HTML page in a ```html code block directly after the JSON object."
            )
        action.html = html
    return action


def validate_args(name: str, args: dict) -> None:
    required = ACTIONS[name]
    allowed = {**required, **OPTIONAL_ARGS.get(name, {})}
    missing = [k for k in required if k not in args]
    unknown = [k for k in args if k not in allowed]
    wrong = [k for k, t in allowed.items() if k in args and not isinstance(args[k], t)]
    empty = [k for k in required if isinstance(args.get(k), str) and not args[k].strip()]
    problems = []
    if missing:
        problems.append(f"missing {missing}")
    if unknown:
        problems.append(f"unknown {unknown}")
    if wrong:
        problems.append(f"wrong type for {wrong}")
    if empty:
        problems.append(f"empty {empty}")
    if problems:
        expected = json.dumps({k: t.__name__ for k, t in required.items()})
        raise ProtocolError(f"Invalid args for {name}: {'; '.join(problems)}. Expected args: {expected}.")


# -------------------------------------------------------------------- agent


@dataclass
class AgentState:
    step: int = 0                       # steps in the current round
    total_steps: int = 0
    last_report_passed: bool = False    # result of the most recent write_site
    writes: int = 0
    feedback_pending: bool = False      # human feedback not yet answered with a new write_site
    recent: list[str] = field(default_factory=list)  # signatures of recent actions (repeat detection)


class SiteAgent:
    def __init__(
        self,
        llm: LLMClient,
        index: StyleGuideIndex,
        content_dir: Path,
        out_path: Path,
        team: str = "Group 14",
        k: int = DEFAULT_K,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        feedback_fn: Callable[[], str] | None = None,
        echo: Callable[[str], None] = print,
    ) -> None:
        self.llm = llm
        self.index = index
        self.content_dir = Path(content_dir)
        self.images_dir = self.content_dir / "images"
        self.out_path = Path(out_path)
        self.k = k
        self.max_steps = max_steps
        self.max_tokens = max_tokens
        self.feedback_fn = feedback_fn
        self.echo = echo
        self.member_names = member_names_from_content(self.content_dir)
        self.state = AgentState()
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": TASK_PROMPT.format(team=team)},
        ]

    # ---------------------------------------------------------------- loop

    def run(self) -> bool:
        """Returns True if the run ended with an accepted finish."""
        self.llm.log_event({"event": "run_start", "member_names": self.member_names, "k": self.k,
                            "max_steps": self.max_steps, "out": str(self.out_path)})
        accepted = False
        while True:
            accepted = self._run_round()
            if not accepted:
                break
            feedback = self._ask_feedback()
            if not feedback:
                break
            self.llm.log_event({"event": "human_feedback", "text": feedback})
            self.messages.append({"role": "user", "content":
                                  f"HUMAN FEEDBACK on the finished site:\n{feedback}\n\n"
                                  "Apply it, write the complete page again with write_site, then finish."})
            # The page has not been changed for the feedback yet -> finish needs a new passing write first.
            self.state.feedback_pending = True
            self.state.step = 0
        self.llm.log_event({"event": "run_end", "accepted": accepted, "total_steps": self.state.total_steps,
                            "usage": self.llm.usage_summary()})
        self.echo(f"\nUsage: {self.llm.usage_summary()}")
        return accepted

    def _run_round(self) -> bool:
        while self.state.step < self.max_steps:
            self.state.step += 1
            self.state.total_steps += 1
            n = self.state.total_steps
            reply, finish_reason = self._call_model()

            try:
                if finish_reason == "length":
                    raise ProtocolError(
                        "Your reply was cut off (output token limit). Keep the thought short and the page compact."
                    )
                action = parse_action(reply)
            except ProtocolError as err:
                observation = f"ERROR: {err}"
                self.echo(f"[step {n}] invalid output -> {err}")
                self._observe(n, "invalid", observation, error=True)
                continue

            self.echo(f"[step {n}] {action.name} {self._short_args(action)}  | {action.thought[:100]}")
            observation, done = self._execute(action)
            observation += self._repeat_warning(action)
            self._observe(n, action.name, observation, error=observation.startswith("ERROR"))
            if done:
                self.echo(f"[step {n}] finish accepted: {action.args.get('summary', '')}")
                return True

        msg = (f"Step limit ({self.max_steps}) reached without an accepted finish. "
               f"Last page: {self.out_path} (checks passed: {self.state.last_report_passed}).")
        self.echo(msg)
        self.llm.log_event({"event": "step_limit", "message": msg})
        return False

    def _call_model(self) -> tuple[str, str | None]:
        # _chat_request (instead of chat) gives us finish_reason; it logs request + response like chat().
        response = self.llm._chat_request(self.messages, None, self.max_tokens)
        choice = response.choices[0]
        reply = choice.message.content or ""
        self.messages.append({"role": "assistant", "content": reply})
        return reply, getattr(choice, "finish_reason", None)

    def _observe(self, step: int, action: str, text: str, error: bool = False) -> None:
        content = f"OBSERVATION (step {step}, {action}):\n{text}"
        self.messages.append({"role": "user", "content": content})
        self.llm.log_event({"event": "observation", "step": step, "action": action, "error": error,
                            "tokens": count_tokens(content), "content": content})

    # ------------------------------------------------------------- actions

    def _execute(self, action: Action) -> tuple[str, bool]:
        """Run an action. Returns (observation, finished)."""
        try:
            if action.name == "search_style_guide":
                return self._search(action.args["query"]), False
            if action.name == "read_profiles":
                return self._read_profiles(), False
            if action.name == "write_site":
                return self._write_site(action.html or ""), False
            if action.name == "finish":
                return self._finish()
        except Exception as exc:  # an action must never crash the loop
            return f"ERROR while running {action.name}: {type(exc).__name__}: {exc}", False
        return f"ERROR: unknown action {action.name}", False  # unreachable (validated before)

    def _search(self, query: str) -> str:
        chunks = self.index.search(query, self.k)
        text, used = format_observation(chunks)
        self.llm.log_event({"event": "retrieval", "query": query, "style_guide_tokens": used,
                            "results": [{"doc_id": c.doc_id, "score": round(c.score, 4), "note": c.note}
                                        for c in chunks]})
        return text

    def _read_profiles(self) -> str:
        parts = []
        for path in sorted(self.content_dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            parts.append(f"=== {path.name} ===\n{path.read_text(encoding='utf-8').strip()}")
        images = sorted(p.name for p in self.images_dir.iterdir() if p.is_file() and not p.name.startswith("."))
        parts.append("Available image files (use as {{image:<filename>}}): " + ", ".join(images))
        parts.append("Members (each must appear on the page): " + ", ".join(self.member_names))
        return "\n\n".join(parts)

    def _write_site(self, html: str) -> str:
        if re.search(r"data:image/[^;]+;base64,[A-Za-z0-9+/]{20}", html):
            return ("ERROR: the page contains base64 image data. Never write image data yourself; "
                    "use <img src=\"{{image:<filename>}}\">. Nothing was saved.")
        try:
            final = embed_images(html, self.images_dir)
        except (FileNotFoundError, ValueError) as exc:
            return f"ERROR: {exc}. Nothing was saved."

        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text(final, encoding="utf-8")
        report = check_site(final, mode="full", member_names=self.member_names,
                            file_size=self.out_path.stat().st_size)
        self.state.writes += 1
        self.state.last_report_passed = report.passed
        self.state.feedback_pending = False
        self._compact_old_pages()
        self.llm.log_event({"event": "checker", "version": self.state.writes, "report": report.to_dict()})
        hint = "" if report.passed else "\nFix the failing checks: search the style guide for each one."
        return f"Saved version {self.state.writes} ({len(final) // 1024} KB).\n{report.summary()}{hint}"

    def _finish(self) -> tuple[str, bool]:
        if self.state.writes == 0:
            return "finish REJECTED: you have not written the site yet (write_site).", False
        if self.state.feedback_pending:
            return ("finish REJECTED: apply the human feedback and write the updated page with write_site "
                    "before finishing."), False
        if not self.state.last_report_passed:
            report = check_site(self.out_path.read_text(encoding="utf-8"), mode="full",
                                member_names=self.member_names)
            return "finish REJECTED: the last checker report did not pass.\n" + report.summary(only_failures=True), False
        return "finish accepted.", True

    # --------------------------------------------------------- context size

    def _compact_old_pages(self) -> None:
        """Keep only the newest page in full in the history.

        Every request re-sends the whole history. Without this, each earlier version of the page
        (~5-10k tokens) would be sent again on every later step. The full HTML stays in the log.
        """
        seen_latest = False
        for m in reversed(self.messages):
            if m["role"] != "assistant" or "```html" not in m["content"]:
                continue
            if not seen_latest:
                seen_latest = True
                continue
            if "[older page omitted" in m["content"]:
                break  # everything before this was compacted already
            head = m["content"].split("```html", 1)[0]
            m["content"] = head + "```html\n[older page omitted to save context; see the newest write_site]\n```"

    def _repeat_warning(self, action: Action) -> str:
        sig = action.name + json.dumps(action.args, sort_keys=True)
        self.state.recent = (self.state.recent + [sig])[-REPEAT_LIMIT:]
        if len(self.state.recent) == REPEAT_LIMIT and len(set(self.state.recent)) == 1:
            return (f"\n\nWARNING: you sent the same action {REPEAT_LIMIT} times in a row. "
                    "It will give the same result. Do something different.")
        return ""

    # -------------------------------------------------------------- helpers

    def _ask_feedback(self) -> str:
        if self.feedback_fn is None:
            return ""
        try:
            return self.feedback_fn().strip()
        except EOFError:
            return ""

    @staticmethod
    def _short_args(action: Action) -> str:
        if action.name == "write_site":
            return f"({len(action.html or '')} chars of HTML)"
        return json.dumps(action.args, ensure_ascii=False)[:80]


# ---------------------------------------------------------------------- CLI


def next_log_path(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    nums = [int(m.group(1)) for p in log_dir.glob("run_*.jsonl") if (m := re.fullmatch(r"run_(\d+)\.jsonl", p.name))]
    return log_dir / f"run_{max(nums, default=0) + 1:02d}.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--content", type=Path, default=REPO_ROOT / "task1_opencode" / "content",
                    help="folder with <name>.md profiles and images/")
    ap.add_argument("--out", type=Path, default=HERE / "site" / "index.html", help="where to write the site")
    ap.add_argument("--team", default="Group 14", help="team name used in the task prompt")
    ap.add_argument("--model", default=None, help="chat model (default: $LAB_CHAT_MODEL)")
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS, help="step limit per round")
    ap.add_argument("--k", type=int, default=DEFAULT_K, help="chunks per style guide search")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, help="max output tokens per call")
    ap.add_argument("--log-dir", type=Path, default=HERE / "logs")
    ap.add_argument("--no-feedback", action="store_true", help="don't ask for human feedback after finish")
    args = ap.parse_args(argv)

    log_path = next_log_path(args.log_dir)
    llm = LLMClient(model=args.model, log_path=log_path)
    print(f"Log: {log_path}\nModel: {llm.model}\nIndexing the style guide ...")
    index = StyleGuideIndex.from_llm(llm)
    print(f"{len(index.docs)} documents, {len(index.chunks)} chunks.\n")

    feedback_fn = None if args.no_feedback else (
        lambda: input("\nFeedback for the agent (empty = done): ")
    )
    agent = SiteAgent(llm, index, args.content, args.out, team=args.team, k=args.k,
                      max_steps=args.max_steps, max_tokens=args.max_tokens, feedback_fn=feedback_fn)
    ok = agent.run()
    print(f"\n{'Done' if ok else 'Stopped'}. Site: {args.out}  Log: {log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
