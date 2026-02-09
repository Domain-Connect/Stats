# Domain Connect Templates - Statistics Dashboard

This directory contains the statistics dashboard for the Domain Connect Templates repository.

## Files

- **index.html** - Main dashboard page with interactive charts and tables
- **styles.css** - Stylesheet with Domain Connect branding
- **stats.json** - Generated statistics data (updated automatically)
- **assets/** - Logos and images

## Viewing the Dashboard

### Local Viewing

Simply open `index.html` in a web browser:

```bash
open docs/index.html
# or
firefox docs/index.html
# or
chrome docs/index.html
```

### Online Viewing

The dashboard can be viewed online via GitHub Pages or CDN services like:
- GitHub Pages: Configure repository settings to serve from `/dashboard` directory
- RawGit/GitHack: `https://raw.githack.com/Domain-Connect/Templates/master/docs/index.html`

## Statistics Included

### Summary Metrics
- Total Templates
- Total Service Providers
- Total Merged PRs
- Total Open PRs
- Total Contributors
- Average Records per Template

### Charts
1. **Templates Growth Over Time** - Monthly template additions and cumulative total
2. **Pull Request Activity** - PRs created vs merged by month
3. **Record Types Distribution** - DNS record types usage (donut chart)

### Tables
- **Top Providers** - Ranked by template count (all-time and last 30 days)
- **Recent Pull Requests** - Latest PRs with templates, status, and labels

## Features

- **Responsive Design** - Works on desktop, tablet, and mobile
- **Interactive Charts** - Zoom, pan, and download capabilities via Plotly.js
- **Auto-updates** - Statistics refreshed daily via GitHub Actions
- **Brand Consistent** - Uses Domain Connect official colors and styling

## Technical Details

- **Static HTML** - No server required, runs entirely in the browser
- **CDN Libraries** - Plotly.js loaded from CDN for easy updates
- **Data Format** - All data loaded from `stats.json` in JSON format
- **Cross-browser** - Compatible with modern browsers (Chrome, Firefox, Safari, Edge)

## Updating Statistics

Statistics are automatically updated by GitHub Actions workflow, but you can also update manually:

```bash
# Set GitHub token for API access
export GITHUB_TOKEN="your_token_here"

# Run the update script
python scripts/update_stats.py

# View the dashboard
open docs/index.html
```

## Customization

To customize the dashboard:

1. **Colors** - Edit CSS variables in `styles.css`
2. **Charts** - Modify Plotly configurations in `index.html`
3. **Data** - Update `scripts/update_stats.py` to collect additional metrics

See [CLAUDE.md](../CLAUDE.md) in the repository root for complete development guidelines.
