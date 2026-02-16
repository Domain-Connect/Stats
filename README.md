# Domain Connect Templates — Statistics & Analysis Tools

Tooling for generating statistics, extracting PR review data, and analysing
review patterns from the
[Domain-Connect/Templates](https://github.com/Domain-Connect/Templates)
repository.

## Repository layout

```
.
├── docs/                        # Statistics dashboard (static site)
├── scripts/                     # Data generation scripts (Python)
├── rules_extract_script/        # PR review analysis via local LLM
├── .github/workflows/           # CI/CD automation
├── _claude_prompt.txt           # Original requirements spec for the dashboard
├── CLAUDE.md                    # Development guidelines for AI-assisted editing
├── LICENSE                      # MIT
└── README.md
```

## docs/ — Statistics Dashboard

A static HTML dashboard that visualises the health and activity of the
Domain Connect Templates repository.

| File | Description |
|---|---|
| `index.html` | Single-page dashboard with interactive Plotly.js charts |
| `styles.css` | Stylesheet using Domain Connect brand colours |
| `stats.json` | All chart/table data (auto-generated, do not edit) |
| `assets/` | Domain Connect logos and favicons |

Open `docs/index.html` in a browser to view. Charts support zoom, pan, and
data download. Clicking chart elements links to GitHub code search.

### What the dashboard shows

- Template and provider growth over time
- Pull request activity (opened vs merged per month)
- DNS record type distribution across templates
- Feature usage (syncPubKeyDomain, syncRedirectDomain, warnPhishing, hostRequired)
- Top service providers (all-time and last 30 days)
- Recent pull requests with status and labels
- Top PR reviewers

## scripts/ — Data Generation

Python scripts that collect data from the Templates repository and the
GitHub API.

| File | Description |
|---|---|
| `update_stats.py` | Main statistics generator — parses template JSON files, analyses git history, fetches PR data from GitHub API, and writes `docs/stats.json` |
| `extract_pr_data.py` | Extracts detailed PR metadata, comments, and reviews into structured JSON and per-PR Markdown summaries |
| `setup_local_dev.sh` | Configures `git skip-worktree` on cache files so local runs don't dirty the index |
| `requirements.txt` | Python dependencies (`requests`, `python-dateutil`) |

### Running locally

```bash
pip install -r scripts/requirements.txt
export GITHUB_TOKEN="ghp_..."

# Generate stats.json
python scripts/update_stats.py --folder /path/to/Templates \
    --repo-owner Domain-Connect --repo-name Templates

# Extract PR review data
python scripts/extract_pr_data.py --repo-owner Domain-Connect --repo-name Templates
```

## .github/workflows/ — CI/CD

| File | Description |
|---|---|
| `update-stats.yml` | Runs `update_stats.py` daily at midnight UTC, on push to `main`, or on manual dispatch. Commits updated `stats.json` back to the repository. |

## rules_extract_script/ — PR Review Analysis

Processes the per-PR Markdown summaries (produced by `extract_pr_data.py`)
through a local [Ollama](https://ollama.com/) LLM to iteratively build a
document of:

1. Common pitfalls and errors in template creation
2. Guidelines for future reviewers
3. Checks that can be automated (linter rule candidates)

| File | Description |
|---|---|
| `extract_rules.py` | Main script — feeds each PR summary + accumulated findings to the LLM, updates findings in place |
| `current_state.seed.md` | Blank seed template used when starting a fresh run |
| `install.sh` | Creates a Python virtualenv and installs dependencies |
| `requirements.txt` | Python dependencies (`requests`) |

### Setup and usage

```bash
cd rules_extract_script
bash install.sh
source venv/bin/activate

# First run (no prior progress)
python extract_rules.py

# Resume after interruption (Ctrl-C saves state automatically)
python extract_rules.py --resume

# Discard prior progress and start over
python extract_rules.py --startnew

# Custom Ollama endpoint / model
python extract_rules.py --ollama-url http://host:11434 --model gpt-oss:20b

# Preview what would be processed
python extract_rules.py --dry-run
```

Key behaviours:
- Processes PRs in reverse number order (newest first)
- Skips PRs with no review comments or feedback
- Saves `progress.json` after every PR for reliable resume
- On `--resume`, previously errored PRs are automatically retried
- Ctrl-C is handled gracefully — progress is saved before exit
- Creates periodic backups of the findings document

## License

MIT — see [LICENSE](LICENSE).
