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
│   ├── requirements.txt        # Python dependencies
│   └── pr_reviews_cache.json   # Cache for PR review data (committed in CI)
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

4. **Record Types Distribution**
   - Horizontal bar chart showing % of templates containing each DNS record type
   - Count unique record types per template (e.g. 5 CNAMEs in one template counts as 1)
   - Bars show percentage with label "X% (count/total)"
   - Clicking a bar opens GitHub code search for that record type

5. **Feature Usage**
   - Row of donut charts below Record Types Distribution
   - Each donut shows % of templates using a feature:
     - `syncPubKeyDomain` (present and non-empty)
     - `syncRedirectDomain` (present and non-empty)
     - `warnPhishing` (present and set to `true`)
     - `hostRequired` (present and set to `true`)
   - Percentage displayed in center of each donut, clickable to GitHub search

### Top Providers
- Two tables: All-time and Last 30 days
- Shows provider logo (from first template) to the left of provider name
- Uses `providerName` from template content (not filename-derived provider ID)
- Number of templates is a clickable link opening GitHub code search for that `providerId`

### Recent Pull Requests
- Table showing: PR number, title, status, labels, open date, GitHub link
- Content: All open PRs + last 10 merged PRs
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
- Caches PR review data in `scripts/pr_reviews_cache.json` to avoid repeated API calls
- Cache is automatically updated when new PRs are processed
- In CI/CD, the cache file is committed to the repository for persistent benefit across runs

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

### Running in CI/CD
- Triggered on push to master and on schedule (daily at midnight UTC)
- Uses `secrets.GITHUB_TOKEN` automatically provided by GitHub Actions
- Commits updated stats.json and pushes back to repository

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
- Optional fields: `logoUrl`, `description`, `version`

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
