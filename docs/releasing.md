# Releasing

This project uses [release-please](https://github.com/googleapis/release-please) to automate releases. Merging a release PR is the only manual step.

## How It Works

```
merge PRs with conventional commits
         │
         ▼
release-please creates/updates a Release PR
  (version bump in pyproject.toml + CHANGELOG.md)
         │
         ▼
merge the Release PR  ◄── this is the "one click"
         │
         ▼
release-please creates git tag + GitHub Release
         │
         ▼
release.yml triggers: test → Docker build → push to GHCR
```

## Day-to-Day: Writing Commits

Use [conventional commit](https://www.conventionalcommits.org/) prefixes in PR titles. When you squash-merge, the PR title becomes the commit message.

| Prefix | Version bump | Example |
|--------|-------------|---------|
| `feat:` | minor (0.0.x → 0.1.0) | `feat: add corridor classifier` |
| `fix:` | patch (0.0.1 → 0.0.2) | `fix: pipeline crash on null timestamps` |
| `feat!:` or `fix!:` | major (0.x.x → 1.0.0) | `feat!: redesign API response format` |
| `docs:`, `chore:`, `ci:`, `refactor:`, `test:` | none (no release) | `docs: update setup guide` |

PR titles are validated by the `lint-pr.yml` workflow — it will fail if the title doesn't follow the convention.

> **Pre-release note:** While on `0.0.x`, `feat:` bumps patch (not minor) thanks to `bump-patch-for-minor-pre-major` in `release-please-config.json`.

## Releasing

1. Merge feature/fix PRs to `main` as usual
2. release-please automatically opens (or updates) a **Release PR** titled "chore(main): release X.Y.Z"
3. Review the PR — it contains the version bump and generated changelog
4. **Merge the Release PR** — this is the one click
5. release-please creates the `vX.Y.Z` tag and GitHub Release
6. The tag triggers `release.yml` which builds and pushes the Docker image

## Docker Image Tags

Each release produces these tags on `ghcr.io/jflammia/commutetracker`:

| Tag | Example | Meaning |
|-----|---------|---------|
| `X.Y.Z` | `0.0.2` | Pinned to exact version (recommended for production) |
| `X.Y` | `0.0` | Floats to latest patch in this minor |
| `latest` | `latest` | Most recent release |

## Where Version Lives

| Location | Updated by |
|----------|-----------|
| `pyproject.toml` | release-please (automatic) |
| `.release-please-manifest.json` | release-please (automatic) |
| `CHANGELOG.md` | release-please (automatic) |
| Git tag (`vX.Y.Z`) | release-please (automatic) |
| FastAPI OpenAPI docs | Reads from `pyproject.toml` at runtime |
| Docker `APP_VERSION` | Injected by CI from git tag |

## Manual Release (Escape Hatch)

If you need to release without release-please:

```bash
# Via GitHub Actions UI
# Go to Actions → Release → Run workflow → enter version

# Via CLI
gh workflow run release.yml -f version=0.0.2
```

## CI Workflows

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `ci.yml` | Push/PR to main | Lint + test + Docker build (no push) |
| `lint-pr.yml` | PR opened/edited | Validates conventional commit in PR title |
| `release-please.yml` | Push to main | Creates/updates Release PR with changelog + version bump |
| `release.yml` | `v*` tag push or manual | Test → Docker multi-arch build → push to GHCR |

## Upgrading on Your Server

```bash
docker compose pull
docker compose up -d
```

## GitHub Repo Settings

For this flow to work correctly:

1. **Actions permissions**: Settings → Actions → General → enable "Allow GitHub Actions to create and approve pull requests"
2. **Merge strategy**: Settings → General → Pull Requests → check "Allow squash merging" and set "Default to PR title for squash merge commits"
