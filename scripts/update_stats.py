#!/usr/bin/env python3
"""
Domain Connect Templates Statistics Generator

This script generates statistics from the Domain Connect Templates repository
and outputs them to docs/stats.json for visualization.

Requirements:
    - Python 3.8+
    - requests library
    - GITHUB_TOKEN environment variable for API access

Usage:
    python scripts/update_stats.py [--folder FOLDER] [--repo-owner OWNER --repo-name NAME]

    Options:
        --folder FOLDER      Path to templates repository folder (default: 'Templates')
        --repo-owner OWNER   GitHub repository owner
        --repo-name NAME     GitHub repository name
        --remote REMOTE      Git remote name for auto-detection
"""

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import re

try:
    import requests
except ImportError:
    print("Error: 'requests' library not found. Install with: pip install requests")
    sys.exit(1)


class StatsGenerator:
    """Generate statistics for Domain Connect Templates repository."""

    def __init__(self, repo_path: str = ".", repo_owner: Optional[str] = None,
                 repo_name: Optional[str] = None, remote: Optional[str] = None):
        """Initialize the statistics generator.

        Args:
            repo_path: Path to the repository root
            repo_owner: GitHub repository owner (overrides auto-detection)
            repo_name: GitHub repository name (overrides auto-detection)
            remote: Git remote name to use for auto-detection
        """
        self.repo_path = Path(repo_path).resolve()
        self.github_token = os.environ.get("GITHUB_TOKEN")
        self.github_api = "https://api.github.com"
        self.cache_file = Path(__file__).parent / "pr_reviews_cache.json"

        if repo_owner and repo_name:
            self.repo_owner = repo_owner
            self.repo_name = repo_name
        else:
            self.repo_owner, self.repo_name = self._get_repo_info(remote)

    def _get_repo_info(self, remote: Optional[str] = None) -> Tuple[str, str]:
        """Extract repository owner and name from git remote.

        Args:
            remote: Git remote name to use. If None, auto-detected
                    (must be unambiguous — aborts if multiple remotes exist).
        """
        try:
            if not remote:
                remote = self._resolve_remote()

            result = subprocess.run(
                ["git", "remote", "get-url", remote],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )
            remote_url = result.stdout.strip()

            # Parse GitHub URL (both HTTPS and SSH formats)
            # https://github.com/owner/repo.git or git@github.com:owner/repo.git
            match = re.search(r'github\.com[:/](.+?)/(.+?)(?:\.git)?$', remote_url)
            if match:
                return match.group(1), match.group(2)

            print(f"Error: Could not parse GitHub owner/repo from remote '{remote}' URL: {remote_url}")
            sys.exit(1)
        except subprocess.CalledProcessError:
            print(f"Error: Git remote '{remote}' not found.")
            sys.exit(1)

    def _resolve_remote(self) -> str:
        """Resolve which git remote to use.

        Returns the remote name if exactly one exists, aborts otherwise.
        """
        result = subprocess.run(
            ["git", "remote"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True
        )
        remotes = [r for r in result.stdout.strip().splitlines() if r]

        if len(remotes) == 0:
            print("Error: No git remotes configured.")
            sys.exit(1)
        elif len(remotes) == 1:
            return remotes[0]
        else:
            print(f"Error: Multiple git remotes found: {', '.join(remotes)}")
            print("Please specify which remote to use with --remote, "
                  "or provide --repo-owner and --repo-name directly.")
            sys.exit(1)

    def _github_api_request(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make a request to GitHub API.

        Args:
            endpoint: API endpoint (without base URL)
            params: Query parameters

        Returns:
            JSON response data
        """
        url = f"{self.github_api}{endpoint}"
        headers = {"Accept": "application/vnd.github.v3+json"}

        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Warning: GitHub API request failed: {e}")
            return None

    def _get_all_paginated(self, endpoint: str, params: Optional[Dict] = None) -> List[Any]:
        """Get all pages of a paginated GitHub API endpoint.

        Args:
            endpoint: API endpoint
            params: Query parameters

        Returns:
            List of all items across all pages
        """
        if params is None:
            params = {}
        
        params.setdefault("per_page", 100)
        params.setdefault("page", 1)

        all_items = []

        print(f"    [DEBUG] Paginating {endpoint} with params: {params}")

        while True:
            items = self._github_api_request(endpoint, params)
            if not items:
                print(f"    [DEBUG] Page {params['page']}: empty response, stopping")
                break

            all_items.extend(items)
            print(f"    [DEBUG] Page {params['page']}: got {len(items)} items (total so far: {len(all_items)})")

            # Check if there are more pages
            if len(items) < params["per_page"]:
                print(f"    [DEBUG] Last page reached (got {len(items)} < {params['per_page']})")
                break

            params["page"] += 1

        print(f"    [DEBUG] Done paginating {endpoint}: {len(all_items)} total items")
        return all_items

    def get_template_files(self) -> List[Path]:
        """Get all template JSON files in repository root.

        Returns:
            List of template file paths
        """
        template_files = []
        for file_path in self.repo_path.glob("*.json"):
            # Skip non-template files
            if file_path.name in ["package.json", "package-lock.json"]:
                continue
            template_files.append(file_path)

        return sorted(template_files)

    def parse_template(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Parse a template JSON file.

        Args:
            file_path: Path to template file

        Returns:
            Parsed template data or None if invalid
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: Could not parse {file_path.name}: {e}")
            return None

    def get_record_types(self, template: Dict[str, Any]) -> Set[str]:
        """Extract unique record types from a template.

        Args:
            template: Parsed template data

        Returns:
            Set of record types (e.g., 'A', 'CNAME', 'MX')
        """
        record_types = set()
        records = template.get("records", [])

        for record in records:
            record_type = record.get("type")
            if record_type:
                record_types.add(record_type)

        return record_types

    def get_git_history(self) -> List[Dict[str, Any]]:
        """Get git commit history for template files.

        Returns:
            List of commit info dictionaries
        """
        try:
            # Get all commits with file changes
            result = subprocess.run(
                ["git", "log", "--all", "--date=short", "--name-only",
                 "--pretty=format:%ad|%H|%an|%ae"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                check=True
            )

            commits = []
            lines = result.stdout.strip().split('\n')
            current_commit = None

            for line in lines:
                if not line.strip():
                    continue

                if '|' in line:
                    # Commit header line
                    parts = line.split('|')
                    if len(parts) == 4:
                        current_commit = {
                            'date': parts[0],
                            'hash': parts[1],
                            'author': parts[2],
                            'email': parts[3],
                            'files': []
                        }
                        commits.append(current_commit)
                elif current_commit and line.endswith('.json'):
                    # File change line
                    current_commit['files'].append(line)

            return commits
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not get git history: {e}")
            return []

    def calculate_monthly_growth(self, commits: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate monthly template growth statistics.

        Args:
            commits: List of commit data

        Returns:
            Dictionary with monthly statistics
        """
        # Track when each template was first added
        template_first_seen = {}

        # Process commits in chronological order (oldest first)
        for commit in reversed(commits):
            commit_date = commit['date']
            for file in commit['files']:
                if file.endswith('.json') and file not in template_first_seen:
                    template_first_seen[file] = commit_date

        # Group by month
        monthly_additions = defaultdict(int)
        for template, date_str in template_first_seen.items():
            # Convert to YYYY-MM format
            year_month = date_str[:7]  # Get YYYY-MM from YYYY-MM-DD
            monthly_additions[year_month] += 1

        # Calculate cumulative totals
        sorted_months = sorted(monthly_additions.keys())
        cumulative = 0
        monthly_data = []

        for month in sorted_months:
            cumulative += monthly_additions[month]
            monthly_data.append({
                'month': month,
                'added': monthly_additions[month],
                'cumulative': cumulative
            })

        return {
            'monthly': monthly_data,
            'total_templates': cumulative
        }

    def calculate_provider_growth(self, commits: List[Dict[str, Any]],
                                  templates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Calculate monthly provider growth statistics.

        Determines when each unique providerId first appeared by correlating
        git history (when files were first committed) with parsed template data
        (which contains the actual providerId).

        Args:
            commits: List of commit data from get_git_history()
            templates: List of parsed template dicts (must have 'filename' and 'provider_id')

        Returns:
            List of monthly data with 'month', 'added', and 'cumulative' fields
        """
        # Build filename -> providerId mapping from parsed templates
        filename_to_provider = {}
        for t in templates:
            provider_id = t.get('provider_id')
            if provider_id:
                filename_to_provider[t['filename']] = provider_id

        # Track when each file was first seen in git history
        template_first_seen = {}
        for commit in reversed(commits):
            commit_date = commit['date']
            for file in commit['files']:
                if file.endswith('.json') and file not in template_first_seen:
                    template_first_seen[file] = commit_date

        # Determine when each provider was first seen
        provider_first_seen = {}
        for filename, date_str in template_first_seen.items():
            provider_id = filename_to_provider.get(filename)
            if not provider_id:
                continue
            if provider_id not in provider_first_seen or date_str < provider_first_seen[provider_id]:
                provider_first_seen[provider_id] = date_str

        # Group by month
        monthly_additions = defaultdict(int)
        for provider, date_str in provider_first_seen.items():
            year_month = date_str[:7]
            monthly_additions[year_month] += 1

        # Calculate cumulative totals
        sorted_months = sorted(monthly_additions.keys())
        cumulative = 0
        monthly_data = []

        for month in sorted_months:
            cumulative += monthly_additions[month]
            monthly_data.append({
                'month': month,
                'added': monthly_additions[month],
                'cumulative': cumulative
            })

        return monthly_data

    def fetch_all_prs_once(self) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch all PRs from GitHub API once (to avoid multiple API calls).

        Returns:
            Dictionary with 'open' and 'closed' PR lists
        """
        if not self.github_token:
            print("Warning: GITHUB_TOKEN not set. PR data will be limited.")
            return {'open': [], 'closed': []}

        # Get all open PRs
        open_prs = self._get_all_paginated(
            f"/repos/{self.repo_owner}/{self.repo_name}/pulls",
            {"state": "open"}
        )

        # Get all closed PRs (sorted by update time)
        closed_prs = self._get_all_paginated(
            f"/repos/{self.repo_owner}/{self.repo_name}/pulls",
            {"state": "closed", "sort": "updated", "direction": "desc"}
        )

        return {'open': open_prs, 'closed': closed_prs}

    def get_pull_requests(self, all_prs: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        """Process pull request data for display.

        Args:
            all_prs: Pre-fetched dictionary with 'open' and 'closed' PR lists

        Returns:
            List of PR data for display
        """
        if not self.github_token:
            return []

        open_prs = all_prs.get('open', [])
        closed_prs = all_prs.get('closed', [])

        # Filter for merged PRs only (last 10 merged)
        merged_prs = [pr for pr in closed_prs if pr.get("merged_at")][:10]

        # Combine open + recently merged
        combined_prs = open_prs + merged_prs

        # Extract relevant PR information
        pr_data = []
        for pr in combined_prs:
            pr_info = {
                'number': pr['number'],
                'title': pr['title'],
                'state': pr['state'],
                'merged': pr.get('merged_at') is not None,
                'created_at': pr['created_at'],
                'updated_at': pr['updated_at'],
                'merged_at': pr.get('merged_at'),
                'author': pr['user']['login'],
                'author_avatar': pr['user']['avatar_url'],
                'url': pr['html_url'],
                'labels': [label['name'] for label in pr.get('labels', [])],
                'templates': []  # Will be filled by analyzing files
            }

            # Get files changed in this PR
            files = self._github_api_request(
                f"/repos/{self.repo_owner}/{self.repo_name}/pulls/{pr['number']}/files"
            )

            if files:
                for file in files:
                    filename = file['filename']
                    if filename.endswith('.json') and '/' not in filename:
                        # Extract provider and service from filename
                        parts = filename.replace('.json', '').split('.')
                        if len(parts) >= 2:
                            provider_id = parts[0]
                            service_id = '.'.join(parts[1:])

                            # Try to get logo from template content
                            logo_url = None
                            if file['status'] != 'removed':
                                # Try to read current version of file
                                template_path = self.repo_path / filename
                                if template_path.exists():
                                    template_data = self.parse_template(template_path)
                                    if template_data:
                                        logo_url = template_data.get('logoUrl')

                            pr_info['templates'].append({
                                'provider_id': provider_id,
                                'service_id': service_id,
                                'filename': filename,
                                'logo_url': logo_url,
                                'status': file['status']
                            })

            pr_data.append(pr_info)

        return pr_data

    def calculate_pr_activity(self, all_prs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Calculate PR creation and merge statistics by month.

        Args:
            all_prs: Pre-fetched dictionary with 'open' and 'closed' PR lists

        Returns:
            Dictionary with monthly PR activity
        """
        if not self.github_token:
            return {'monthly': [], 'total_merged': 0, 'total_open': 0}

        # Combine all PRs
        combined_prs = all_prs.get('open', []) + all_prs.get('closed', [])

        # Group by month
        monthly_created = defaultdict(int)
        monthly_merged = defaultdict(int)
        total_merged = 0
        total_open = 0

        for pr in combined_prs:
            # Created date
            created_date = pr['created_at'][:7]  # YYYY-MM
            monthly_created[created_date] += 1

            # Merged date
            if pr.get('merged_at'):
                merged_date = pr['merged_at'][:7]  # YYYY-MM
                monthly_merged[merged_date] += 1
                total_merged += 1

            if pr['state'] == 'open':
                total_open += 1

        # Combine data
        all_months = sorted(set(list(monthly_created.keys()) + list(monthly_merged.keys())))
        monthly_data = []

        for month in all_months:
            monthly_data.append({
                'month': month,
                'created': monthly_created[month],
                'merged': monthly_merged[month]
            })

        return {
            'monthly': monthly_data,
            'total_merged': total_merged,
            'total_open': total_open
        }

    def get_contributors(self) -> List[Dict[str, Any]]:
        """Get repository contributors from GitHub API.

        Returns:
            List of contributor data
        """
        if not self.github_token:
            return []

        contributors = self._get_all_paginated(
            f"/repos/{self.repo_owner}/{self.repo_name}/contributors"
        )

        if not contributors:
            return []

        contributor_data = []
        for contrib in contributors:
            contributor_data.append({
                'login': contrib['login'],
                'contributions': contrib['contributions'],
                'avatar_url': contrib['avatar_url'],
                'profile_url': contrib['html_url']
            })

        return contributor_data

    def load_review_cache(self) -> Dict[int, List[Dict[str, Any]]]:
        """Load PR review data from cache file.

        Returns:
            Dictionary mapping PR number to list of reviews
        """
        if not self.cache_file.exists():
            return {}

        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                # Convert string keys back to integers
                return {int(k): v for k, v in cache_data.items()}
        except (json.JSONDecodeError, IOError) as e:
            print(f"    - Warning: Could not load review cache: {e}")
            return {}

    def save_review_cache(self, cache: Dict[int, List[Dict[str, Any]]]):
        """Save PR review data to cache file.

        Args:
            cache: Dictionary mapping PR number to list of reviews
        """
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
            print(f"    - Saved review cache to {self.cache_file.name}")
        except IOError as e:
            print(f"    - Warning: Could not save review cache: {e}")

    def get_top_reviewers(self, all_prs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Get top PR reviewers from GitHub API (with caching).

        Args:
            all_prs: Pre-fetched dictionary with 'open' and 'closed' PR lists

        Returns:
            Dictionary with all-time and last 30 days top reviewers
        """
        if not self.github_token:
            return {'all_time': [], 'last_30_days': []}

        # Filter for merged PRs only from closed PRs
        closed_prs = all_prs.get('closed', [])
        merged_prs = [pr for pr in closed_prs if pr.get("merged_at")]

        print(f"    - Processing {len(merged_prs)} merged PRs for reviewer data...")

        # Load review cache
        review_cache = self.load_review_cache()
        cache_hits = 0
        cache_misses = 0

        # Calculate reviewer statistics
        reviewer_stats_all_time = defaultdict(lambda: {'reviews': 0, 'avatar_url': None, 'profile_url': None})
        reviewer_stats_30_days = defaultdict(lambda: {'reviews': 0, 'avatar_url': None, 'profile_url': None})

        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

        total_prs = len(merged_prs)
        for idx, pr in enumerate(merged_prs, 1):
            pr_number = pr['number']
            merged_at = pr.get('merged_at')

            if not merged_at:
                continue

            # Parse merged_at date
            merged_date = datetime.fromisoformat(merged_at.replace('Z', '+00:00'))
            is_recent = merged_date >= thirty_days_ago

            # Progress log every 50 PRs
            if idx % 50 == 0:
                print(f"      Progress: Processed {idx}/{total_prs} PRs (cache hits: {cache_hits}, misses: {cache_misses})...")

            # Check cache first
            if pr_number in review_cache:
                reviews = review_cache[pr_number]
                cache_hits += 1
            else:
                # Get reviews from API
                reviews = self._github_api_request(
                    f"/repos/{self.repo_owner}/{self.repo_name}/pulls/{pr_number}/reviews"
                )
                # Store in cache
                if reviews is not None:
                    review_cache[pr_number] = reviews
                cache_misses += 1

            if reviews:
                # Track unique reviewers per PR (count each reviewer once per PR)
                reviewers_in_pr = set()
                for review in reviews:
                    # Skip if user is None (deleted users, bots, etc.)
                    if not review.get('user'):
                        continue

                    reviewer = review['user']['login']
                    # Skip if reviewer is the PR author (self-reviews don't count)
                    if reviewer != pr['user']['login']:
                        reviewers_in_pr.add(reviewer)
                        # Store avatar and profile URL
                        if not reviewer_stats_all_time[reviewer]['avatar_url']:
                            reviewer_stats_all_time[reviewer]['avatar_url'] = review['user']['avatar_url']
                            reviewer_stats_all_time[reviewer]['profile_url'] = review['user']['html_url']
                        if is_recent and not reviewer_stats_30_days[reviewer]['avatar_url']:
                            reviewer_stats_30_days[reviewer]['avatar_url'] = review['user']['avatar_url']
                            reviewer_stats_30_days[reviewer]['profile_url'] = review['user']['html_url']

                # Count each unique reviewer once per PR
                for reviewer in reviewers_in_pr:
                    reviewer_stats_all_time[reviewer]['reviews'] += 1
                    if is_recent:
                        reviewer_stats_30_days[reviewer]['reviews'] += 1

        # Sort and get top 5 for all time
        top_all_time = sorted(
            [
                {
                    'login': login,
                    'review_count': stats['reviews'],
                    'avatar_url': stats['avatar_url'],
                    'profile_url': stats['profile_url']
                }
                for login, stats in reviewer_stats_all_time.items()
            ],
            key=lambda x: x['review_count'],
            reverse=True
        )[:5]

        # Sort and get top 5 for last 30 days
        top_30_days = sorted(
            [
                {
                    'login': login,
                    'review_count': stats['reviews'],
                    'avatar_url': stats['avatar_url'],
                    'profile_url': stats['profile_url']
                }
                for login, stats in reviewer_stats_30_days.items()
                if stats['reviews'] > 0  # Only include reviewers with reviews in last 30 days
            ],
            key=lambda x: x['review_count'],
            reverse=True
        )[:5]

        # Save updated cache
        if cache_misses > 0:
            self.save_review_cache(review_cache)

        print(f"    - Review data: {cache_hits} from cache, {cache_misses} from API")

        return {
            'all_time': top_all_time,
            'last_30_days': top_30_days
        }

    def generate_statistics(self) -> Dict[str, Any]:
        """Generate all statistics for the dashboard.

        Returns:
            Complete statistics dictionary
        """
        print("Generating Domain Connect Templates statistics...")

        # Get template files
        print("  - Scanning template files...")
        template_files = self.get_template_files()

        # Parse all templates
        print("  - Parsing templates...")
        templates = []
        providers = set()
        record_type_distribution = defaultdict(int)
        provider_template_count = defaultdict(int)
        provider_meta = {}  # provider_id -> {name, logo_url}
        total_records = 0
        feature_counts = {
            'syncPubKeyDomain': 0,
            'syncRedirectDomain': 0,
            'warnPhishing': 0,
            'hostRequired': 0,
        }

        for file_path in template_files:
            template = self.parse_template(file_path)
            if template:
                templates.append({
                    'filename': file_path.name,
                    'provider_id': template.get('providerId'),
                    'service_id': template.get('serviceId'),
                    'provider_name': template.get('providerName'),
                    'service_name': template.get('serviceName'),
                    'logo_url': template.get('logoUrl'),
                    'record_count': len(template.get('records', []))
                })

                provider_id = template.get('providerId')
                if provider_id:
                    providers.add(provider_id)
                    provider_template_count[provider_id] += 1
                    # Keep first encountered name/logo for each provider
                    if provider_id not in provider_meta:
                        provider_meta[provider_id] = {
                            'name': template.get('providerName', provider_id),
                            'logo_url': template.get('logoUrl')
                        }

                # Count record types (unique per template)
                record_types = self.get_record_types(template)
                for record_type in record_types:
                    record_type_distribution[record_type] += 1

                total_records += len(template.get('records', []))

                # Feature usage
                if template.get('syncPubKeyDomain'):
                    feature_counts['syncPubKeyDomain'] += 1
                if template.get('syncRedirectDomain'):
                    feature_counts['syncRedirectDomain'] += 1
                if template.get('warnPhishing') is True:
                    feature_counts['warnPhishing'] += 1
                if template.get('hostRequired') is True:
                    feature_counts['hostRequired'] += 1

        # Git history analysis
        print("  - Analyzing git history...")
        commits = self.get_git_history()
        growth_data = self.calculate_monthly_growth(commits)
        provider_growth_data = self.calculate_provider_growth(commits, templates)

        # Pull request data - fetch once and reuse
        print("  - Fetching pull request data...")
        all_prs = self.fetch_all_prs_once()
        recent_prs = self.get_pull_requests(all_prs)
        pr_activity = self.calculate_pr_activity(all_prs)

        # Contributor data
        print("  - Fetching contributor data...")
        contributors = self.get_contributors()

        # Top reviewers
        print("  - Fetching top reviewers...")
        top_reviewers = self.get_top_reviewers(all_prs)

        # Top providers
        sorted_providers = sorted(
            provider_template_count.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Last 30 days providers (use providerId from template content)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
        filename_to_provider = {t['filename']: t['provider_id'] for t in templates if t.get('provider_id')}
        recent_providers = defaultdict(int)

        for commit in commits:
            if commit['date'] >= thirty_days_ago:
                for file in commit['files']:
                    provider_id = filename_to_provider.get(file)
                    if provider_id:
                        recent_providers[provider_id] += 1

        sorted_recent_providers = sorted(
            recent_providers.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Compile statistics
        stats = {
            'generated_at': datetime.now().isoformat(),
            'repository': {
                'owner': self.repo_owner,
                'name': self.repo_name,
                'url': f"https://github.com/{self.repo_owner}/{self.repo_name}"
            },
            'summary': {
                'total_templates': len(templates),
                'total_providers': len(providers),
                'total_merged_prs': pr_activity['total_merged'],
                'total_open_prs': pr_activity['total_open'],
                'total_contributors': len(contributors),
                'avg_records_per_template': round(total_records / len(templates), 2) if templates else 0
            },
            'templates_growth': growth_data['monthly'],
            'providers_growth': provider_growth_data,
            'pr_activity': pr_activity['monthly'],
            'record_types': [
                {'type': rec_type, 'count': count}
                for rec_type, count in sorted(
                    record_type_distribution.items(),
                    key=lambda x: x[1],
                    reverse=True
                )
            ],
            'top_providers': {
                'all_time': [
                    {
                        'provider_id': provider,
                        'provider_name': provider_meta.get(provider, {}).get('name', provider),
                        'logo_url': provider_meta.get(provider, {}).get('logo_url'),
                        'template_count': count
                    }
                    for provider, count in sorted_providers[:20]
                ],
                'last_30_days': [
                    {
                        'provider_id': provider,
                        'provider_name': provider_meta.get(provider, {}).get('name', provider),
                        'logo_url': provider_meta.get(provider, {}).get('logo_url'),
                        'template_count': count
                    }
                    for provider, count in sorted_recent_providers[:20]
                ]
            },
            'feature_usage': {
                'total_templates': len(templates),
                'features': [
                    {'name': 'syncPubKeyDomain', 'label': 'syncPubKeyDomain', 'count': feature_counts['syncPubKeyDomain']},
                    {'name': 'syncRedirectDomain', 'label': 'syncRedirectDomain', 'count': feature_counts['syncRedirectDomain']},
                    {'name': 'warnPhishing', 'label': 'warnPhishing', 'count': feature_counts['warnPhishing']},
                    {'name': 'hostRequired', 'label': 'hostRequired', 'count': feature_counts['hostRequired']},
                ]
            },
            'recent_prs': recent_prs,
            'top_reviewers': top_reviewers,
            'templates': templates,
            'contributors': contributors[:50]  # Top 50 contributors
        }

        print(f"  - Statistics generated successfully!")
        print(f"    Total templates: {stats['summary']['total_templates']}")
        print(f"    Total providers: {stats['summary']['total_providers']}")
        print(f"    Total contributors: {stats['summary']['total_contributors']}")

        return stats

    def save_statistics(self, stats: Dict[str, Any], output_path: str = "docs/stats.json"):
        """Save statistics to JSON file.

        Args:
            stats: Statistics dictionary
            output_path: Output file path
        """
        output_file = Path(output_path).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False)

        print(f"  - Statistics saved to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate statistics for Domain Connect Templates repository"
    )
    parser.add_argument("--folder", default="Templates",
                        help="Path to the templates repository folder (default: 'Templates')")
    parser.add_argument("--repo-owner", help="GitHub repository owner (e.g. 'Domain-Connect')")
    parser.add_argument("--repo-name", help="GitHub repository name (e.g. 'Templates')")
    parser.add_argument("--remote", help="Git remote name to use for auto-detection (e.g. 'upstream')")
    args = parser.parse_args()

    if bool(args.repo_owner) != bool(args.repo_name):
        parser.error("--repo-owner and --repo-name must be specified together")

    if args.remote and (args.repo_owner or args.repo_name):
        parser.error("--remote cannot be used together with --repo-owner/--repo-name")

    # Check for GitHub token
    if not os.environ.get("GITHUB_TOKEN"):
        print("Warning: GITHUB_TOKEN environment variable not set.")
        print("Some statistics (PRs, contributors) may be unavailable or incomplete.")
        print()

    # Generate statistics
    generator = StatsGenerator(
        repo_path=args.folder,
        repo_owner=args.repo_owner,
        repo_name=args.repo_name,
        remote=args.remote
    )
    stats = generator.generate_statistics()
    generator.save_statistics(stats)

    print("\nDone! Open docs/index.html to view statistics.")


if __name__ == "__main__":
    main()
