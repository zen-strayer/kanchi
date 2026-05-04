# Versioning & Release Pipeline Design

**Date:** 2026-05-04
**Status:** Approved

## Overview

Bake automated versioning into the release and publish pipeline using **git-cliff** and a manual `workflow_dispatch` trigger. The human decides when to cut a release; tooling handles version determination, changelog generation, tagging, GitHub Release creation, and Docker image publishing.

## Goals

- Automatic semver version bumping derived from conventional commits
- `CHANGELOG.md` maintained in the repo and mirrored as GitHub Release notes
- Human remains in full control — nothing releases without an explicit trigger
- No new credentials, bot accounts, or external services required
- Chains cleanly with the existing `publish-docker.yml` workflow

## Release Flow

```
developer triggers "Run workflow" on release.yml
        ↓
git-cliff reads all commits since last tag
  → determines next semver bump:
      feat        → minor  (0.1.0 → 0.2.0)
      fix         → patch  (0.1.0 → 0.1.1)
      feat! / fix! → major (0.1.0 → 1.0.0)
  → generates changelog content for this release
        ↓
workflow commits updated CHANGELOG.md to main
  commit message: "chore(release): vX.Y.Z [NOJIRA]"
        ↓
workflow creates and pushes tag vX.Y.Z
        ↓
workflow creates GitHub Release
  → body = git-cliff output scoped to this version only
  → respects --prerelease and --draft flags
        ↓
publish-docker.yml triggers automatically (listens for v* tags)
  → builds linux/amd64 + linux/arm64 image
  → pushes to ghcr.io/zen-strayer/kanchi with semver tags
```

## New Workflow: `release.yml`

### Trigger

`workflow_dispatch` only — no automatic triggers.

### Inputs

| Input | Type | Default | Description |
|---|---|---|---|
| `version` | string | *(empty)* | Override the auto-detected version. Required for the first release (`0.1.0`). Also use to force a major bump when only `chore` commits have landed. |
| `prerelease` | boolean | `false` | Mark the GitHub Release as pre-release. Produces tags like `v1.2.3-rc.1` when combined with a version override. |
| `draft` | boolean | `false` | Create the GitHub Release as a draft for review before publishing. Recommended for first use. |

### Permissions

```yaml
permissions:
  contents: write   # commit CHANGELOG.md, push tag, create GitHub Release
```

### Steps

1. **Checkout** — full history (`fetch-depth: 0`) so git-cliff can read all commits
2. **Install git-cliff** — via the `orhun/git-cliff-action` or direct binary download
3. **Determine version** — use `version` input if provided; otherwise run `git cliff --bumped-version`
4. **Generate changelog** — run git-cliff, scoped to unreleased commits, output to `CHANGELOG.md` (full file) and a temp file for the release body (current version only)
5. **Commit `CHANGELOG.md`** — `git commit -m "chore(release): vX.Y.Z [NOJIRA]"`
6. **Push commit + tag** — `git tag vX.Y.Z && git push origin main --tags`
7. **Create GitHub Release** — `gh release create vX.Y.Z --notes-file <temp> [--prerelease] [--draft]`

## Changes to `publish-docker.yml`

Add an `image_tag` input to the existing `workflow_dispatch` trigger to support standalone image builds (e.g., rebuilding after a base image CVE patch, pushing a branch image for testing):

```yaml
workflow_dispatch:
  inputs:
    image_tag:
      description: 'Optional image tag override (e.g. "edge", "1.2.3"). Falls back to "latest" if omitted.'
      required: false
      type: string
```

When `image_tag` is provided, pass it as an additional `type=raw` entry to `docker/metadata-action`.

## New Config: `cliff.toml`

Placed at the repo root. Controls changelog grouping and filtering.

```toml
[changelog]
header = "# Changelog\n\n"
body = """
{% for group, commits in commits | group_by(attribute="group") %}
### {{ group | upper_first }}\n
{% for commit in commits %}
- {{ commit.message | upper_first }} ([{{ commit.id | truncate(length=7, end="") }}](https://github.com/zen-strayer/kanchi/commit/{{ commit.id }}))
{% endfor %}
{% endfor %}
"""
trim = true

[git]
conventional_commits = true
filter_unconventional = true
commit_parsers = [
  { message = "^feat", group = "Features" },
  { message = "^fix", group = "Bug Fixes" },
  { message = "^chore|^ci|^docs|^refactor|^test|^build", skip = true },
]
filter_commits = true
tag_pattern = "v[0-9].*"
```

`chore`, `ci`, `docs`, `refactor`, `test`, and `build` commits are excluded from release notes. Breaking changes (`!`) are always surfaced regardless of type.

## Edge Cases

### First release
No prior tags exist. git-cliff's `--bumped-version` will error with nothing to bump from. **The `version` input must be set explicitly for the first run** — use `0.1.0`. The changelog will include all commits to date.

### Chore-only periods
If only `chore`/`ci` commits have landed since the last tag, `--bumped-version` finds no bumpable commit and errors. In this case the `version` input must be provided explicitly. The workflow should fail fast with a clear error message if neither a bumpable commit nor a `version` input is present.

### Mid-release failure
If the workflow commits `CHANGELOG.md` and pushes the tag but the GitHub Release step fails, the Docker publish will still fire (tag already exists). The GitHub Release can be created manually via:
```bash
gh release create vX.Y.Z --notes-file <changelog-excerpt>
```
No data is lost — git-cliff is deterministic.

### Duplicate tag protection
`git push --tags` will fail if the tag already exists, preventing accidental double-releases. This is the desired behavior.

## Verification Checklist

- [ ] First run: use `draft: true` and `version: 0.1.0` — inspect generated release notes before publishing
- [ ] Confirm `CHANGELOG.md` commit appears on `main` after the run
- [ ] Confirm Docker image lands in GHCR with correct semver tags (`1.2.3`, `1.2`, `1`, `latest`)
- [ ] Confirm `publish-docker.yml` standalone dispatch works with an `image_tag` input
- [ ] Confirm chore-only scenario errors clearly when no `version` input is provided
