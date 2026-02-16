#!/usr/bin/env python3
"""
Extract common pitfalls, reviewer guidelines, and automatable checks
from Domain-Connect/Templates PR review summaries.

Uses a local Ollama instance to process each PR file against a running
"current state" document that accumulates findings.
"""

import argparse
import glob
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gpt-oss:20b-128k"
DEFAULT_PR_DIR = os.path.join(
    os.path.dirname(__file__), "..", "review_extraction", "per-pr"
)
DEFAULT_STATE_FILE = os.path.join(os.path.dirname(__file__), "current_state.md")
DEFAULT_PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "progress.json")

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are an expert analyst reviewing pull-request summaries from the
Domain-Connect/Templates repository.  Your job is to identify:

1. **Common Pitfalls & Errors** — recurring mistakes template authors make
   (e.g. missing fields, wrong record types, security issues, formatting).
2. **Reviewer Guidelines** — things a human reviewer should always check.
3. **Automatable Checks** — issues that could be caught by a linter or CI bot.

You will receive:
- The current accumulated findings document (`current_state.md`).
- One new PR summary to analyse.

Instructions:
- Read the PR summary carefully.  Focus on review comments, change-requests,
  linter warnings, and substantive discussion — NOT simple "lgtm" approvals.
- If the PR has NO substantive review feedback (only approvals with "lgtm",
  no comments, no changes requested, no linter issues), output the current
  state document UNCHANGED but update the Processing Log table at the bottom.
- If there IS substantive feedback, integrate new findings into the
  appropriate sections. Increment counters, add new bullet points, or
  add PR numbers to existing bullets.
- Keep the document well-structured in Markdown.
- Each pitfall/guideline/check entry should note which PR numbers exhibited it.
- Preserve ALL existing findings — never remove earlier entries. However you may consolidate or enhance findings, if they are related.
- For each findings add a counter starting with 1 and incremented each time a PR matches an already existing finding
- The Processing Log table at the very bottom must always be updated with the
  new count and last PR processed.

Output ONLY the updated `current_state.md` content.  No extra commentary.\
"""

USER_PROMPT_TEMPLATE = """\
## Current accumulated findings

```markdown
{current_state}
```

## New PR summary to analyse

```markdown
{pr_content}
```

Now output the fully updated `current_state.md`.\
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_pr_files(pr_dir: str) -> list[str]:
    """Return PR markdown files sorted by PR number descending."""
    pattern = os.path.join(pr_dir, "PR-*.md")
    files = glob.glob(pattern)

    def pr_number(path: str) -> int:
        m = re.search(r"PR-(\d+)\.md$", path)
        return int(m.group(1)) if m else 0

    files.sort(key=pr_number, reverse=True)
    return files


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def has_substantive_feedback(pr_content: str) -> bool:
    """Quick pre-filter: skip PRs that clearly have no review feedback."""
    # Must have at least a Reviews or Comments section
    if "### Reviews" not in pr_content and "### Comments" not in pr_content:
        return False
    # If it only has bot comments and no human reviews, still skip
    # But let the model decide edge cases — just filter the obvious empties
    return True


def load_progress(path: str) -> dict:
    """Load progress state from JSON file, or return empty state."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return new_progress()


def new_progress() -> dict:
    """Return a fresh progress structure."""
    return {
        "version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": None,
        "last_pr_processed": None,
        "processed": [],
        "skipped": [],
        "errors": [],
        "counts": {"processed": 0, "skipped": 0, "errors": 0},
    }


def save_progress(path: str, progress: dict) -> None:
    """Atomically write progress state to JSON file."""
    progress["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
    os.replace(tmp, path)


def call_ollama(
    ollama_url: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout: int = 600,
) -> str:
    """Call Ollama chat completions API and return the assistant message."""
    url = f"{ollama_url}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 65536,
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def extract_markdown(raw: str) -> str:
    """Strip wrapping ```markdown fences if present."""
    raw = raw.strip()
    if raw.startswith("```"):
        # Remove first line (```markdown or ```)
        lines = raw.split("\n", 1)
        raw = lines[1] if len(lines) > 1 else ""
        # Remove trailing ```
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()
    return raw


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract review rules from PR summaries using Ollama."
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL),
        help=f"Ollama API base URL (default: {DEFAULT_OLLAMA_URL})",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL),
        help=f"Ollama model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--pr-dir",
        default=DEFAULT_PR_DIR,
        help="Directory containing PR-*.md files",
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help="Path to current_state.md (read & written in place)",
    )
    parser.add_argument(
        "--progress-file",
        default=DEFAULT_PROGRESS_FILE,
        help=f"Path to progress.json tracking file (default: {DEFAULT_PROGRESS_FILE})",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--resume",
        action="store_true",
        help="Resume processing from where it previously stopped.",
    )
    mode_group.add_argument(
        "--startnew",
        action="store_true",
        help="Discard any existing progress and start from scratch.",
    )

    parser.add_argument(
        "--stop-after",
        type=int,
        default=None,
        help="Stop after processing this many PRs (for testing).",
    )
    parser.add_argument(
        "--backup-every",
        type=int,
        default=10,
        help="Create a backup of state file every N PRs (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List PRs that would be processed, without calling Ollama.",
    )
    args = parser.parse_args()

    # Resolve paths
    pr_dir = os.path.abspath(args.pr_dir)
    state_file = os.path.abspath(args.state_file)
    progress_file = os.path.abspath(args.progress_file)
    backup_dir = os.path.join(os.path.dirname(state_file), "backups")

    # ------------------------------------------------------------------
    # Handle resume / startnew / existing progress
    # ------------------------------------------------------------------
    progress_exists = os.path.exists(progress_file)

    if args.dry_run:
        pass  # dry-run ignores progress state
    elif progress_exists and not args.resume and not args.startnew:
        progress = load_progress(progress_file)
        done = progress["counts"]["processed"] + progress["counts"]["skipped"]
        last = progress.get("last_pr_processed", "?")
        print(
            f"ERROR: Existing progress found in {progress_file}\n"
            f"  ({done} PRs done, last processed: PR-{last})\n"
            f"\n"
            f"Pick one:\n"
            f"  --resume    Continue where the previous run stopped\n"
            f"  --startnew  Discard progress and start from scratch\n",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.startnew:
        if progress_exists:
            os.remove(progress_file)
            print(f"Removed existing progress file: {progress_file}")
        # Also reset current_state.md to the seed template
        seed = os.path.join(os.path.dirname(__file__), "current_state.seed.md")
        if os.path.exists(seed):
            shutil.copy2(seed, state_file)
            print(f"Reset {state_file} from seed template")

    # Discover PR files
    pr_files = list_pr_files(pr_dir)
    if not pr_files:
        print(f"No PR-*.md files found in {pr_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(pr_files)} PR files in {pr_dir}")

    # Load or create progress
    if args.resume and progress_exists:
        progress = load_progress(progress_file)
        # Only treat processed + skipped as done; errors will be retried
        already_done = set(progress["processed"] + progress["skipped"])
        retry_count = len(progress["errors"])
        # Clear errors so they are retried this run
        progress["errors"] = []
        progress["counts"]["errors"] = 0
        print(
            f"Resuming: {progress['counts']['processed']} processed, "
            f"{progress['counts']['skipped']} skipped, "
            f"{retry_count} previous errors will be retried"
        )
    else:
        progress = new_progress()
        already_done = set()

    if args.dry_run:
        for f in pr_files[: args.stop_after]:
            pr_num = re.search(r"PR-(\d+)", f).group(1)
            if pr_num in already_done:
                tag = "DONE(previous run)"
            else:
                content = read_file(f)
                tag = "PROCESS" if has_substantive_feedback(content) else "SKIP(no feedback)"
            print(f"  PR-{pr_num}: {tag}")
        return

    # Load current state markdown
    if os.path.exists(state_file):
        current_state = read_file(state_file)
        print(f"Loaded existing state from {state_file}")
    else:
        print(f"State file not found at {state_file}, will create.", file=sys.stderr)
        current_state = read_file(
            os.path.join(os.path.dirname(__file__), "current_state.md")
        )

    os.makedirs(backup_dir, exist_ok=True)

    processed = 0
    skipped = 0
    errors = 0
    interrupted = False

    try:
        for i, pr_path in enumerate(pr_files):
            pr_num = re.search(r"PR-(\d+)", pr_path).group(1)

            # Skip PRs already handled in a previous run
            if pr_num in already_done:
                print(f"[{i+1}/{len(pr_files)}] PR-{pr_num}: already done (previous run)")
                continue

            pr_content = read_file(pr_path)

            # Pre-filter: skip PRs with no review sections at all
            if not has_substantive_feedback(pr_content):
                skipped += 1
                progress["skipped"].append(pr_num)
                progress["counts"]["skipped"] += 1
                progress["last_pr_processed"] = pr_num
                save_progress(progress_file, progress)
                print(f"[{i+1}/{len(pr_files)}] PR-{pr_num}: skipped (no reviews/comments)")
                continue

            print(f"[{i+1}/{len(pr_files)}] PR-{pr_num}: processing ...", end=" ", flush=True)

            user_msg = USER_PROMPT_TEMPLATE.format(
                current_state=current_state,
                pr_content=pr_content,
            )

            t0 = time.time()
            try:
                raw_response = call_ollama(
                    ollama_url=args.ollama_url,
                    model=args.model,
                    system=SYSTEM_PROMPT,
                    user=user_msg,
                )
                updated_state = extract_markdown(raw_response)

                # Sanity check: response should still contain key headings
                if "## 1." in updated_state and "## 2." in updated_state:
                    current_state = updated_state
                    write_file(state_file, current_state)
                    processed += 1
                    progress["processed"].append(pr_num)
                    progress["counts"]["processed"] += 1
                else:
                    print(f"WARNING: response missing expected headings, keeping old state")
                    errors += 1
                    progress["errors"].append(pr_num)
                    progress["counts"]["errors"] += 1

            except KeyboardInterrupt:
                # Re-raise to be caught by the outer handler
                raise
            except requests.exceptions.RequestException as e:
                print(f"ERROR: {e}")
                errors += 1
                progress["errors"].append(pr_num)
                progress["counts"]["errors"] += 1
                progress["last_pr_processed"] = pr_num
                save_progress(progress_file, progress)
                time.sleep(5)
                continue
            except Exception as e:
                print(f"ERROR: {e}")
                errors += 1
                progress["errors"].append(pr_num)
                progress["counts"]["errors"] += 1
                progress["last_pr_processed"] = pr_num
                save_progress(progress_file, progress)
                continue

            elapsed = time.time() - t0
            progress["last_pr_processed"] = pr_num
            save_progress(progress_file, progress)
            print(f"done ({elapsed:.1f}s)")

            # Periodic backup
            total_done = processed + skipped
            if total_done % args.backup_every == 0:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                backup_path = os.path.join(backup_dir, f"current_state_{ts}_PR-{pr_num}.md")
                shutil.copy2(state_file, backup_path)
                print(f"  Backup saved: {backup_path}")

            # Stop-after limit (counts only model-processed PRs)
            if args.stop_after and processed >= args.stop_after:
                print(f"Reached --stop-after {args.stop_after}, stopping.")
                break

    except KeyboardInterrupt:
        interrupted = True
        print("\n\nInterrupted! Saving progress ...", flush=True)
        save_progress(progress_file, progress)
        print(f"Progress saved to {progress_file}")
        print(f"Resume later with: --resume")

    total_p = progress["counts"]["processed"]
    total_s = progress["counts"]["skipped"]
    total_e = progress["counts"]["errors"]
    print(f"\n{'Interrupted.' if interrupted else 'Done.'} "
          f"This run: processed={processed}, skipped={skipped}, errors={errors}")
    print(f"Cumulative:     processed={total_p}, skipped={total_s}, errors={total_e}")
    print(f"Progress file:  {progress_file}")
    print(f"Findings:       {state_file}")

    if interrupted:
        sys.exit(130)


if __name__ == "__main__":
    main()
