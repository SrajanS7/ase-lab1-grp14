"""Offline tests for the Task 2 agent: a fake model client replays scripted replies.

Run from the repository root:  uv run pytest task2_own_agent/tests -q
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from agent import ProtocolError, SiteAgent, parse_action  # noqa: E402
from common.llm_client import LLMClient  # noqa: E402
from rag import StyleGuideIndex, format_observation  # noqa: E402

CONTENT = HERE.parent.parent / "task1_opencode" / "content"
MEMBERS = sorted(p.stem for p in CONTENT.glob("*.md") if not p.name.startswith("_"))


# ------------------------------------------------------------------ fakes


def fake_embed(texts: list[str]) -> np.ndarray:
    """Hashed bag of words: deterministic, good enough to test the plumbing."""
    out = np.zeros((len(texts), 256), dtype=np.float32)
    for i, t in enumerate(texts):
        t = t.split("Query: ", 1)[-1]  # ignore the query instruction prefix
        for w in t.lower().split():
            out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 256] += 1
    return out


class FakeCompletions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def create(self, **params):
        self.requests.append(json.loads(json.dumps(params["messages"])))
        item = self.replies.pop(0) if self.replies else '{"action": "finish", "args": {"summary": "x"}}'
        text, reason = item if isinstance(item, tuple) else (item, "stop")
        msg = SimpleNamespace(role="assistant", content=text, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason=reason)], usage=None)


def make_agent(tmp_path, replies, feedback=(), max_steps=25):
    completions = FakeCompletions(replies)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    llm = LLMClient(model="fake", log_path=tmp_path / "run.jsonl", openai_client=client)
    index = StyleGuideIndex(fake_embed, use_cache=False)
    fb = list(feedback)
    agent = SiteAgent(llm, index, CONTENT, tmp_path / "site" / "index.html", max_steps=max_steps,
                      feedback_fn=(lambda: fb.pop(0) if fb else ""), echo=lambda s: None)
    return agent, completions


def act(action, **args):
    return json.dumps({"thought": "t", "action": action, "args": args})


def write(html):
    return act("write_site") + "\n```html\n" + html + "\n```"


def passing_page(extra=""):
    cards = "\n".join(
        f'<article class="member-card"><img src="{{{{image:{m}.jpg}}}}" alt="Photo of {m.capitalize()}">'
        f'<h3>{m.capitalize()}</h3><p class="hot-take">hot</p></article>'
        for m in MEMBERS
    )
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Team Group 14 | Agentic SE Lab</title>
<style>
:root {{ --bg: #FFF8E7; --text: #1D1D1F; --accent: #E4007C; }}
[data-theme="dark"] {{ --bg: #14142B; --text: #F4F1DE; }}
body {{ font-family: system-ui, sans-serif; font-size: 1rem; background: var(--bg); color: var(--text); }}
@media (max-width: 700px) {{ body {{ padding: 1rem; }} }}
</style></head><body>
<header><h1>Team Group 14</h1><button id="theme-toggle" aria-label="Toggle night mode">Theme</button></header>
<main><section id="team">{cards}</section><section id="future-of-se"><h2>Future</h2>{extra}</section></main>
<footer>Built by an agent, supervised by humans.</footer>
<script>
const t = localStorage.getItem('upb-theme') || 'light';
document.documentElement.setAttribute('data-theme', t);
document.getElementById('theme-toggle').onclick = () => {{
  const n = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', n); localStorage.setItem('upb-theme', n);
}};
</script></body></html>"""


def observations(agent):
    return [m["content"] for m in agent.messages if m["role"] == "user" and m["content"].startswith("OBSERVATION")]


# ----------------------------------------------------------------- parser


def test_parse_tolerates_fences_and_prose():
    a = parse_action('Sure!\n```json\n{"thought": "x", "action": "read_profiles", "args": {}}\n```')
    assert a.name == "read_profiles"


def test_parse_write_site_with_code_block_containing_braces():
    a = parse_action(write("<style>a { color: red }</style>"))
    assert a.name == "write_site" and "color: red" in a.html


def test_parse_write_site_html_in_args():
    a = parse_action(json.dumps({"action": "write_site", "args": {"html": "<html></html>"}}))
    assert a.html == "<html></html>"


@pytest.mark.parametrize("text, fragment", [
    ("", "empty"),
    ("I will now search.", "no valid JSON"),
    ('{"thought": "x"}', 'no "action"'),
    (act("dance"), "Unknown action"),
    (act("search_style_guide"), "missing ['query']"),
    (act("search_style_guide", query=3), "wrong type"),
    (act("search_style_guide", query="x", k=3), "unknown ['k']"),
    (act("finish", summary="  "), "empty"),
    (act("write_site"), "needs the complete HTML"),
    (act("write_site") + "\n```html\n<html>", "not closed"),
    ('{"action": "read_profiles", "args": [1]}', "must be a JSON object"),
])
def test_parse_errors(text, fragment):
    with pytest.raises(ProtocolError, match=None) as exc:
        parse_action(text)
    assert fragment in str(exc.value)


# -------------------------------------------------------------------- rag


def test_observation_respects_budget():
    index = StyleGuideIndex(fake_embed, use_cache=False)
    from common.llm_client import count_tokens
    text, used = format_observation(index.search("night mode colors palette", k=20))
    assert used <= 1000
    style_text = text.split("\n\n(Not shown")[0]
    assert count_tokens(style_text) <= 1000


def test_deprecated_hit_brings_replacement():
    index = StyleGuideIndex(fake_embed, use_cache=False)
    sg12 = next(ch for ch in index.chunks if ch.doc_id == "SG-12")
    res = index.search(sg12.embed_text, k=1)
    ids = [c.doc_id for c in res]
    assert ids[0] == "SG-12" and "SG-13" in ids


# ------------------------------------------------------------------- loop


def test_full_run_with_errors_feedback_and_finish(tmp_path):
    replies = [
        "not json",                                                   # 1 invalid
        act("dance"),                                                 # 2 unknown action
        act("finish", summary="done"),                                # 3 rejected: nothing written
        act("read_profiles"),                                         # 4
        act("search_style_guide", query="footer text"),               # 5
        write('<img src="data:image/png;base64,' + "A" * 100 + '">'), # 6 base64 rejected
        write('<img src="{{image:nobody.jpg}}" alt="x">'),            # 7 unknown image
        write("<html><body>bad</body></html>"),                       # 8 saved, checks fail
        act("finish", summary="done"),                                # 9 rejected: checks failed
        write(passing_page()),                                        # 10 passes
        act("finish", summary="done"),                                # 11 accepted
        # human feedback round
        act("finish", summary="done again"),                          # 12 rejected: feedback pending
        write(passing_page("<p>more jokes</p>")),                     # 13
        act("finish", summary="added jokes"),                         # 14 accepted
    ]
    agent, _ = make_agent(tmp_path, replies, feedback=["add more jokes", ""])
    assert agent.run() is True
    obs = observations(agent)
    assert len(obs) == 14
    assert "no valid JSON" in obs[0]
    assert "Unknown action" in obs[1]
    assert "not written the site yet" in obs[2]
    assert "aryan.md" in obs[3] and "srajan.jpg" in obs[3]
    assert "[SG-" in obs[4]
    assert "base64" in obs[5]
    assert "nobody.jpg" in obs[6] and "Available" in obs[6]
    assert "FAIL" in obs[7]
    assert "REJECTED" in obs[8] and "FAIL" in obs[8]
    assert "All good!" in obs[9]
    assert "accepted" in obs[10]
    assert "human feedback" in obs[11]
    assert "accepted" in obs[13]
    html = (tmp_path / "site" / "index.html").read_text()
    assert "data:image/jpeg;base64," in html and "{{image:" not in html and "more jokes" in html
    events = [json.loads(l) for l in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "human_feedback" for e in events)
    assert sum(e.get("type") == "chat" for e in events) == 14


def test_step_limit(tmp_path):
    agent, _ = make_agent(tmp_path, ["garbage"] * 10, max_steps=3)
    assert agent.run() is False
    assert len(observations(agent)) == 3
    events = [json.loads(l) for l in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert any(e.get("event") == "step_limit" for e in events)


def test_truncated_output_is_reported(tmp_path):
    agent, _ = make_agent(tmp_path, [(act("read_profiles"), "length")], max_steps=1)
    agent.run()
    assert "cut off" in observations(agent)[0]


def test_repeat_warning(tmp_path):
    q = act("search_style_guide", query="footer")
    agent, _ = make_agent(tmp_path, [q, q, q], max_steps=3)
    agent.run()
    assert "WARNING" in observations(agent)[2] and "WARNING" not in observations(agent)[1]


def test_old_pages_are_compacted_in_history(tmp_path):
    replies = [write(passing_page("<p>v1</p>")), write(passing_page("<p>v2</p>")), act("read_profiles")]
    agent, completions = make_agent(tmp_path, replies, max_steps=3)
    agent.run()
    last_request = completions.requests[-1]
    assistant = [m["content"] for m in last_request if m["role"] == "assistant"]
    assert "older page omitted" in assistant[0] and "<p>v1</p>" not in assistant[0]
    assert "<p>v2</p>" in assistant[1]
