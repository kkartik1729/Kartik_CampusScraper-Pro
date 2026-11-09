"""A local model as a second opinion, used only where judgement is the problem.

The scorer refuses in two situations: nothing clears the threshold, and two
different domains sit within a whisker of each other. Both are judgement calls
rather than arithmetic, and judgement is what a model is actually good at. The
earlier failures came from asking a small model to count, quote and plan, which
it cannot do reliably; choosing between two URLs it can be shown is a different
task entirely.

The model is boxed in so it cannot repeat those failures:

  It never generates a URL. It picks an index from a list it was handed, and
  anything that is not a valid index is treated as "cannot tell".

  It only sees candidates the scorer already shortlisted, so a blocked
  aggregator never reaches it.

  It can always answer "none", and that answer is respected rather than
  retried until it agrees.

  Its pick is recorded with its reason and a lower confidence than a scored
  match, so a later audit can tell the two apart.

When no model is running, every call returns "cannot tell" and the behaviour is
exactly what it was before.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_ENDPOINT = "http://127.0.0.1:11434/api/generate"
DEFAULT_MODEL = "qwen3:4b"

PROMPT = """You are checking which website belongs to one specific organisation.

Organisation: {name}
{place}
Candidate websites:
{options}

Answer with the number of the website that is the organisation's own official
site. Answer 0 if none of them is, or if two of them could equally be it.

Rules:
- A parent group's site is not the answer when the organisation is one branch
  or college within it. Answer 0 in that case.
- A directory, ranking site, news article or social page is never the answer.
- Do not explain. Reply with a single number and nothing else.

Number:"""


THINK_BLOCK = re.compile(r"<think>.*?</think>", re.S | re.I)
UNCLOSED_THINK = re.compile(r"<think>.*", re.S | re.I)


def _strip_thinking(text: str) -> str:
    """Drop a reasoning block so its numbers are not mistaken for the answer.

    A closed block is removed. An unclosed one means the model was cut off
    mid-thought and never answered, so everything from the tag on is dropped
    and the caller sees an undecided verdict rather than a number lifted out
    of the model's working.
    """
    text = THINK_BLOCK.sub(" ", text)
    return UNCLOSED_THINK.sub(" ", text).strip()


@dataclass
class Verdict:
    index: int | None          # 1-based pick, or None for "cannot tell"
    raw: str = ""
    error: str = ""

    @property
    def decided(self) -> bool:
        return self.index is not None


def ask(
    name: str,
    urls: list[str],
    location: str | None = None,
    endpoint: str = DEFAULT_ENDPOINT,
    model: str = DEFAULT_MODEL,
    timeout: float = 45.0,
) -> Verdict:
    """Ask the local model to pick one candidate. Never raises."""
    if not urls:
        return Verdict(None, error="no candidates")
    shortlist = urls[:6]  # a longer list invites a sloppier answer

    body = json.dumps({
        "model": model,
        "prompt": PROMPT.format(
            name=name,
            place=f"Located in: {location}\n" if location else "",
            options="\n".join(f"{i}. {u}" for i, u in enumerate(shortlist, 1)),
        ),
        "stream": False,
        "think": False,
        # qwen3 and its relatives emit a reasoning block before the answer.
        # Eight tokens is spent entirely inside that block, so the reply comes
        # back with no answer in it at all. Allow room for the thinking, then
        # discard it below.
        "options": {"temperature": 0, "num_predict": 300},
    }).encode()

    request = urllib.request.Request(
        endpoint, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            answer = json.load(response).get("response", "")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as error:
        return Verdict(None, error=f"{type(error).__name__}")

    answer = _strip_thinking(answer)

    # Take the first number in what is left. A model that pads its answer with
    # a sentence still gets read correctly; one that answers nothing numeric is
    # treated as undecided rather than guessed at.
    match = re.search(r"\d+", answer.strip())
    if not match:
        return Verdict(None, raw=answer[:80], error="no number in reply")

    choice = int(match.group())
    if choice == 0 or choice > len(shortlist):
        return Verdict(None, raw=answer[:80])
    return Verdict(choice, raw=answer[:80])


def break_tie(
    name: str,
    scored: list,
    location: str | None = None,
    max_candidates: int = 4,
    **kwargs,
):
    """Choose among already-scored candidates. Returns the Candidate or None.

    `scored` is the scorer's own output, highest first, so anything blocked has
    already been dropped. Only the top few are offered: past the top handful the
    scores are too low for the question to be worth asking.
    """
    shortlist = [c for c in scored[:max_candidates]]
    if len(shortlist) < 2:
        return None, Verdict(None, error="nothing to choose between")

    verdict = ask(name, [c.url for c in shortlist], location, **kwargs)
    if not verdict.decided:
        return None, verdict
    # Range-check rather than trusting the index. ask() already maps 0 and
    # out-of-range to undecided, but a negative or zero index reaching this
    # line would silently wrap to the last candidate, which is exactly the
    # kind of quiet wrong answer this whole path exists to avoid.
    if not 1 <= verdict.index <= len(shortlist):
        return None, Verdict(None, raw=verdict.raw, error="index out of range")
    return shortlist[verdict.index - 1], verdict


def available(endpoint: str = DEFAULT_ENDPOINT, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """Is a model actually reachable? Used to report, not to decide."""
    tags = endpoint.replace("/api/generate", "/api/tags")
    try:
        with urllib.request.urlopen(tags, timeout=5) as response:
            names = [m.get("name", "") for m in json.load(response).get("models", [])]
    except Exception as error:
        return False, f"no model service at {tags} ({type(error).__name__})"
    if not any(n.split(":")[0] == model.split(":")[0] for n in names):
        return False, f"{model} not installed; found {', '.join(names) or 'nothing'}"
    return True, f"{model} available"


def self_test() -> None:
    """Parsing checks with canned replies. No model needed."""
    import types

    cases = [
        ("2", 2, "plain number"),
        ("2\n", 2, "trailing newline"),
        ("The answer is 2.", 2, "padded with words"),
        ("0", None, "model says none"),
        ("9", None, "out of range"),
        ("I am not sure", None, "no number"),
        ("", None, "empty reply"),
        ("<think>Maybe 1, maybe 3...</think>\n2", 2, "answer after thinking"),
        ("<think>I should check 1 and 2", None, "cut off mid-thought"),
        ("<think>option 3 looks right</think>0", None, "thinking then none"),
    ]
    urls = ["https://a.test/", "https://b.test/", "https://c.test/"]
    failures = 0
    for reply, expected, label in cases:
        verdict = _parse_for_test(reply, urls)
        ok = verdict.index == expected
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label:22} reply={reply!r:20} -> {verdict.index}")

    # An out-of-range index must not wrap to the last candidate.
    pair = [
        types.SimpleNamespace(url="https://a.test/", score=2.5),
        types.SimpleNamespace(url="https://b.test/", score=2.5),
    ]
    # Patch this module's own global. Importing by name would create a second
    # module object when the file runs as __main__, leaving break_tie bound to
    # the real ask() — a test that then passes only on a machine with no model
    # running, which is exactly how this guard went unexercised.
    here = globals()
    saved = here["ask"]
    for bad in (0, -1, 99):
        here["ask"] = lambda *a, _b=bad, **k: Verdict(_b)
        chosen, _verdict = break_tie("X", pair)
        ok = chosen is None
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} index {bad} rejected")
    here["ask"] = saved

    # The model must not be called at all when only one candidate survives.
    calls = []
    here["ask"] = lambda *a, **k: (calls.append(1), Verdict(1))[1]
    break_tie("X", pair[:1])
    ok = not calls
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} model not called for a single candidate")
    here["ask"] = saved

    # A single candidate is never sent to the model.
    fake = [types.SimpleNamespace(url="https://only.test/", score=1.0)]
    pick, verdict = break_tie("X", fake)
    ok = pick is None and not verdict.decided
    failures += 0 if ok else 1
    print(f"  {'ok  ' if ok else 'FAIL'} single candidate skipped")

    print(f"\n{len(cases) + 5 - failures}/{len(cases) + 5} passed")
    reachable, note = available()
    print(f"model check: {note}")


def _parse_for_test(answer: str, urls: list[str]) -> Verdict:
    answer = _strip_thinking(answer)
    match = re.search(r"\d+", answer.strip())
    if not match:
        return Verdict(None, raw=answer[:80], error="no number in reply")
    choice = int(match.group())
    if choice == 0 or choice > len(urls):
        return Verdict(None, raw=answer[:80])
    return Verdict(choice, raw=answer[:80])


if __name__ == "__main__":
    self_test()
