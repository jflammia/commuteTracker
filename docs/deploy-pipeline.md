# Deployment pipeline (production automation)

How a merged change reaches production. Established during the 2026-06-11 rewrite
cutover; see the `prod-deploy-topology` memory and `cutover-runbook.md` for context.

```
GitHub  jflammia/commuteTracker (dev: PRs, CI, release-please)
   │  main updated on merge
   ▼  (scheduled mirror, ≤15 min — .forgejo/workflows/mirror-commutetracker.yml
   │   in justin/blueshift-stacks; force-pushes main, no-op when unchanged)
Forgejo justin/commutetracker (prod source + image registry)
   │  push to main fires the repo webhook
   ▼  (Komodo build listener — webhook on the repo → komodo.blueshift.xyz)
Komodo Build "commutetracker"  (builds Dockerfile.backend → git.blueshift.xyz registry, :latest)
   │  new :latest digest
   ▼  (stack auto_update / poll_for_updates)
Komodo Stack "commutetracker" on bighead  (podman; pulls :latest, redeploys)
   │
   ▼  commute-receiver (FastAPI :8090, host :8083) — OwnTracks ingest + API + MCP + SvelteKit UI
```

## Key points
- **GitHub is dev; Forgejo is the prod source.** They are kept in sync by the
  scheduled mirror — do not hand-edit the Forgejo repo. A merged GitHub PR reaches
  prod within ~15 min (mirror interval) + build + redeploy, with no manual step.
- **Boot is incremental.** The backend persists a rebuild checkpoint
  (`<data_dir>/derived/rebuild_checkpoint.json`); restarts replay only events since
  the checkpoint (full rebuild only on first boot / missing checkpoint).
- **Manual override** (any step, via the homelab CLI — `hlc`, never direct SSH):
  `hlc komodo build-run commutetracker` then `hlc komodo stack-deploy commutetracker`;
  logs via `hlc komodo stack-logs commutetracker`.
- **Auth/secrets** are injected at deploy time from 1Password via the stack's
  `pre_deploy` (geofences + OwnTracks `/pub` Basic Auth).
