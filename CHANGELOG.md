# Changelog

## [0.0.7](https://github.com/jflammia/commuteTracker/compare/v0.0.6...v0.0.7) (2026-03-28)


### Features

* **dashboard:** add healthcheck for Streamlit container ([#14](https://github.com/jflammia/commuteTracker/issues/14)) ([af1e09f](https://github.com/jflammia/commuteTracker/commit/af1e09fe151d89640e95a6fdd115695c9507c70a))

## [0.0.6](https://github.com/jflammia/commuteTracker/compare/v0.0.5...v0.0.6) (2026-03-28)


### Features

* **dashboard:** show build version and git commit in sidebar ([dea475d](https://github.com/jflammia/commuteTracker/commit/dea475de3ea67fc9e4b2caa83571955a5a2abe70))
* timezone-aware timestamps with GPS-derived timezone ([#13](https://github.com/jflammia/commuteTracker/issues/13)) ([1f89c8f](https://github.com/jflammia/commuteTracker/commit/1f89c8ffa30610edaede8093704fcb53587273e3))

## [0.0.5](https://github.com/jflammia/commuteTracker/compare/v0.0.4...v0.0.5) (2026-03-27)


### Bug Fixes

* default HOME_RADIUS_M to 50m instead of 150m ([6fcc792](https://github.com/jflammia/commuteTracker/commit/6fcc792fc94e34d38fcfd3c921a7b0ea0bf6665a)), closes [#7](https://github.com/jflammia/commuteTracker/issues/7)


### Documentation

* add downgrade setting, locked field note, iOS system settings ([94a8eb4](https://github.com/jflammia/commuteTracker/commit/94a8eb4ee7aae7aa8c3600ad2c2cda205ce3e511)), closes [#10](https://github.com/jflammia/commuteTracker/issues/10)
* add recommended OwnTracks iOS settings for commute tracking ([2a85db2](https://github.com/jflammia/commuteTracker/commit/2a85db2edbb88a68357740ccb596336ba51e5e71)), closes [#9](https://github.com/jflammia/commuteTracker/issues/9)
* restructure CLAUDE.md for autonomous mobile sessions ([df55a1d](https://github.com/jflammia/commuteTracker/commit/df55a1d3539569cc4e6cd552e6c01cb58d6fb932))
* update skills with quality gates and release process lessons ([9ecc4e0](https://github.com/jflammia/commuteTracker/commit/9ecc4e02c03f117d21cc12d2eecb319718cf3088))

## [0.0.4](https://github.com/jflammia/commuteTracker/compare/v0.0.3...v0.0.4) (2026-03-27)


### Bug Fixes

* **ci:** chain Docker build from release-please workflow ([919ea0e](https://github.com/jflammia/commuteTracker/commit/919ea0e11cbeb8e8279d0de292a2bf6238a376de))
* use /data volume for derived/raw dirs in container deployments ([44dd43f](https://github.com/jflammia/commuteTracker/commit/44dd43f6740d57e3cb0608e579b9cfd39617c383)), closes [#6](https://github.com/jflammia/commuteTracker/issues/6)
* use correct MCP transport type "http" in .mcp.json and docs ([6f1cf0d](https://github.com/jflammia/commuteTracker/commit/6f1cf0d0dae8f8b2c286d31744f958bdea42d693)), closes [#5](https://github.com/jflammia/commuteTracker/issues/5)


### Documentation

* document quality gates in CLAUDE.md and README ([82e6440](https://github.com/jflammia/commuteTracker/commit/82e64405d4778f0495579bc9f2365ae54cb08314))

## [0.0.3](https://github.com/jflammia/commuteTracker/compare/v0.0.2...v0.0.3) (2026-03-27)


### Bug Fixes

* **ci:** use bare version tags for release-please ([890d1d6](https://github.com/jflammia/commuteTracker/commit/890d1d63e0129c5a9f10dc3c112545e98eac14fb))
* **ci:** use plain docker build to avoid Docker Hub rate limits ([2608386](https://github.com/jflammia/commuteTracker/commit/260838679de530c5daf0fb1a9865c81cc9d6d538))
* MCP server 421 error behind reverse proxy ([48888d6](https://github.com/jflammia/commuteTracker/commit/48888d6ab202e8fbc4e570b250cbd06e66ce151b)), closes [#3](https://github.com/jflammia/commuteTracker/issues/3)


### Documentation

* add .mcp.json for Claude Code auto-discovery ([a78cbc6](https://github.com/jflammia/commuteTracker/commit/a78cbc6a241fb0934343ddc1febc480f578b278f))
* add lessons learned from release process setup ([1dc317c](https://github.com/jflammia/commuteTracker/commit/1dc317c6a36ec1167f44f3e6d165176648b83156))
* align commit-pr skill with release-please pipeline ([f1f364e](https://github.com/jflammia/commuteTracker/commit/f1f364ecc131b3a600ff445c27d82b6cc68a02fa))
* prohibit AI attribution trailers in commits and PRs ([a23d74c](https://github.com/jflammia/commuteTracker/commit/a23d74c67d3f7a8320eccc7af18cf49c8e59692d))
* simplify MCP setup for Claude Code and Claude Desktop ([a789b87](https://github.com/jflammia/commuteTracker/commit/a789b87d345ef1d45cd683ccaae14bec3ee6695e))

## [0.0.2](https://github.com/jflammia/commuteTracker/compare/commute-tracker-v0.0.1...commute-tracker-v0.0.2) (2026-03-27)


### Bug Fixes

* **ci:** use plain docker build to avoid Docker Hub rate limits ([2608386](https://github.com/jflammia/commuteTracker/commit/260838679de530c5daf0fb1a9865c81cc9d6d538))


### Documentation

* align commit-pr skill with release-please pipeline ([f1f364e](https://github.com/jflammia/commuteTracker/commit/f1f364ecc131b3a600ff445c27d82b6cc68a02fa))
* prohibit AI attribution trailers in commits and PRs ([a23d74c](https://github.com/jflammia/commuteTracker/commit/a23d74c67d3f7a8320eccc7af18cf49c8e59692d))
