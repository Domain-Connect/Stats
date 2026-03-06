# Project Memory

## Cache Strategy for pr_reviews_cache.json

The cache file (`scripts/pr_reviews_cache.json`) stores per-PR data keyed by PR number (int).
Each entry can contain multiple fields (currently `reviews`, and in future `label_events`, etc.).

**Critical rule when adding a new cached field:**
- If the new field is absent from ALL cached entries → this is a first run for this feature.
  Fetch the new data for ALL PRs (both cached and new ones from the API).
- If the new field is absent from SOME cached entries → fetch only for those missing entries.
- Never re-fetch fields that are already cached — preserve existing cache data.

This avoids redundant API calls while ensuring backward compatibility when new stats features
are added. Always extend the existing cache dict per entry rather than replacing it.
