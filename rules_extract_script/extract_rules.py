#!/usr/bin/env python3
"""
Extract common pitfalls, reviewer guidelines, and automatable checks
from Domain-Connect/Templates PR review summaries.

Processing is split into two LLM steps per PR:
  Step 1 — extract_from_pr(): read a single PR file, classify each finding
            into a fixed category taxonomy, output a JSON array.
  Step 2 — consolidate_finding(): for each new finding, iterate over
            existing findings in the same category and ask the LLM yes/no
            whether it is the same issue. Matching and merging are done in
            Python; each LLM call is tiny (one question about one pair).

After consolidation the markdown output is regenerated from rules_state.json
(generate_markdown()), so markdown is always derived from JSON.

rules_state.json finding schema:
  {
    "title": "...",
    "description": "...",
    "count": 3,
    "prs": {
      "0771": {"title": "original finding title", "description": "original text"},
      ...
    }
  }

progress.json gains "pr_findings": { "0771": [ <raw finding list from step 1> ] }
"""

import argparse
import glob
import json
import os
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gpt-oss:20b-128k"
DEFAULT_PR_DIR = os.path.join(
    os.path.dirname(__file__), "..", "review_extraction", "per-pr"
)
DEFAULT_OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "current_state.md")
DEFAULT_RULES_FILE = os.path.join(os.path.dirname(__file__), "rules_state.json")
DEFAULT_PROGRESS_FILE = os.path.join(os.path.dirname(__file__), "progress.json")

# ---------------------------------------------------------------------------
# Fixed category taxonomy — always passed to the LLM in step 1
# ---------------------------------------------------------------------------
CATEGORIES = [
    "Security",
    "DNS Records",
    "Schema / Required Fields",
    "File Naming & Structure",
    "Template Variables & Substitution",
    "Logo & Provider Metadata",
    "Process & Checklist",
    "Testing & Validation",
    "Other",
]

# ---------------------------------------------------------------------------
# Step 1 prompt — extract findings from a single PR
# ---------------------------------------------------------------------------
EXTRACT_SYSTEM_PROMPT = """\
You are an expert analyst reviewing pull-request summaries from the
Domain-Connect/Templates repository.

Your task: read ONE PR summary and extract any substantive findings about
template authoring quality — mistakes, missing fields, security issues,
formatting errors, process violations, or best-practice violations that
reviewers pointed out.

Focus on review comments, change-requests, linter warnings, and substantive
discussion. Ignore simple "LGTM" approvals, automated bot messages, and
praise with no actionable content.

You MUST assign every finding to exactly one of the following categories
(use the exact string, including capitalisation):
{categories_list}

Output a JSON array (and NOTHING else — no prose, no markdown fences).
Each element must have exactly these keys:
  "category"    — one of the category strings listed above
  "title"       — one concise sentence describing the GENERAL rule or pattern,
                  NOT the specific instance from this PR
  "description" — one or two sentences: what the general problem is and what
                  the correct approach is, without mentioning specific field
                  values, provider names, hostnames, or service names from
                  this PR

Generalisation rules — strip all PR-specific detail from title and description:
  - Do NOT mention specific field values (e.g. "optional", "_resend", "eu",
    "us", a particular host name, a particular record value).
  - Do NOT mention the provider name, service name, or any filename.
  - DO describe the class of mistake: what kind of field/record/check is
    wrong and why it matters.
  - If the issue is an unknown or invalid field, say "unknown field" — not
    the field's name.
  - If the issue is a missing required field, name the field only if it is
    part of the Domain Connect specification (e.g. syncPubKeyDomain,
    txtConflictMatchingMode) — omit it otherwise.

If there are NO substantive findings, output an empty JSON array: []

Example — TOO SPECIFIC (wrong):
  title: "Missing txtConflictMatchingMode for _resend TXT record"
  description: "The _resend host TXT record in posthog.eu template is missing
    the txtConflictMatchingMode field."

Example — CORRECTLY GENERALISED (right):
  title: "TXT records missing txtConflictMatchingMode"
  description: "TXT records should declare txtConflictMatchingMode to specify
    how conflicts with existing records are handled. Omitting it can cause
    unintended record overwrites."

Example output array:
[
  {{
    "category": "Security",
    "title": "Missing syncPubKeyDomain for CNAME templates",
    "description": "Templates using CNAME records should include syncPubKeyDomain. Without it the provider cannot cryptographically verify domain ownership."
  }},
  {{
    "category": "Schema / Required Fields",
    "title": "logoUrl points to a non-existent URL",
    "description": "The logoUrl field must resolve to an actually-served image. A broken or placeholder URL causes PR rejection."
  }}
]\
"""

EXTRACT_USER_TEMPLATE = """\
## PR summary to analyse

```markdown
{pr_content}
```

Output the JSON array of findings now.\
"""

# ---------------------------------------------------------------------------
# Step 2 prompt — single yes/no match question (tiny, fast call)
# ---------------------------------------------------------------------------
MATCH_SYSTEM_PROMPT = """\
You are comparing two findings from pull-request reviews of the
Domain-Connect/Templates repository.

Answer with a single JSON object (nothing else):
  {{"match": true}}   — if both findings describe the SAME underlying issue
  {{"match": false}}  — if they describe different issues

Two findings are the SAME issue if fixing one would fix the other,
even if the wording is different. They are DIFFERENT issues if they
require different corrective actions.\
"""

MATCH_USER_TEMPLATE = """\
Finding A (existing):
  title: {title_a}
  description: {desc_a}

Finding B (new):
  title: {title_b}
  description: {desc_b}

Are these the same issue?\
"""

# ---------------------------------------------------------------------------
# Refinement prompt — generalise an entry from all its PR evidence
# ---------------------------------------------------------------------------
REFINE_SYSTEM_PROMPT = """\
You are maintaining a structured knowledge base of review findings from the
Domain-Connect/Templates repository.

You will receive a list of individual observations, all of which have been
determined to describe the same underlying issue in different pull requests.
They are presented in random order.

Your task: synthesise a single, generalised finding that best captures the
common pattern across all observations.

Generalisation rules — the output must describe the CLASS of problem, not any
specific instance:
  - Do NOT mention specific field values, hostnames, record values, or
    provider/service/template names from any individual observation.
  - Do NOT mention the field name of an unknown or invalid field — say
    "unknown field" or "unsupported field" instead.
  - DO name Domain Connect specification fields (e.g. syncPubKeyDomain,
    txtConflictMatchingMode, warnPhishing) only when the issue is specifically
    about that field across the observations.
  - Focus on what kind of mistake is being made and what the correct practice is.

Output a single JSON object (and NOTHING else — no prose, no markdown fences):
{
  "title": "One concise sentence — the generalised class of issue",
  "description": "One or two sentences — what the general problem is and the correct approach"
}\
"""

REFINE_USER_TEMPLATE = """\
Observations (random order):
{observations}

Output the generalised finding JSON now.\
"""

# ---------------------------------------------------------------------------
# Markdown generation — pure Python, no LLM
# ---------------------------------------------------------------------------

def generate_markdown(rules: dict, progress: dict) -> str:
    """Render the rules JSON into a markdown document. prs dict is not shown."""
    lines = [
        "# Domain Connect Templates — PR Review Analysis",
        "",
        "## Purpose",
        "This document accumulates findings from analysing PR review feedback in the",
        "Domain-Connect/Templates repository. It is auto-generated from `rules_state.json`",
        "and regenerated after every PR is processed.",
        "",
        "---",
        "",
    ]

    categories = rules.get("categories", [])
    if not categories:
        lines.append("_(No findings yet — will be populated as PRs are processed.)_")
        lines.append("")
    else:
        for cat_idx, cat in enumerate(categories, start=1):
            findings = cat.get("findings", [])
            if not findings:
                continue
            lines.append(f"## {cat_idx}. {cat['name']}")
            lines.append("")
            for f_idx, finding in enumerate(findings, start=1):
                count = finding.get("count", 1)
                lines.append(f"### {cat_idx}.{f_idx}. {finding['title']} _(×{count})_")
                lines.append("")
                lines.append(finding["description"])
                lines.append("")

    lines += [
        "---",
        "",
        "## Processing Log",
        "| PRs processed | PRs skipped | Errors | Last PR processed | Timestamp |",
        "|---|---|---|---|---|",
    ]
    total_p = progress["counts"]["processed"]
    total_s = progress["counts"]["skipped"]
    total_e = progress["counts"]["errors"]
    last = progress.get("last_pr_processed") or "—"
    ts = progress.get("last_updated") or "—"
    lines.append(f"| {total_p} | {total_s} | {total_e} | {last} | {ts} |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rules state helpers
# ---------------------------------------------------------------------------

def empty_rules() -> dict:
    """Return a fresh rules state with all fixed categories pre-created."""
    return {
        "version": 1,
        "categories": [{"name": c, "findings": []} for c in CATEGORIES],
    }


def category_index(rules: dict) -> dict[str, int]:
    """Return {category_name: list_index} for quick lookup."""
    return {cat["name"]: i for i, cat in enumerate(rules["categories"])}


def ensure_categories(rules: dict) -> None:
    """Add any missing fixed categories to an existing rules dict (migration)."""
    existing = {cat["name"] for cat in rules["categories"]}
    for name in CATEGORIES:
        if name not in existing:
            rules["categories"].append({"name": name, "findings": []})


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
    return "### Reviews" in pr_content or "### Comments" in pr_content


def load_progress(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("pr_findings", {})
        return data
    return new_progress()


def new_progress() -> dict:
    return {
        "version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": None,
        "last_pr_processed": None,
        "processed": [],
        "skipped": [],
        "errors": [],
        "counts": {"processed": 0, "skipped": 0, "errors": 0},
        "pr_findings": {},
    }


def save_progress(path: str, progress: dict) -> None:
    progress["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)
    os.replace(tmp, path)


def load_rules(path: str) -> dict:
    """Load rules state from JSON, or return empty state with fixed categories."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            rules = json.load(f)
        ensure_categories(rules)
        return rules
    seed = os.path.join(os.path.dirname(__file__), "rules_state.seed.json")
    if os.path.exists(seed):
        with open(seed, "r", encoding="utf-8") as f:
            rules = json.load(f)
        ensure_categories(rules)
        return rules
    return empty_rules()


def save_rules(path: str, rules: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rules, f, indent=2)
    os.replace(tmp, path)


def call_ollama(
    ollama_url: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    timeout: int = 1000,
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
        "think": False,
        "options": {
            "temperature": temperature,
            "num_ctx": 128 * 1024,
        },
    }
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    return data["message"]["content"]


def extract_json(raw: str) -> str:
    """Strip optional markdown code fences around a JSON block."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()
    return raw.strip()


# ---------------------------------------------------------------------------
# Two-step LLM processing
# ---------------------------------------------------------------------------

def extract_from_pr(ollama_url: str, model: str, pr_content: str) -> list[dict]:
    """Step 1: extract findings from a single PR, classified into fixed categories."""
    categories_list = "\n".join(f"  - {c}" for c in CATEGORIES)
    system = EXTRACT_SYSTEM_PROMPT.format(categories_list=categories_list)
    user_msg = EXTRACT_USER_TEMPLATE.format(pr_content=pr_content)
    raw = call_ollama(ollama_url, model, system, user_msg)
    cleaned = extract_json(raw)
    findings = json.loads(cleaned)
    if not isinstance(findings, list):
        raise ValueError(f"Expected JSON array, got {type(findings).__name__}")
    # Normalise: unknown categories fall back to "Other"
    known = set(CATEGORIES)
    for f in findings:
        if f.get("category") not in known:
            f["category"] = "Other"
    return findings


def is_same_issue(ollama_url: str, model: str, finding_a: dict, finding_b: dict) -> bool:
    """Step 2 (per pair): ask the LLM whether two findings describe the same issue."""
    user_msg = MATCH_USER_TEMPLATE.format(
        title_a=finding_a["title"],
        desc_a=finding_a["description"],
        title_b=finding_b["title"],
        desc_b=finding_b["description"],
    )
    raw = call_ollama(ollama_url, model, MATCH_SYSTEM_PROMPT, user_msg, temperature=0.0)
    cleaned = extract_json(raw)
    result = json.loads(cleaned)
    return bool(result.get("match", False))


def consolidate_finding(
    ollama_url: str,
    model: str,
    rules: dict,
    new_finding: dict,
    pr_num: str,
) -> None:
    """Merge one new finding into rules in-place. Pure Python bookkeeping after LLM yes/no calls."""
    cat_name = new_finding.get("category", "Other")
    if cat_name not in set(CATEGORIES):
        cat_name = "Other"

    idx = category_index(rules)
    cat = rules["categories"][idx[cat_name]]
    existing_findings = cat["findings"]

    pr_entry = {"title": new_finding["title"], "description": new_finding["description"]}

    # Compare most-popular entries first to maximise consolidation rate
    existing_findings.sort(key=lambda f: f.get("count", 1), reverse=True)

    for existing in existing_findings:
        if is_same_issue(ollama_url, model, existing, new_finding):
            # Matched — increment count and record the PR
            existing["count"] = existing.get("count", 1) + 1
            existing.setdefault("prs", {})[pr_num] = pr_entry
            return

    # No match found — add as a new finding
    existing_findings.append({
        "title": new_finding["title"],
        "description": new_finding["description"],
        "count": 1,
        "prs": {pr_num: pr_entry},
    })


def maybe_refine_findings(
    ollama_url: str,
    model: str,
    rules: dict,
    refine_after: int,
) -> list[str]:
    """
    Scan all findings; for any whose count has grown by refine_after or more
    since the last refinement, call the LLM to produce a generalised title +
    description from all stored PR observations (shuffled).
    Returns a list of human-readable messages describing what was refined.
    """
    messages = []
    for cat in rules["categories"]:
        for finding in cat["findings"]:
            count = finding.get("count", 1)
            count_at = finding.get("count_at_last_refinement", 0)
            if count - count_at < refine_after:
                continue

            prs = finding.get("prs", {})
            if not prs:
                continue

            # Build shuffled list of observations from stored PR entries
            observations = list(prs.values())
            random.shuffle(observations)
            obs_text = "\n".join(
                f"- title: {o['title']}\n  description: {o['description']}"
                for o in observations
            )
            user_msg = REFINE_USER_TEMPLATE.format(observations=obs_text)

            try:
                raw = call_ollama(ollama_url, model, REFINE_SYSTEM_PROMPT, user_msg)
                cleaned = extract_json(raw)
                refined = json.loads(cleaned)
                if "title" not in refined or "description" not in refined:
                    raise ValueError("Missing title or description in refinement response")
            except Exception as e:
                messages.append(f"  REFINE ERROR ({cat['name']} / {finding['title'][:40]}): {e}")
                continue

            old_title = finding["title"]
            finding["title"] = refined["title"]
            finding["description"] = refined["description"]
            finding["count_at_last_refinement"] = count
            finding["last_refined_at"] = datetime.now(timezone.utc).isoformat()
            messages.append(
                f"  refined [{cat['name']}]: \"{old_title[:50]}\" → \"{refined['title'][:50]}\""
            )

    return messages


def sort_rules(rules: dict) -> None:
    """Sort findings within each category by count desc, categories by total count desc."""
    for cat in rules["categories"]:
        cat["findings"].sort(key=lambda f: f.get("count", 1), reverse=True)
    rules["categories"].sort(
        key=lambda c: sum(f.get("count", 1) for f in c["findings"]),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract review rules from PR summaries using Ollama (2-step LLM pipeline)."
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
        "--output-file",
        default=DEFAULT_OUTPUT_FILE,
        help="Path to the output markdown file (default: current_state.md)",
    )
    parser.add_argument(
        "--rules-file",
        default=DEFAULT_RULES_FILE,
        help="Path to the structured rules JSON state file (default: rules_state.json)",
    )
    parser.add_argument(
        "--progress-file",
        default=DEFAULT_PROGRESS_FILE,
        help=f"Path to progress tracking JSON file (default: {DEFAULT_PROGRESS_FILE})",
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
        "--reconsolidate",
        action="store_true",
        help=(
            "Must be used with --resume. Keeps all step-1 extractions from the "
            "previous run (pr_findings in progress.json) but resets rules_state.json "
            "and replays consolidation from scratch using the cached findings."
        ),
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
        help="Create a backup of state files every N PRs (default: 10).",
    )
    parser.add_argument(
        "--refine-after",
        type=int,
        default=3,
        metavar="N",
        help=(
            "Trigger entry refinement when a finding gains N or more new PR matches "
            "since its last refinement (default: 3, set to 0 to disable)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List PRs that would be processed, without calling Ollama.",
    )
    args = parser.parse_args()

    # Resolve paths
    pr_dir = os.path.abspath(args.pr_dir)
    output_file = os.path.abspath(args.output_file)
    rules_file = os.path.abspath(args.rules_file)
    progress_file = os.path.abspath(args.progress_file)
    backup_dir = os.path.join(os.path.dirname(rules_file), "backups")

    # ------------------------------------------------------------------
    # Validate flag combinations
    # ------------------------------------------------------------------
    if args.reconsolidate and not args.resume:
        print("ERROR: --reconsolidate requires --resume", file=sys.stderr)
        sys.exit(1)

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
            f"  --resume              Continue where the previous run stopped\n"
            f"  --resume --reconsolidate  Re-run consolidation from cached extractions\n"
            f"  --startnew            Discard progress and start from scratch\n",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.startnew:
        if progress_exists:
            os.remove(progress_file)
            print(f"Removed existing progress file: {progress_file}")
        rules = empty_rules()
        save_rules(rules_file, rules)
        print(f"Reset {rules_file} with empty fixed-category structure")

    # Discover PR files (defines canonical order: PR number descending)
    pr_files = list_pr_files(pr_dir)
    if not pr_files:
        print(f"No PR-*.md files found in {pr_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(pr_files)} PR files in {pr_dir}")

    # ------------------------------------------------------------------
    # RECONSOLIDATE branch: replay step 2 from cached step-1 findings
    # ------------------------------------------------------------------
    if args.reconsolidate:
        progress = load_progress(progress_file)
        cached = progress.get("pr_findings", {})
        if not cached:
            print("ERROR: No cached pr_findings in progress.json — run without --reconsolidate first.", file=sys.stderr)
            sys.exit(1)

        print(
            f"Reconsolidating {len(cached)} PRs with cached extractions "
            f"(rules reset to empty, step-1 data preserved)."
        )

        # Reset rules to empty; progress extraction data is untouched
        current_rules = empty_rules()
        save_rules(rules_file, current_rules)

        os.makedirs(backup_dir, exist_ok=True)

        reconsolidated = 0
        interrupted = False

        try:
            for i, pr_path in enumerate(pr_files):
                m = re.search(r"PR-(\d+)", pr_path)
                if not m:
                    continue
                pr_num = m.group(1)
                if pr_num not in cached:
                    continue  # was skipped/errored in original run

                new_findings = cached[pr_num]

                # Re-run step 1 if any cached finding has a category outside the fixed set
                known = set(CATEGORIES)
                stale_cats = {f.get("category") for f in new_findings} - known
                if stale_cats:
                    print(
                        f"[{reconsolidated+1}/{len(cached)}] PR-{pr_num}: "
                        f"stale categories {stale_cats} → re-extracting ...",
                        end=" ", flush=True,
                    )
                    try:
                        new_findings = extract_from_pr(args.ollama_url, args.model, read_file(pr_path))
                        # Update cache with fresh findings
                        progress["pr_findings"][pr_num] = new_findings
                        save_progress(progress_file, progress)
                    except Exception as e:
                        print(f"ERROR (re-extract): {e}")
                        continue
                    print(f"{len(new_findings)} finding(s) → matching ...", end=" ", flush=True)
                else:
                    print(
                        f"[{reconsolidated+1}/{len(cached)}] PR-{pr_num}: "
                        f"{len(new_findings)} finding(s) → matching ...",
                        end=" ", flush=True,
                    )

                t0 = time.time()
                try:
                    for new_finding in new_findings:
                        consolidate_finding(
                            args.ollama_url, args.model, current_rules, new_finding, pr_num
                        )
                    sort_rules(current_rules)

                    if args.refine_after > 0:
                        refine_msgs = maybe_refine_findings(
                            args.ollama_url, args.model, current_rules, args.refine_after
                        )
                        for msg in refine_msgs:
                            print(f"\n{msg}", end="")

                    save_rules(rules_file, current_rules)
                    md = generate_markdown(current_rules, progress)
                    write_file(output_file, md)
                    reconsolidated += 1
                except KeyboardInterrupt:
                    raise
                except requests.exceptions.RequestException as e:
                    print(f"ERROR (network): {e}")
                    time.sleep(5)
                    continue
                except Exception as e:
                    print(f"ERROR: {e}")
                    continue

                print(f"done ({time.time()-t0:.1f}s)")

                if args.stop_after and reconsolidated >= args.stop_after:
                    print(f"Reached --stop-after {args.stop_after}, stopping.")
                    break

        except KeyboardInterrupt:
            interrupted = True
            print("\n\nInterrupted!", flush=True)

        print(
            f"\n{'Interrupted.' if interrupted else 'Done.'} "
            f"Reconsolidated {reconsolidated}/{len(cached)} PRs."
        )
        print(f"Rules JSON:  {rules_file}")
        print(f"Findings MD: {output_file}")
        if interrupted:
            sys.exit(130)
        return

    # ------------------------------------------------------------------
    # Normal run (resume or startnew)
    # ------------------------------------------------------------------

    # Load or create progress
    if args.resume and progress_exists:
        progress = load_progress(progress_file)
        already_done = set(progress["processed"] + progress["skipped"])
        retry_count = len(progress["errors"])
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

    # Load current rules state
    current_rules = load_rules(rules_file)
    print(f"Loaded rules state from {rules_file}")

    os.makedirs(backup_dir, exist_ok=True)

    processed = 0
    skipped = 0
    errors = 0
    interrupted = False

    try:
        for i, pr_path in enumerate(pr_files):
            pr_num = re.search(r"PR-(\d+)", pr_path).group(1)

            if pr_num in already_done:
                print(f"[{i+1}/{len(pr_files)}] PR-{pr_num}: already done (previous run)")
                continue

            pr_content = read_file(pr_path)

            if not has_substantive_feedback(pr_content):
                skipped += 1
                progress["skipped"].append(pr_num)
                progress["counts"]["skipped"] += 1
                progress["last_pr_processed"] = pr_num
                save_progress(progress_file, progress)
                print(f"[{i+1}/{len(pr_files)}] PR-{pr_num}: skipped (no reviews/comments)")
                continue

            print(f"[{i+1}/{len(pr_files)}] PR-{pr_num}: extracting ...", end=" ", flush=True)

            t0 = time.time()
            try:
                # Step 1: extract findings from this PR (fixed categories)
                new_findings = extract_from_pr(args.ollama_url, args.model, pr_content)
                t1 = time.time()

                if not new_findings:
                    skipped += 1
                    progress["skipped"].append(pr_num)
                    progress["counts"]["skipped"] += 1
                    progress["last_pr_processed"] = pr_num
                    save_progress(progress_file, progress)
                    print(f"no findings ({t1-t0:.1f}s)")
                    continue

                print(f"{len(new_findings)} finding(s) ({t1-t0:.1f}s) → matching ...", end=" ", flush=True)

                # Step 2: match each finding against existing ones (per-pair yes/no calls)
                for new_finding in new_findings:
                    consolidate_finding(
                        args.ollama_url, args.model, current_rules, new_finding, pr_num
                    )

                sort_rules(current_rules)

                # Refinement pass: generalise entries that have grown enough
                if args.refine_after > 0:
                    refine_msgs = maybe_refine_findings(
                        args.ollama_url, args.model, current_rules, args.refine_after
                    )
                    for msg in refine_msgs:
                        print(f"\n{msg}", end="")

                save_rules(rules_file, current_rules)

                # Regenerate markdown from JSON
                md = generate_markdown(current_rules, progress)
                write_file(output_file, md)

                processed += 1
                progress["processed"].append(pr_num)
                progress["counts"]["processed"] += 1
                progress.setdefault("pr_findings", {})[pr_num] = new_findings

            except KeyboardInterrupt:
                raise
            except (json.JSONDecodeError, ValueError) as e:
                print(f"ERROR (bad JSON): {e}")
                errors += 1
                progress["errors"].append(pr_num)
                progress["counts"]["errors"] += 1
                progress["last_pr_processed"] = pr_num
                save_progress(progress_file, progress)
                continue
            except requests.exceptions.RequestException as e:
                print(f"ERROR (network): {e}")
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
            print(f"done ({elapsed:.1f}s total)")

            # Periodic backup of both state files
            total_done = processed + skipped
            if total_done % args.backup_every == 0:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                for src, suffix in [(rules_file, "json"), (output_file, "md")]:
                    name = f"rules_state_{ts}_PR-{pr_num}.{suffix}"
                    bpath = os.path.join(backup_dir, name)
                    shutil.copy2(src, bpath)
                print(f"  Backups saved to {backup_dir}")

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
    print(
        f"\n{'Interrupted.' if interrupted else 'Done.'} "
        f"This run: processed={processed}, skipped={skipped}, errors={errors}"
    )
    print(f"Cumulative:     processed={total_p}, skipped={total_s}, errors={total_e}")
    print(f"Progress file:  {progress_file}")
    print(f"Rules JSON:     {rules_file}")
    print(f"Findings MD:    {output_file}")

    if interrupted:
        sys.exit(130)


if __name__ == "__main__":
    main()
