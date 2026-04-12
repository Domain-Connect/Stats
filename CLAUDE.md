# Domain Connect Templates - Development Guidelines

This document contains guidelines for maintaining and developing the Domain Connect Templates repository statistics dashboard.

## Project Structure

```
.
├── docs/              # Statistics dashboard output directory
│   ├── index.html         # Main statistics page (static HTML)
│   ├── styles.css         # Styling with Domain Connect branding
│   ├── stats.json         # Generated statistics data
│   ├── _claude_prompt.txt # Requirements specification for the dashboard
│   └── assets/            # Logos and images
├── scripts/               # Python scripts for statistics generation
│   ├── update_stats.py         # Main script to generate stats.json
│   ├── check_syntax_rules.py   # Template syntax/field validator
│   ├── requirements.txt        # Python dependencies
│   ├── pr_reviews_cache.json   # Cache for PR review data (use skip-worktree locally)
│   └── setup_local_dev.sh      # Helper to configure local development (sets skip-worktree)
├── .github/workflows/     # CI/CD workflows
│   └── update-stats.yml   # Workflow to auto-update statistics
└── [provider].[service].json  # Template files (root directory)
```

## Statistics Dashboard Architecture

### Design Principles
- **Static HTML**: Dashboard is a single static HTML file with embedded JavaScript
- **Responsive Design**: Must work well on both desktop and mobile devices
- **Offline-capable**: All data loaded from stats.json, no external API calls from frontend
- **Interactive**: Graphs support zoom, pan, and data download features

### Brand Colors (from domainconnect.org)
- Primary: `#03263B` (dark navy)
- Secondary: `#0b3954` (lighter navy)
- Tertiary: `#bddae6` (light blue-gray)
- Accent: `#ff6663` (coral-red)
- Link: `#00bfff` (bright cyan)
- Text: `#252525` (charcoal)

### Charting Library
- **Plotly.js**: Used for all interactive graphs
- Features: Built-in zoom/pan, download options, responsive design
- CDN-loaded for easier maintenance

### Interactive Elements
- Chart bars/donuts link to GitHub code search when clicked
- Search URLs use escaped JSON patterns (e.g. `"\"type\": \"CNAME\""`)
- Provider template counts link to GitHub code search for `providerId`
- Feature usage donut percentages link to GitHub code search for that feature

## Statistics Collected

### Summary Metrics
- Last updated date
- Total Templates
- Total Service Providers
- Total Merged PRs
- Total Open PRs
- Total Contributors
- Average Records per Template

### Graphs
1. **Templates Growth Over Time**
   - Monthly data since repository inception
   - Dual Y-axis: Cumulative total (line) + Monthly additions (bar)
   - Both scales start at zero

2. **Provider Growth Over Time**
   - Monthly data since repository inception
   - Dual Y-axis: Cumulative providers (line) + Monthly additions (bar)
   - Both scales start at zero
   - Uses `providerId` from template JSON content (not filename-derived)

3. **Pull Requests Activity**
   - Monthly PRs created vs PRs merged (bar chart)
   - Only counts PRs that touch at least one template file

4. **Pull Request Time to Merge**
   - Bar chart: month (of opening) on X-axis, average days from open to merge on Y-axis (1 decimal)
   - Only merged template PRs counted; bucket = `created_at` month
   - Computed purely from `created_at`/`merged_at` on PR objects — no extra API calls

6. **Pull Request Comment Activity**
   - Bar chart: month on X-axis, average comments per PR on Y-axis (1 decimal)
   - Only template PRs; counts issue comments + review comments; excludes bot accounts (`user.type == 'Bot'` or login ending in `[bot]`)
   - Fetched via `/issues/{number}/comments` and `/pulls/{number}/comments`, cached under `comment_counts` key (integer total per PR)

7. **Pull Request Label Activity**
   - Line chart: month on X-axis, number of PRs on Y-axis, each label as a separate data series
   - Only template PRs; counts distinct PRs per label per month (bucket = PR `created_at` month)
   - Includes labels ever applied to a PR, even if later removed (uses GitHub Issues Events API `labeled` events)
   - Data fetched via `/repos/{owner}/{repo}/issues/{number}/events`, cached in `pr_reviews_cache.json` under `label_events` key

8. **Record Types Distribution**
   - Horizontal bar chart showing % of templates containing each DNS record type
   - Count unique record types per template (e.g. 5 CNAMEs in one template counts as 1)
   - Bars show percentage with label "X% (count/total)"
   - Clicking a bar opens GitHub code search for that record type

9. **Feature Usage**
   - Row of donut charts below Record Types Distribution
   - Each donut shows % of templates using a feature:
     - `syncPubKeyDomain` (present and non-empty)
     - `syncRedirectDomain` (present and non-empty)
     - `warnPhishing` (present and set to `true`)
     - `hostRequired` (present and set to `true`)
   - Percentage displayed in center of each donut, clickable to GitHub search

### Providers
- Two tables: All-time and Recently Added (last 30 new providers)
- Shows provider logo (from first template) to the left of provider name
- Uses `providerName` from template content (not filename-derived provider ID)
- Number of templates is a clickable link opening GitHub code search for that `providerId`
- All-time: sorted by template count descending (top 20)
- Recently Added: the 20 most recently first-added providers, ordered by first-added date descending; determined via git history (oldest-first traversal to find when each provider_id first appeared)

### Open Pull Requests
- Table showing: PR number, title, status, labels, open date, GitHub link
- Content: All open PRs only
- Each PR shows associated provider/service IDs and logos

### Top Pull Request Reviewers
- Two side-by-side tables: All-time and Last 30 days
- Shows top 5 reviewers for each period
- Displays: Rank, reviewer avatar, username (linked to GitHub profile), review count
- Review count = number of PRs reviewed (each reviewer counted once per PR)
- Excludes self-reviews (PR author reviewing their own PR)

## update_stats.py Script

### Requirements
- Python 3.8+
- Dependencies: `requests`
- GitHub API token via `GITHUB_TOKEN` environment variable

### CLI Arguments
- `--folder FOLDER`: Path to templates repository folder (default: 'Templates')
- `--repo-owner` / `--repo-name`: Specify GitHub repo directly (must be used together)
- `--remote`: Specify git remote name for auto-detection (e.g. `upstream`)
- If neither `--repo-owner/--repo-name` nor `--remote` provided, auto-detects from the single git remote (aborts if multiple remotes exist)

### Functionality
1. Parse all `*.json` template files in the specified folder
2. Analyze git history for commits, template additions, and provider growth
3. Fetch PR data from GitHub API (using token)
4. Calculate all statistics: growth, record types, feature usage, provider metadata
5. Generate `docs/stats.json` with complete dataset

### GitHub API Usage
- Uses `GITHUB_TOKEN` from environment for authentication
- Fetches PR data, contributor info, and repository metadata
- Implements rate limiting and error handling
- Caches per-PR data in `scripts/pr_reviews_cache.json` to avoid repeated API calls
  - Cache maps PR number → dict with keys: `reviews`, `label_events`, `comment_counts`, `has_templates`
  - Legacy entries (plain list) are migrated on load to `{reviews: [...]}` automatically
  - **When adding a new cached field:** if the field is absent from ALL cached entries, fetch it for every PR; otherwise fetch only for entries missing that field. Never re-fetch already-cached fields.
- Cache is automatically updated when new PRs are processed
- Cache file is committed to repository but can be ignored locally using `git update-index --skip-worktree`

### Running Locally
```bash
export GITHUB_TOKEN="your_token_here"

# Using default folder (Templates):
python scripts/update_stats.py --repo-owner Domain-Connect --repo-name Templates

# Using custom folder:
python scripts/update_stats.py --folder /path/to/templates --repo-owner Domain-Connect --repo-name Templates

# Auto-detect remote (requires exactly one git remote):
python scripts/update_stats.py --remote upstream

# Specify owner/name directly (skips git remote detection):
python scripts/update_stats.py --repo-owner Domain-Connect --repo-name Templates
```

#### Setting Up Local Development

To prevent accidentally committing cache file changes:

```bash
# Run once to configure local development
./scripts/setup_local_dev.sh
```

This uses `git update-index --skip-worktree` to tell git to ignore local changes to the cache file. The cache will still be pulled from the repository but your local modifications won't show up in `git status`.

### Running in CI/CD
- Triggered on push to main and on schedule (daily at midnight UTC)
- Uses `secrets.GITHUB_TOKEN` automatically provided by GitHub Actions
- Automatically enables cache file tracking with `--no-skip-worktree`
- Commits updated `stats.json` and `pr_reviews_cache.json` back to repository

## check_syntax_rules.py Script

### Purpose
Validates every `*.json` template file in the Templates folder against a set of
typed syntax rules derived from the Domain Connect specification.  Exits with
code 1 if any rule violations are found; warnings (deprecations) are printed but
do not affect the exit code.

### Requirements
- Python 3.8+ (no third-party dependencies)

### CLI Arguments
- `--folder FOLDER`: Path to templates folder (default: `Templates`)
- `--list-unchecked`: Print all top-level template properties not yet covered by
  any rule, then exit.  Useful when reviewing spec changes.

### Running Locally
```bash
# Check all templates in the default folder:
python scripts/check_syntax_rules.py

# Check a custom folder:
python scripts/check_syntax_rules.py --folder /path/to/templates

# List properties not yet covered by any rule:
python scripts/check_syntax_rules.py --list-unchecked
```

### Output Format
- `FAIL  <filename>` — file has one or more rule violations, followed by indented
  error messages.
- `WARN  <filename>` — file has deprecation warnings but no errors, followed by
  indented warning messages.  Files with both errors and warnings show the `FAIL`
  header; warnings are printed underneath.
- Summary line: `N files checked, F with errors, W with warnings only.`

### Rules Implemented

| # | Field(s) | Constraint |
|---|----------|-----------|
| 1 | `syncRedirectDomain` | `dc-host-list = domain-name *( *SP "," *SP domain-name )` — comma-separated list of RFC 5890 domain names; spaces around commas allowed |
| 2 | `providerId`, `serviceId`, `groupId` (per record) | `dc-id = 1*63( ALPHA / DIGIT / "-" / "_" / "." )` |
| 3 | `syncPubKeyDomain` | `dc-pubkey-domain = *( dc-underscore-label "." ) domain-name` — optional RFC 8552 underscore labels prepended to an RFC 5890 domain name |
| 4 | `providerName`, `serviceName` | `dc-display-name = 1*255unicode-assignable` — non-empty, ≤ 255 Unicode Assignable code points (RFC 9839 §4.3) |
| 5 | `sharedProviderName`, `sharedServiceName`, `syncBlock`, `multiInstance`, `hostRequired`, `shared`, `warnPhishing` | JSON boolean (`true` / `false`) |
| 6 | `description`, `variableDescription` | `dc-description-text = 0*2048unicode-assignable` — empty or ≤ 2048 Unicode Assignable code points (RFC 9839 §4.3) |
| 7 | `version` | `dc-version = %x31-39 *DIGIT` — positive integer ≥ 1, no leading zeros, when present |
| 8 | `logoUrl` | Valid RFC 3986 URI with scheme `https` or `http`, or empty string, when present (`http` triggers a deprecation warning) |

### Deprecation Warnings

| Trigger | Message |
|---------|---------|
| `shared` is present but `sharedProviderName` is absent | Migrate `shared` → `sharedProviderName` |
| `logoUrl` uses `http` scheme | Migrate `logoUrl` to `https` |

### Adding or Changing Rules
1. Add or update the validator function (e.g. `is_valid_*`) in the appropriate
   section of `check_syntax_rules.py`.
2. Add or update the `rule_*` function and append it to `RULES`.
3. Add the covered field name(s) to `_CHECKED_PROPERTIES` so they disappear from
   `--list-unchecked` output.
4. Update the module docstring rules list and this CLAUDE.md table.

For deprecation warnings (non-fatal), add a `warn_*` function to `WARNINGS`
instead of `RULES`.  Warning functions have the same signature but their messages
do not cause a non-zero exit code.

## Development Guidelines

### Adding or Changing Statistics
1. Update `update_stats.py` to calculate new metric
2. Add data to the stats.json schema
3. Update `docs/index.html` to display the new data
4. Update `docs/_claude_prompt.txt` to reflect the new requirements
5. Update this `CLAUDE.md` to document the change

**Important:** `docs/_claude_prompt.txt` and `CLAUDE.md` must always be kept in sync with the actual implementation. Any change to dashboard features, chart types, data sources, script arguments, or interactive behavior must be reflected in both files.

### Template File Format
- Naming: `[providerId].[serviceId].json`
- Required fields: `providerId`, `providerName`, `serviceId`, `serviceName`, `records`
- Optional fields: `logoUrl`, `description`, `variableDescription`, `version`,
  `syncPubKeyDomain`, `syncRedirectDomain`, `hostRequired`, `warnPhishing`,
  `syncBlock`, `multiInstance`, `shared`, `sharedProviderName`, `sharedServiceName`

### Code Style
- Python: PEP 8 compliant, type hints where appropriate
- JavaScript: Modern ES6+, async/await for clarity
- HTML/CSS: Semantic HTML5, mobile-first responsive design
- Comments: Explain "why", not "what"

### Testing Changes
1. Run `update_stats.py` locally to generate stats.json
2. Open `docs/index.html` in browser to verify display
3. Test responsive design at various screen widths
4. Verify all interactive features (zoom, download, etc.)

### Pull Request Guidelines
- Keep template changes separate from dashboard changes
- Test statistics generation before submitting PR
- Ensure CI/CD passes (template linting + stats generation)
- Update CLAUDE.md if architecture changes

## File Maintenance

### What to Edit
- `scripts/update_stats.py`: Statistics calculation logic
- `scripts/check_syntax_rules.py`: Template field syntax rules and deprecation warnings
- `docs/index.html`: Layout and JavaScript for visualization
- `docs/styles.css`: Styling and responsive design
- `.github/workflows/update-stats.yml`: CI/CD automation

### What NOT to Edit Manually
- `docs/stats.json`: Auto-generated by update_stats.py
- Template files: Follow template contribution guidelines in README.md

## Security Considerations
- GitHub token must have read access to repository
- Never commit tokens or credentials to repository
- stats.json contains only public repository data
- No sensitive information exposed in dashboard

## Browser Support
- Modern browsers (Chrome, Firefox, Safari, Edge)
- ES6+ JavaScript support required
- Plotly.js compatible browsers
- Mobile browsers (iOS Safari, Chrome Mobile)
