# Changelog

## [1.4.0] - 2026-05-04

### Bug Fixes

- Reconnect on broker disconnect with exponential backoff ([`1df2782`](https://github.com/zen-strayer/kanchi/commit/1df278276e25af04de006753ab2b0cbd7ec17f84))
- Batch orphaned-tasks queries to eliminate N+1 ([`7a54cf0`](https://github.com/zen-strayer/kanchi/commit/7a54cf0119a8dcf32ea01e41fbe0ce998d792ba3))
- Replace Query.count() with scalar count to avoid subquery materialization ([`87cba57`](https://github.com/zen-strayer/kanchi/commit/87cba57c53f13b4d036e01d65412d5c911a1d2a8))
- Four active production bugs — datetime, orphan snapshot, broadcaster, avg_runtime ([`b4f7da4`](https://github.com/zen-strayer/kanchi/commit/b4f7da451a5e0094ac391718e7bafacac8d20a45))
- Bounded workflow thread pool, orphan sleep fix, broker validation, reconnect recovery (#5) ([`ef8e896`](https://github.com/zen-strayer/kanchi/commit/ef8e89614c02c1d1bb5640c3380aadf886283bd7))
- Upgrade pickle serialization warning to CRITICAL level ([`051dc0d`](https://github.com/zen-strayer/kanchi/commit/051dc0ddac402f2c661916633f7e4ec5880b078b))
- Run as non-root user; require CELERY_BROKER_URL at runtime ([`223d767`](https://github.com/zen-strayer/kanchi/commit/223d767c6266156a02c9b4e01f63d221ce9a986b))
- Add per-IP rate limiting to /api/auth/basic/login (5 req/60s) ([`3672a13`](https://github.com/zen-strayer/kanchi/commit/3672a13d9730ebeb786e5455c7170264de595dc3))
- Reject empty ALLOWED_EMAIL_PATTERNS when OAuth is enabled ([`4f71888`](https://github.com/zen-strayer/kanchi/commit/4f718887d660d314bf6cfdcda3723d890b699046))
- Validate Slack webhook URL is HTTPS and hooks.slack.com domain ([`b6806ca`](https://github.com/zen-strayer/kanchi/commit/b6806ca15ffb9c0037b20186d43a920b503a95f0))
- Gate /metrics behind auth when AUTH_ENABLED=true ([`a2e5dc0`](https://github.com/zen-strayer/kanchi/commit/a2e5dc0433bb13858d4a3f4c3c491797086bd0b2))
- Accept WS auth via first message; deprecate query param token ([`cd63ae4`](https://github.com/zen-strayer/kanchi/commit/cd63ae4c94e0b23e0023d4db08aa5c596f303ca4))
- Add auth handshake timeout; extract register_accepted to avoid duplicated logic ([`e72df13`](https://github.com/zen-strayer/kanchi/commit/e72df135e1e3520801cf31dfe96eeaa7e129e224))
- Skip rate limit for unknown IP; log register_accepted; guard OAuth check with auth_enabled ([`de1a4bf`](https://github.com/zen-strayer/kanchi/commit/de1a4bfedb5830372861695ec3658ab73aec9e26))
- Guard WS first-message against non-dict JSON; reduce auth timeout to 5s; unify close reason ([`5adcfac`](https://github.com/zen-strayer/kanchi/commit/5adcfaccc8e4f833724a7de926fd4e274a6475dc))
- Rename _is_rate_limited to _is_allowed; prune stale IPs; use X-Forwarded-For for proxy awareness ([`603a713`](https://github.com/zen-strayer/kanchi/commit/603a713f6681802d25b37136c54788ec81d5c09d))
- Metrics fails-closed when auth uninitialized; skip WS reconnect on 4401 ([`6bdead6`](https://github.com/zen-strayer/kanchi/commit/6bdead61d0e6dfd37f6e662a1bf3b1edb5d74034))
- Add httpx dev dependency for starlette test client ([`2b6d51f`](https://github.com/zen-strayer/kanchi/commit/2b6d51f6332a0cd49cdacade70d8a978f6fbf2ce))
- Create annotated tag so --follow-tags pushes it to remote [NOJIRA] ([`8d98f1c`](https://github.com/zen-strayer/kanchi/commit/8d98f1cfa2c087fe5aa564925d5399c81057a276))
- Skip CHANGELOG commit if already up to date (idempotent re-run) [NOJIRA] ([`26e614c`](https://github.com/zen-strayer/kanchi/commit/26e614caa63289d0ee825bcd649bb95134fb2da5))


### Features

- Retention pruning, WS env filtering, limit cap, sort_by whitelist (#6) ([`b2eaae0`](https://github.com/zen-strayer/kanchi/commit/b2eaae05a21e8986f2ad9258fe7dd8fd638bbef9))
- Handle task-rejected events; add to COMPLETED_EVENT_TYPES ([`f2f735c`](https://github.com/zen-strayer/kanchi/commit/f2f735c974198d123013d7ba86211b2aebd9ece0))
- Wire Docker publish into release workflow via workflow_call [NOJIRA] ([`ef24ca0`](https://github.com/zen-strayer/kanchi/commit/ef24ca09c5d1f1b01d56c9a0d00607aa7c902885))

## [1.3.8] - 2025-12-28

### Features

- Add task progress #69 (#74) ([`1d5bbb4`](https://github.com/zen-strayer/kanchi/commit/1d5bbb4f0ce032fe830cfa9f98db5c496b287cbf))

## [1.3.6] - 2025-11-27

### Features

- Integrate Prometheus metrics collection into event handling (#62) ([`bd127ce`](https://github.com/zen-strayer/kanchi/commit/bd127cef8ef8fd1640af8493c5c1135a70fa2780))
- Implement task resolution feature for manual task management ([`4a7b5af`](https://github.com/zen-strayer/kanchi/commit/4a7b5af56acc4c3ba4ed1cf324f374afcbe12c67))

## [1.3.5] - 2025-11-25

### Features

- Enhance TaskService for MySQL support and UTC timestamp handling (#61) ([`f133ac4`](https://github.com/zen-strayer/kanchi/commit/f133ac4ab59b05a760206874555d50a67be5fc78))

## [1.3.4] - 2025-11-24

### Features

- Implement TaskLatestDB for efficient task event snapshots (#54) ([`e8589bf`](https://github.com/zen-strayer/kanchi/commit/e8589bfc5983072a7d281940b8f439a938ea40ce))

## [1.3.3] - 2025-11-23

### Bug Fixes

- Add deps ([`d04f25a`](https://github.com/zen-strayer/kanchi/commit/d04f25a92329f40d071872eeccc4e629f18b4ad3))

## [1.3.2] - 2025-11-22

### Bug Fixes

- Add MySQL driver support to resolve crash loop with MySQL databases (#48) ([`04b349b`](https://github.com/zen-strayer/kanchi/commit/04b349b21221693c4b2a90907650ea6945bcb095))
- Enhance _json_safe method to handle Enum types (#50) ([`6da8015`](https://github.com/zen-strayer/kanchi/commit/6da8015709ad288eab2404c90f25105ffae5c472))


### Features

- Add support for pickle serialization in Celery monitor (#51) ([`b2e1909`](https://github.com/zen-strayer/kanchi/commit/b2e19098c9e3c0bcfb61c6f4242e80c6c53a31c4))
- Add app version (#52) ([`2448e10`](https://github.com/zen-strayer/kanchi/commit/2448e1025bb0d2138ea04713df1afb60de11ea13))

## [1.3.1] - 2025-11-08

### Bug Fixes

- Handle task truncation gracefully #27 (#40) ([`e9f717e`](https://github.com/zen-strayer/kanchi/commit/e9f717eb7bca201039cdb32725231a01ea2666fa))

## [1.2.0] - 2025-10-28

### Features

- Add Redis support for Celery broker ([`cdcfbe7`](https://github.com/zen-strayer/kanchi/commit/cdcfbe74e440f50771b4820f78b89d33a517cf60))

## [1.1.0] - 2025-10-27

### Bug Fixes

- Pagination shows wrong selected limit (closes #19) ([`9c51669`](https://github.com/zen-strayer/kanchi/commit/9c5166910413963117080df8ce903ff731263c3c))
- Use correct sqldialect for timeline requests ([`10dd076`](https://github.com/zen-strayer/kanchi/commit/10dd0763410ef8fdb99a04e7f21362a811f79f38))
- Remove keybind ([`6be0ef9`](https://github.com/zen-strayer/kanchi/commit/6be0ef93a0d875c7d69fc461f306c2e19b12bf3e))


### Features

- Work on workflows and failed task display ([`4c0fd19`](https://github.com/zen-strayer/kanchi/commit/4c0fd190a69b4c50095c03f55302beb06752ed1f))
- Add workflows and task details ([`a284b33`](https://github.com/zen-strayer/kanchi/commit/a284b33d8ae23d3ced76a3fc7ef7a9ebe87733cf))

## [0.0.19] - 2025-10-14

### Bug Fixes

- Env filter only affects reads #17 ([`6d21c7e`](https://github.com/zen-strayer/kanchi/commit/6d21c7eb2d59980e3a2ffa167717d748f2218d1e))

## [0.0.18] - 2025-10-12

### Bug Fixes

- Imports ([`5519d9d`](https://github.com/zen-strayer/kanchi/commit/5519d9d9e7a5ecf95532ec1b6e9a1ed9f8b0bfc3))

## [0.0.17] - 2025-10-12

### Bug Fixes

- Imports ([`78082a0`](https://github.com/zen-strayer/kanchi/commit/78082a0f5747b4c146565125d1789cebe5afdb15))

## [0.0.16] - 2025-10-12

### Bug Fixes

- Imports ([`80a9561`](https://github.com/zen-strayer/kanchi/commit/80a9561bf30c14e1aaa52f38f641a34a566d76c4))

## [0.0.15] - 2025-10-12

### Bug Fixes

- Imports ([`05bfbe1`](https://github.com/zen-strayer/kanchi/commit/05bfbe13be1395ebd191352f35c46aee61df9eb9))

## [0.0.14] - 2025-10-12

### Features

- Add task registry and environments ([`c55e187`](https://github.com/zen-strayer/kanchi/commit/c55e1877a9b0b8094ae56e698176208cdee28edb))

## [0.0.4-alpha] - 2025-10-05

### Features

- Add light mode closes #14 ([`b44b835`](https://github.com/zen-strayer/kanchi/commit/b44b8354abd8e0df841a584f53e10754316bcfd7))

## [0.0.2-alpha] - 2025-09-27

### Bug Fixes

- Keep correct row expanded #1 ([`6cd47da`](https://github.com/zen-strayer/kanchi/commit/6cd47da6ebd814b6fcc314fd0dcc98f8e8b73457))

## [0.0.1-alpha] - 2025-09-24

