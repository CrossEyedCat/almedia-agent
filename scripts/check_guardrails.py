"""Automatic part of docs/GUARDRAILS.md. Standard library only. Exit code 0 = pass."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET = re.compile(r"(sk-ant-[A-Za-z0-9_-]{10,}|apify_api_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|TABPFN_TOKEN\s*=\s*\S{8,})")
FORBIDDEN_TRACKED = (".env", "data/", "incidents/")
BRAIN_FORBIDDEN = ("ground_truth", "payment_failure")  # extend with each new injection name
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".db", ".sqlite", ".woff", ".woff2"}


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def main():
    problems = []
    for rel in tracked():
        if rel == "scripts/check_guardrails.py":
            continue
        if rel == ".env" or rel.startswith(("data/", "incidents/")):
            problems.append(f"{rel}: must not be tracked (rule 1 / contracts)")
            continue
        path = ROOT / rel
        if path.suffix.lower() in SKIP_SUFFIX or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if SECRET.search(line):
                problems.append(f"{rel}:{n}: looks like a secret (rule 1)")
            if rel.startswith("brain/") and any(w in line for w in BRAIN_FORBIDDEN):
                problems.append(f"{rel}:{n}: brain/ must not use the ground truth or injection names (rule 2)")
    if problems:
        print("Guardrail check FAILED:")
        for p in problems:
            print(" -", p)
        return 1
    print("Guardrail check passed (secrets, tracked data, brain/ vs ground truth).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
