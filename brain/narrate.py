"""A short narration of an incident, written by a language model from the structured evidence (README 3.7).

The core loop never needs it: the investigator's template report is always written, and narration is an
extra field, `narrative`, that arrives on a later tick. The rules:

- Only the evidence goes out: the incident's numbers, place, hypotheses with their evidence, what is
  uncertain and the template report (`evidence`). No database rows, no ground truth, nothing else.
- One request per milestone of an incident, not per tick: a new incident, a change of the leading
  explanation or of its status, a cost that has doubled, the incident resolved. Numbers that grow a
  little while the story stays the same do not trigger a new request. At most one request per incident is in flight; a newer milestone waits.
- It runs in background threads and is bounded: 20 s per request, no retries inside the SDK.
- What comes back is checked before it is used: a plain text that finished normally, and every number in
  it must appear in the evidence (as given, rounded to the precision written, or as a percentage of a
  share). A text with a number the evidence does not hold is dropped, not repaired (`numbers_backed`).
- Failures say why and cost nothing more: a refused key or no credit turns narration off for the run; a
  timeout, a rate limit or a server error waits COOLDOWN_S before the milestone is tried again.
- The key comes from the environment or the repository's .env; it never appears in a reason or a status.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

MODEL = "claude-opus-5-5"
EFFORT = "low"                  # a few sentences from given facts: low effort keeps it well inside the timeout
TIMEOUT_S = 20.0
MAX_TOKENS = 4000               # thinking counts too; the answer itself is three or four sentences
COOLDOWN_S = 60.0
MAX_CHARS = 900
FALLBACK_BETA = "server-side-fallback-2026-07-01"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

SYSTEM = (
    "You write the short summary of an incident that an automatic monitor found in a marketplace's business "
    "metrics. The reader runs the marketplace and has a minute. You get the incident as JSON from the "
    "monitor's investigation.\n"
    "Write three or four plain sentences in English: what changed, where and since when; what it has cost so "
    "far; the most likely cause and how sure the investigation is (a cause counts as found only when its "
    "status is \"supported\"); and the main thing that is still uncertain. If no cause is supported, say that "
    "no cause is confirmed yet. If the status is resolved, say that it is back to normal.\n"
    "Use only facts and numbers that are in the JSON. Write numbers as they appear there; you may round them "
    "to fewer decimals or write a share as a percentage, but never round to a different round number and "
    "never compute new numbers. Write dates and times as they appear (for example 2026-11-26 05:00 UTC). "
    "No headings, lists, markdown or advice."
)

NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def evidence(body: dict) -> dict:
    """What the model may use: the incident without its long trail."""
    keep = ("id", "status", "first_seen", "last_seen", "resolved_at", "metric", "segment", "change", "impact",
            "explained_share", "uncertain", "report")
    out = {k: body[k] for k in keep if k in body}
    out["drivers"] = [{k: d.get(k) for k in ("metric", "segment", "observed", "expected", "delta_pct", "role", "onset")
                       if k in d} for d in body.get("drivers", [])]
    out["localization"] = [{k: x.get(k) for k in ("dimension", "segment", "share_of_delta", "chosen") if k in x}
                           for x in body.get("localization", [])[:3]]
    out["hypotheses"] = [{k: h.get(k) for k in ("cause", "kind", "status", "evidence") if k in h}
                         for h in body.get("hypotheses", [])]
    return out


def _numbers(text: str) -> list[tuple[float, int]]:
    """Every number in a text as (absolute value, decimals written)."""
    out = []
    for token in NUMBER.findall(text):
        plain = token.replace(",", "")
        out.append((abs(float(plain)), len(plain.split(".")[1]) if "." in plain else 0))
    return out


def numbers_backed(text: str, facts: dict) -> tuple[bool, str]:
    """Is every number in the text one of the evidence's, as written, rounded, or as a percentage?"""
    known = [v for v, _ in _numbers(json.dumps(facts, ensure_ascii=False))]
    for value, decimals in _numbers(text):
        tol = 0.5 * 10 ** -decimals + 1e-9
        if not any(abs(value - k) <= tol or abs(value - 100 * k) <= tol or abs(value / 100 - k) <= tol / 100
                   for k in known):
            shown = f"{value:.{decimals}f}"
            return False, f"the number {shown} is not in the evidence"
    return True, "every number appears in the evidence"


def cost_scale(body: dict) -> int | None:
    """The power of two of the incident's cost in euros: a new value means the cost has doubled (or halved)."""
    for key, value in (body.get("impact") or {}).items():
        if key.endswith("_eur") and isinstance(value, (int, float)) and math.isfinite(value) and abs(value) >= 1:
            return int(math.log2(abs(value)))
    return None


def milestone(body: dict) -> tuple:
    """What a narration is about: the incident's place, status and leading explanation, and the size of its cost
    to within a factor of two. Numbers that grow a little each tick do not make a new milestone; a cost that has
    doubled does, so the story keeps up with the figures shown above it at a few requests per incident."""
    lead = next(iter(body.get("hypotheses") or []), {})
    state = "resolved" if body.get("status") == "resolved" else "active"
    return (state, body.get("metric"), tuple(sorted((body.get("segment") or {}).items())),
            lead.get("cause"), lead.get("status"), cost_scale(body))


def _api_message(e) -> str:
    """The API's own sentence when the error body has one, else the SDK's message."""
    body = getattr(e, "body", None)
    if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("message"):
        return str(body["error"]["message"])
    return str(getattr(e, "message", "") or type(e).__name__)


def _clean(message: str) -> str:
    message = KEY.sub("[key]", str(message))
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        secret = os.environ.get(name)
        if secret:
            message = message.replace(secret, "[key]")
    return " ".join(message.split())[:300]


class Narrator:
    """Narrations in the background. `submit` after writing an incident, `ready` on the next tick."""

    def __init__(self, client=None, model: str = MODEL, env_file: str | Path | None = ENV_FILE, workers: int = 2):
        self.model = model
        self.status = "on"
        self._client = client
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="narrate")
        self._running: dict[str, tuple[tuple, dict, Future]] = {}   # incident -> (milestone, body, job)
        self._waiting: dict[str, tuple[tuple, dict]] = {}            # incident -> newest milestone not yet sent
        self._done: dict[str, tuple] = {}                            # incident -> milestone answered for good
        self._until = 0.0                                            # no new request before this (cooldown)
        if self._client is None:
            self._client = self._connect(env_file)

    def _connect(self, env_file):
        if env_file is not None and Path(env_file).is_file():
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file)
            except ImportError:
                pass
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            self.status = "off: no API key (ANTHROPIC_API_KEY) in the environment or .env"
            return None
        try:
            import anthropic
        except ImportError:
            self.status = "off: the anthropic package is not installed"
            return None
        try:
            return anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
        except Exception as e:    # the SDK reads credential files and the home folder: none of it may stop the monitor
            self.status = "off: the API client could not be set up: " + _clean(str(e))
            return None

    # ------------------------------------------------------------------ the main thread's side
    def submit(self, key: str, body: dict) -> None:
        """Ask for a narration of this state of the incident, unless it is already told or being told."""
        if self._client is None or self.status != "on":
            return
        stage = milestone(body)
        if self._done.get(key) == stage:
            return
        if key in self._running:
            if self._running[key][0] != stage:
                self._waiting[key] = (stage, body)
            return
        if time.monotonic() < self._until:
            return
        self._start(key, stage, body)

    def ready(self) -> list[tuple[str, dict]]:
        """The narrations finished since the last call, as (incident id, narrative field)."""
        out = []
        for key, (stage, body, job) in list(self._running.items()):
            if not job.done():
                continue
            del self._running[key]
            story, verdict = job.result()
            if verdict == "final":
                self._done[key] = stage
            elif verdict == "off":
                self.status = "off: " + story["reason"]
            elif verdict == "later":
                self._until = time.monotonic() + COOLDOWN_S
            out.append((key, story))
            waiting = self._waiting.pop(key, None)
            if waiting is not None and self.status == "on" and time.monotonic() >= self._until \
                    and self._done.get(key) != waiting[0]:
                self._start(key, *waiting)
        return out

    def busy(self) -> bool:
        """A request was sent and its narration has not been collected by `ready` yet."""
        return bool(self._running)

    def settled(self) -> bool:
        """Every request sent has its answer (collected or not): the next `ready` returns them all."""
        return all(job.done() for _, _, job in self._running.values())

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _start(self, key: str, stage: tuple, body: dict) -> None:
        self._running[key] = (stage, body, self._pool.submit(self._tell, body))

    # ------------------------------------------------------------------ the background side
    def _story(self, body: dict, text: str | None, source: str, reason: str, served: str | None = None) -> dict:
        return {"text": text, "source": source, "model": served or self.model, "as_of": body.get("last_seen"),
                "status": body.get("status"), "checked": reason if source == "model" else None,
                "reason": None if source == "model" else reason}

    def _tell(self, body: dict) -> tuple[dict, str]:
        """One request. Returns the narrative and what to do next: final, later (cooldown) or off."""
        import anthropic
        facts = evidence(body)
        try:
            r = self._client.beta.messages.create(
                model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM,
                output_config={"effort": EFFORT},
                betas=[FALLBACK_BETA], fallbacks="default",
                messages=[{"role": "user", "content": "Incident evidence (JSON):\n" + json.dumps(facts, ensure_ascii=False)}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError,
                anthropic.BadRequestError) as e:
            return self._story(body, None, "none", "the API refused the request: " + _clean(_api_message(e))), "off"
        except anthropic.APITimeoutError:
            return self._story(body, None, "none", f"the request timed out after {TIMEOUT_S:.0f} s"), "later"
        except anthropic.RateLimitError:
            return self._story(body, None, "none", "rate limited by the API"), "later"
        except anthropic.APIStatusError as e:
            return self._story(body, None, "none", f"the API answered {e.status_code}"), "later"
        except anthropic.APIConnectionError:
            return self._story(body, None, "none", "the API could not be reached"), "later"
        except Exception as e:       # whatever else happens here must not reach the monitor
            return self._story(body, None, "none", "narration failed: " + _clean(type(e).__name__)), "later"

        if r.stop_reason == "refusal":
            return self._story(body, None, "none", "the model declined to write it"), "final"
        if r.stop_reason == "max_tokens":
            return self._story(body, None, "none", "the text was cut off"), "final"
        text = " ".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()
        if not text or len(text) > MAX_CHARS or any(m in text for m in ("**", "#", "```", "\n\n", "\n- ")):
            return self._story(body, None, "none", "the answer was not a short plain text"), "final"
        ok, why = numbers_backed(text, facts)
        if not ok:
            return self._story(body, None, "none", "dropped: " + why), "final"
        return self._story(body, text, "model", why, getattr(r, "model", None)), "final"


def describe(narrator: Narrator | None) -> str:
    return "narration: off" if narrator is None else f"narration: {narrator.status} ({narrator.model})"


__all__ = ["Narrator", "evidence", "numbers_backed", "milestone", "describe", "MODEL", "COOLDOWN_S", "TIMEOUT_S"]
