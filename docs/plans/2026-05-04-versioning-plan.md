# Versioning & Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up git-cliff and a manual `workflow_dispatch` release workflow so cutting a release is one button click in GitHub Actions.

**Architecture:** Three file changes: a new `cliff.toml` at the repo root configures git-cliff's changelog output; a new `release.yml` workflow determines the next semver version, commits `CHANGELOG.md`, pushes the tag, and creates a GitHub Release; a small update to `publish-docker.yml` adds an optional `image_tag` input for standalone dispatch. The existing publish workflow already fires on `v*` tag pushes — no changes needed to that trigger.

**Tech Stack:** git-cliff (changelog generation + version bumping), `orhun/git-cliff-action@v4` (GitHub Actions wrapper), GitHub CLI (`gh`), conventional commits

---

## File Map

| Action | Path |
|---|---|
| Create | `cliff.toml` |
| Create | `.github/workflows/release.yml` |
| Modify | `.github/workflows/publish-docker.yml` |

---

## Task 1: Create `cliff.toml`

This file tells git-cliff how to parse commits and format the changelog. It lives at the repo root so git-cliff finds it by default.

**Files:**
- Create: `cliff.toml`

- [ ] **Step 1: Write cliff.toml**

Create `cliff.toml` at the repo root with this exact content:

```toml
[changelog]
header = "# Changelog\n\n"
body = """
{% if version %}\
## [{{ version | trim_start_matches(pat="v") }}] - {{ timestamp | date(format="%Y-%m-%d") }}
{% else %}\
## [Unreleased]
{% endif %}\
{% for group, commits in commits | group_by(attribute="group") %}
### {{ group | upper_first }}

{% for commit in commits %}\
- {% if commit.breaking %}**Breaking:** {% endif %}{{ commit.message | upper_first }} \
([`{{ commit.id | truncate(length=7, end="") }}`]\
(https://github.com/zen-strayer/kanchi/commit/{{ commit.id }}))
{% endfor %}
{% endfor %}
"""
trim = true
footer = ""

[git]
conventional_commits = true
filter_unconventional = true
protect_breaking_commits = true
commit_parsers = [
  { message = "^feat", group = "Features" },
  { message = "^fix", group = "Bug Fixes" },
  { message = "^chore|^ci|^docs|^refactor|^test|^build", skip = true },
]
filter_commits = true
tag_pattern = "v[0-9].*"
```

Key decisions:
- `protect_breaking_commits = true` — breaking changes surface in the changelog even if their commit type (e.g. `chore!`) would otherwise be skipped
- `filter_unconventional = true` — merge commits and non-conventional commits are dropped
- The body template emits `**Breaking:**` prefix for breaking commits

- [ ] **Step 2: Validate cliff.toml locally**

Install git-cliff if not present:
```bash
brew install git-cliff
```

Run git-cliff against the current commit history to verify the config parses correctly and produces output:
```bash
git cliff --config cliff.toml --unreleased --bump --strip header
```

Expected: a formatted changelog section listing `feat` and `fix` commits grouped under **Features** and **Bug Fixes**. `chore`/`ci` commits should not appear.

If the repo has no `feat` or `fix` commits since the last tag (or no tags at all with only `chore` commits), you'll see an error like `fatal: No bumpable commit found`. That's expected behavior — it means you'd need to pass `--tag v0.1.0` explicitly. Verify with:
```bash
git cliff --config cliff.toml --tag v0.1.0 --strip header
```

Expected: same formatted output, but with `v0.1.0` as the version header.

- [ ] **Step 3: Commit**

```bash
git add cliff.toml
git commit -m "chore(ci): add git-cliff config for changelog generation [NOJIRA]"
```

---

## Task 2: Update `publish-docker.yml` with `image_tag` input

The workflow currently has a bare `workflow_dispatch:` trigger with no inputs. If you run it manually today, the metadata action has no semver tag to parse and can only produce `latest`. Adding an `image_tag` input lets you specify a tag when running standalone (e.g. rebuilding after a base image CVE patch).

**Files:**
- Modify: `.github/workflows/publish-docker.yml`

- [ ] **Step 1: Add workflow_dispatch inputs and conditional tag**

Replace the existing `on:` block and `metadata` step tags in `.github/workflows/publish-docker.yml`:

```yaml
on:
  push:
    tags:
      - 'v*'
  workflow_dispatch:
    inputs:
      image_tag:
        description: 'Optional image tag override (e.g. "edge", "1.2.3"). Falls back to "latest" if omitted.'
        required: false
        type: string
```

Update the `Extract metadata` step's `tags` block to add the conditional raw tag at the end:

```yaml
      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}
            type=raw,value=latest
            type=raw,value=${{ inputs.image_tag }},enable=${{ inputs.image_tag != '' }}
```

The `enable` condition evaluates to `false` when triggered by a tag push (inputs are empty) and `false` when `workflow_dispatch` is triggered without providing `image_tag`. It only adds the raw tag when you explicitly set it.

- [ ] **Step 2: Validate with actionlint**

Install actionlint if not present:
```bash
brew install actionlint
```

Run:
```bash
actionlint .github/workflows/publish-docker.yml
```

Expected: no output (clean exit).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish-docker.yml
git commit -m "chore(ci): add image_tag input to publish-docker workflow_dispatch [NOJIRA]"
```

---

## Task 3: Create `release.yml`

This is the main new workflow. It runs only on manual dispatch, determines the next version, generates the changelog, commits `CHANGELOG.md`, tags the release, and creates a GitHub Release. The tag push then automatically triggers `publish-docker.yml`.

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release.yml` with this exact content:

```yaml
name: Release

on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version override (e.g. "1.2.3"). Required for the first release. Leave empty to auto-detect from commits.'
        required: false
        type: string
      prerelease:
        description: 'Mark as pre-release'
        required: false
        type: boolean
        default: false
      draft:
        description: 'Create as draft (review before publishing)'
        required: false
        type: boolean
        default: false

jobs:
  release:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Generate release body
        id: cliff
        uses: orhun/git-cliff-action@v4
        with:
          config: cliff.toml
          args: >-
            --unreleased
            --strip header
            ${{ inputs.version != '' && format('--tag v{0}', inputs.version) || '--bump' }}
        env:
          OUTPUT: /tmp/release-body.md
          GITHUB_REPO: ${{ github.repository }}

      - name: Resolve version
        id: version
        run: |
          if [ -n "${{ inputs.version }}" ]; then
            echo "value=${{ inputs.version }}" >> "$GITHUB_OUTPUT"
          elif [ -n "${{ steps.cliff.outputs.version }}" ]; then
            echo "value=${{ steps.cliff.outputs.version }}" >> "$GITHUB_OUTPUT"
          else
            echo "::error::No bumpable commits found since last tag (only chore/ci commits). Set the 'version' input to force a release."
            exit 1
          fi

      - name: Update CHANGELOG.md
        uses: orhun/git-cliff-action@v4
        with:
          config: cliff.toml
          args: --tag v${{ steps.version.outputs.value }}
        env:
          OUTPUT: CHANGELOG.md
          GITHUB_REPO: ${{ github.repository }}

      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add CHANGELOG.md
          git commit -m "chore(release): v${{ steps.version.outputs.value }} [NOJIRA]"
          git tag "v${{ steps.version.outputs.value }}"
          git push origin main --follow-tags

      - name: Create GitHub Release
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          ARGS=""
          [ "${{ inputs.prerelease }}" = "true" ] && ARGS="$ARGS --prerelease"
          [ "${{ inputs.draft }}" = "true" ] && ARGS="$ARGS --draft"
          gh release create "v${{ steps.version.outputs.value }}" \
            --title "v${{ steps.version.outputs.value }}" \
            --notes-file /tmp/release-body.md \
            $ARGS
```

How the version logic works:

| Scenario | `inputs.version` | `--bump` / `--tag` used | `steps.version.outputs.value` |
|---|---|---|---|
| First release | `"0.1.0"` | `--tag v0.1.0` | `"0.1.0"` (from input) |
| Auto-detect feat | *(empty)* | `--bump` | `"0.2.0"` (from cliff output) |
| Auto-detect fix | *(empty)* | `--bump` | `"0.1.1"` (from cliff output) |
| Chore-only, no input | *(empty)* | `--bump` (errors) | workflow fails with clear message |
| Chore-only, forced | `"0.2.0"` | `--tag v0.2.0` | `"0.2.0"` (from input) |

- [ ] **Step 2: Validate with actionlint**

```bash
actionlint .github/workflows/release.yml
```

Expected: no output (clean exit).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "chore(ci): add manual release workflow with git-cliff versioning [NOJIRA]"
```

---

## Task 4: End-to-end validation

Verify the full flow works by triggering a draft release.

**Prerequisites:** All three tasks above committed and pushed to `main`.

- [ ] **Step 1: Push to main**

```bash
git push origin main
```

- [ ] **Step 2: Trigger a draft release**

Go to **GitHub Actions → Release → Run workflow**.

Set inputs:
- `version`: `0.1.0`
- `prerelease`: unchecked
- `draft`: **checked** ✓

Click **Run workflow**.

- [ ] **Step 3: Verify CHANGELOG.md commit**

Once the workflow completes, confirm on GitHub that a new commit appears on `main`:
```
chore(release): v0.1.0 [NOJIRA]
```

The commit should contain an updated `CHANGELOG.md` grouped by **Features** and **Bug Fixes**, with no `chore`/`ci` commits listed.

- [ ] **Step 4: Verify draft GitHub Release**

Go to **GitHub → Releases**. A draft release `v0.1.0` should be present with:
- The same changelog content as `CHANGELOG.md` (scoped to this version only, no header)
- Marked as draft

Review the release notes. If they look correct, publish the draft release from the GitHub UI — this does NOT re-trigger the publish workflow (publishing a draft doesn't push a new tag). The tag `v0.1.0` was already pushed in step 3, which triggered `publish-docker.yml`.

- [ ] **Step 5: Verify Docker image in GHCR**

Go to **GitHub → Packages** (or `ghcr.io/zen-strayer/kanchi`). Confirm these four tags exist:
- `0.1.0`
- `0.1`
- `0`  ← `type=semver,pattern={{major}}` with `v0.1.0` produces `0`, not `1`
- `latest`

- [ ] **Step 6: Verify standalone publish-docker dispatch**

Go to **GitHub Actions → Build and Publish Docker Image → Run workflow**.

Set `image_tag` to `edge`. Run the workflow.

Once complete, verify `ghcr.io/zen-strayer/kanchi:edge` appears in Packages alongside the semver tags. The `latest` tag should still point to `v0.1.0`.
