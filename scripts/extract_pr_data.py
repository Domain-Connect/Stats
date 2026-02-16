#!/usr/bin/env python3
"""
Extract detailed PR data including comments and reviews for all templates.

This script fetches:
- PR metadata (number, title, state, author, dates)
- PR description/body
- Issue comments
- Review comments (inline code comments)
- Reviews (approve/request changes/comment with username, state, body)

Outputs:
- pr_data.json: Compact structured JSON
- pr_data.md: Human-readable Markdown
"""

import os
import sys
import json
import requests
from datetime import datetime
from typing import Dict, List, Any, Optional
import argparse
import hashlib
import gzip


# Global cache for closed PR data
CACHE_FILE = 'pr_data_cache.json.gz'
_cache = {}


def load_cache(cache_file: str) -> Dict:
    """Load cache from gzipped JSON file."""
    if os.path.exists(cache_file):
        try:
            with gzip.open(cache_file, 'rt', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Could not load cache: {e}", file=sys.stderr)
    return {}


def save_cache(cache: Dict, cache_file: str) -> None:
    """Save cache to gzipped JSON file."""
    try:
        os.makedirs(os.path.dirname(cache_file) if os.path.dirname(cache_file) else '.', exist_ok=True)
        with gzip.open(cache_file, 'wt', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, separators=(',', ':'))
    except Exception as e:
        print(f"Warning: Could not save cache: {e}", file=sys.stderr)


def get_cache_key(repo_owner: str, repo_name: str, pr_number: int, data_type: str) -> str:
    """Generate cache key for PR data."""
    return f"{repo_owner}/{repo_name}/PR{pr_number}/{data_type}"


def get_github_token() -> str:
    """Get GitHub token from environment variable."""
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Error: GITHUB_TOKEN environment variable not set", file=sys.stderr)
        sys.exit(1)
    return token


def github_api_request(url: str, token: str, params: Optional[Dict] = None) -> Any:
    """Make authenticated GitHub API request with pagination support."""
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }

    all_data = []
    page = 1

    while True:
        page_params = {**(params or {}), 'page': page, 'per_page': 100}
        response = requests.get(url, headers=headers, params=page_params)

        if response.status_code != 200:
            print(f"Warning: API request failed: {url} - {response.status_code}", file=sys.stderr)
            print(f"Response: {response.text[:200]}", file=sys.stderr)
            return all_data if all_data else None

        data = response.json()

        # Handle single object vs list
        if isinstance(data, list):
            if not data:
                break
            all_data.extend(data)
            page += 1
        else:
            return data

    return all_data


def get_pr_files(repo_owner: str, repo_name: str, pr_number: int, token: str) -> List[str]:
    """Get list of files changed in a PR."""
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/files'
    files_data = github_api_request(url, token)

    if not files_data:
        return []

    return [f['filename'] for f in files_data]


def get_pr_comments(repo_owner: str, repo_name: str, pr_number: int, token: str) -> List[Dict]:
    """Get issue comments (general PR comments)."""
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/issues/{pr_number}/comments'
    comments_data = github_api_request(url, token)

    if not comments_data:
        return []

    return [
        {
            'type': 'comment',
            'author': c['user']['login'] if c['user'] else '[deleted/robot]',
            'body': c['body'],
            'created_at': c['created_at'],
            'updated_at': c['updated_at']
        }
        for c in comments_data
    ]


def get_pr_review_comments(repo_owner: str, repo_name: str, pr_number: int, token: str) -> List[Dict]:
    """Get review comments (inline code comments)."""
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/comments'
    comments_data = github_api_request(url, token)

    if not comments_data:
        return []

    return [
        {
            'type': 'review_comment',
            'author': c['user']['login'] if c['user'] else '[deleted/robot]',
            'body': c['body'],
            'path': c['path'],
            'line': c.get('line') or c.get('original_line'),
            'created_at': c['created_at'],
            'updated_at': c['updated_at'],
            'pull_request_review_id': c.get('pull_request_review_id')  # Link to parent review
        }
        for c in comments_data
    ]


def get_pr_reviews(repo_owner: str, repo_name: str, pr_number: int, token: str) -> List[Dict]:
    """Get PR reviews (approve/request changes/comment)."""
    url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/pulls/{pr_number}/reviews'
    reviews_data = github_api_request(url, token)

    if not reviews_data:
        return []

    return [
        {
            'type': 'review',
            'id': r['id'],  # Review ID for correlation with review comments
            'author': r['user']['login'] if r['user'] else '[deleted/robot]',
            'state': r['state'],  # APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED
            'body': r['body'] or '',
            'submitted_at': r.get('submitted_at', r.get('created_at', ''))
        }
        for r in reviews_data
    ]


def get_all_prs(repo_owner: str, repo_name: str, token: str) -> List[Dict]:
    """Get all PRs from the repository."""
    print("Fetching all PRs...")

    # Get closed PRs (includes merged)
    closed_url = f'https://api.github.com/repos/{repo_owner}/{repo_name}/pulls'
    closed_prs = github_api_request(closed_url, token, {'state': 'closed'})

    # Get open PRs
    open_prs = github_api_request(closed_url, token, {'state': 'open'})

    all_prs = (closed_prs or []) + (open_prs or [])
#    all_prs = all_prs[0:5]
    print(f"Found {len(all_prs)} total PRs")

    return all_prs


def extract_pr_data(repo_owner: str, repo_name: str, token: str, cache: Dict, cache_file: str) -> Dict[str, Any]:
    """Extract all PR data including comments and reviews."""
    all_prs = get_all_prs(repo_owner, repo_name, token)

    pr_data = {
        'repository': f'{repo_owner}/{repo_name}',
        'extracted_at': datetime.utcnow().isoformat() + 'Z',
        'total_prs': len(all_prs),
        'pull_requests': []
    }

    cache_hits = 0
    cache_misses = 0
    new_cached_items = 0
    CACHE_SAVE_INTERVAL = 10  # Save cache every 10 new items

    for idx, pr in enumerate(all_prs, 1):
        pr_number = pr['number']
        is_closed = pr['state'] == 'closed'

        # Check cache for closed PRs
        if is_closed:
            cache_key = get_cache_key(repo_owner, repo_name, pr_number, 'full')
            if cache_key in cache:
                print(f"Processing PR #{pr_number} ({idx}/{len(all_prs)}) - [CACHED]")
                pr_data['pull_requests'].append(cache[cache_key])
                cache_hits += 1
                continue

        print(f"Processing PR #{pr_number} ({idx}/{len(all_prs)})...")
        cache_misses += 1

        # Get files changed
        files = get_pr_files(repo_owner, repo_name, pr_number, token)

        # Get comments
        comments = get_pr_comments(repo_owner, repo_name, pr_number, token)

        # Get review comments
        review_comments = get_pr_review_comments(repo_owner, repo_name, pr_number, token)

        # Get reviews
        reviews = get_pr_reviews(repo_owner, repo_name, pr_number, token)

        pr_info = {
            'number': pr_number,
            'title': pr['title'],
            'state': pr['state'],
            'author': pr['user']['login'] if pr['user'] else '[deleted/robot]',
            'created_at': pr['created_at'],
            'updated_at': pr['updated_at'],
            'closed_at': pr['closed_at'],
            'merged_at': pr.get('merged_at'),
            'body': pr['body'] or '',
            'labels': [label['name'] for label in pr['labels']],
            'files': files,
            'comments': comments,
            'review_comments': review_comments,
            'reviews': reviews,
            'url': pr['html_url']
        }

        pr_data['pull_requests'].append(pr_info)

        # Cache closed PRs
        if is_closed:
            cache_key = get_cache_key(repo_owner, repo_name, pr_number, 'full')
            cache[cache_key] = pr_info
            new_cached_items += 1

            # Periodically save cache to avoid data loss
            if new_cached_items % CACHE_SAVE_INTERVAL == 0:
                print(f"  💾 Auto-saving cache ({len(cache)} entries)...")
                save_cache(cache, cache_file)

    print(f"\nCache statistics: {cache_hits} hits, {cache_misses} misses, {new_cached_items} new items")

    return pr_data


def save_json(data: Dict[str, Any], output_path: str) -> None:
    """Save data as compact JSON."""
    # Ensure directory exists
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Saved JSON to {output_path}")


def escape_markdown_headings(text: str) -> str:
    """Escape markdown heading symbols in user content to prevent structure interference."""
    if not text:
        return text
    # Replace # at start of lines with ▸ (visual indicator without markdown meaning)
    lines = text.split('\n')
    escaped_lines = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith('#'):
            # Count leading spaces
            leading_spaces = len(line) - len(stripped)
            # Replace # with ▸
            escaped = ' ' * leading_spaces + '▸' + stripped[1:]
            escaped_lines.append(escaped)
        else:
            escaped_lines.append(line)
    
    # Check unclosed code blocks
    ret = '\n'.join(escaped_lines)
    if ret.count('```') % 2 != 0:
        ret = f"{ret}\n```"
    
    return ret


def write_pr_markdown(f, pr: Dict) -> None:
    """Write markdown content for a single PR to a file handle."""
    # PR Header
    f.write(f"## PR #{pr['number']}: {pr['title']}\n\n")
    f.write(f"**Author:** @{pr['author']}  \n")
    f.write(f"**State:** {pr['state'].upper()}  \n")
    f.write(f"**Created:** {pr['created_at']}  \n")

    if pr['merged_at']:
        f.write(f"**Merged:** {pr['merged_at']}  \n")
    elif pr['closed_at']:
        f.write(f"**Closed:** {pr['closed_at']}  \n")

    if pr['labels']:
        f.write(f"**Labels:** {', '.join(pr['labels'])}  \n")

    f.write(f"**URL:** {pr['url']}\n\n")

    # Files Changed
    if pr['files']:
        f.write(f"**Files Changed ({len(pr['files'])}):**\n")
        for file in pr['files'][:10]:  # Limit to first 10
            f.write(f"- `{file}`\n")
        if len(pr['files']) > 10:
            f.write(f"- _(and {len(pr['files']) - 10} more)_\n")
        f.write("\n")

    # PR Description
    if pr['body']:
        f.write("### Description\n\n")
        f.write(f"{escape_markdown_headings(pr['body'])}\n\n")

    # Reviews with their associated review comments
    if pr['reviews']:
        # Build a mapping of review_id -> list of review comments
        review_comments_map = {}
        for comment in pr['review_comments']:
            review_id = comment.get('pull_request_review_id')
            if review_id:
                if review_id not in review_comments_map:
                    review_comments_map[review_id] = []
                review_comments_map[review_id].append(comment)

        # Count review comments that aren't associated with any review
        orphaned_comments = [c for c in pr['review_comments'] if not c.get('pull_request_review_id')]

        f.write(f"### Reviews ({len(pr['reviews'])})\n\n")
        for review in pr['reviews']:
            state_emoji = {
                'APPROVED': '✅',
                'CHANGES_REQUESTED': '❌',
                'COMMENTED': '💬',
                'DISMISSED': '🚫'
            }.get(review['state'], '❓')

            f.write(f"**{state_emoji} @{review['author']}** ({review['state']}) - {review['submitted_at']}\n")
            if review['body']:
                escaped_body = escape_markdown_headings(review['body']).replace('\n', '\n> ')
                f.write(f"> {escaped_body}\n")

            # Add associated review comments
            review_id = review.get('id')
            if review_id and review_id in review_comments_map:
                comments = review_comments_map[review_id]
                f.write(f"\n**Review Comments ({len(comments)}):**\n\n")
                for comment in comments:
                    f.write(f"  - `{comment['path']}:{comment['line']}` - @{comment['author']}\n")
                    escaped_body = escape_markdown_headings(comment['body']).replace('\n', '\n    > ')
                    f.write(f"    > {escaped_body}\n")

            f.write("\n")

        # Display orphaned review comments (not associated with any review)
        if orphaned_comments:
            f.write(f"### Orphaned Review Comments ({len(orphaned_comments)})\n\n")
            f.write("_These review comments are not associated with any review:_\n\n")
            for comment in orphaned_comments:
                f.write(f"**@{comment['author']}** on `{comment['path']}:{comment['line']}` - {comment['created_at']}\n")
                escaped_body = escape_markdown_headings(comment['body']).replace('\n', '\n> ')
                f.write(f"> {escaped_body}\n\n")

    # Comments (general PR discussion)
    if pr['comments']:
        f.write(f"### Comments ({len(pr['comments'])})\n\n")
        for comment in pr['comments']:
            f.write(f"**@{comment['author']}** - {comment['created_at']}\n")
            escaped_body = escape_markdown_headings(comment['body']).replace('\n', '\n> ')
            f.write(f"> {escaped_body}\n\n")


def save_markdown(data: Dict[str, Any], output_path: str) -> None:
    """Save all PR data as a single Markdown file."""
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(f"# Pull Request Data: {data['repository']}\n\n")
        f.write(f"**Extracted:** {data['extracted_at']}  \n")
        f.write(f"**Total PRs:** {data['total_prs']}\n\n")
        f.write("---\n\n")

        for pr in data['pull_requests']:
            write_pr_markdown(f, pr)
            f.write("---\n\n")

    print(f"Saved Markdown to {output_path}")


def save_markdown_split(data: Dict[str, Any], split_dir: str) -> None:
    """Save each PR as a separate Markdown file in the given directory."""
    os.makedirs(split_dir, exist_ok=True)

    for pr in data['pull_requests']:
        filename = f"PR-{pr['number']:04d}.md"
        filepath = os.path.join(split_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# PR #{pr['number']}: {pr['title']}\n\n")
            f.write(f"_Repository: {data['repository']} | Extracted: {data['extracted_at']}_\n\n")
            write_pr_markdown(f, pr)

    print(f"Saved {len(data['pull_requests'])} PR files to {split_dir}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract PR data including comments and reviews for all templates'
    )
    parser.add_argument(
        '--repo-owner',
        required=True,
        help='GitHub repository owner (required)'
    )
    parser.add_argument(
        '--repo-name',
        required=True,
        help='GitHub repository name (required)'
    )
    parser.add_argument(
        '--output-dir',
        required=True,
        help='Output directory for JSON and Markdown files (required)'
    )
    parser.add_argument(
        '--cache-file',
        default='pr_data_cache.json.gz',
        help='Cache file path for closed PR data (default: pr_data_cache.json.gz, gzip compressed)'
    )
    parser.add_argument(
        '--split-md',
        metavar='SUBFOLDER',
        help='Save individual PR markdown files in output-dir/SUBFOLDER/'
    )

    args = parser.parse_args()

    # Get GitHub token
    token = get_github_token()

    # Set repo info
    repo_owner = args.repo_owner
    repo_name = args.repo_name

    # Create output file paths
    output_json = os.path.join(args.output_dir, 'pr_data.json')
    output_md = os.path.join(args.output_dir, 'pr_data.md')
    cache_file = args.cache_file

    print(f"Repository: {repo_owner}/{repo_name}")
    print(f"Output directory: {args.output_dir}")
    print(f"Output JSON: {output_json}")
    print(f"Output Markdown: {output_md}")
    print(f"Cache file: {cache_file}")
    print()

    # Load cache
    print("Loading cache...")
    cache = load_cache(cache_file)
    print(f"Loaded {len(cache)} cached entries")
    print()

    # Extract PR data
    pr_data = extract_pr_data(repo_owner, repo_name, token, cache, cache_file)

    # Final cache save
    print("\nFinal cache save...")
    save_cache(cache, cache_file)
    print(f"Cached {len(cache)} closed PR entries")

    # Save outputs
    save_json(pr_data, output_json)
    save_markdown(pr_data, output_md)

    if args.split_md:
        split_dir = os.path.join(args.output_dir, args.split_md)
        save_markdown_split(pr_data, split_dir)

    print("\n✅ Extraction complete!")
    print(f"Total PRs: {pr_data['total_prs']}")
    print(f"Total comments: {sum(len(pr['comments']) for pr in pr_data['pull_requests'])}")
    print(f"Total review comments: {sum(len(pr['review_comments']) for pr in pr_data['pull_requests'])}")
    print(f"Total reviews: {sum(len(pr['reviews']) for pr in pr_data['pull_requests'])}")


if __name__ == '__main__':
    main()
