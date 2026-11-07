# CrashLab contribution requirements

Use source branches and pull requests; do not publish changes directly to master.
Before merging, request a top-level `@codex review` identifying the full current
head SHA. Wait for a completed clean review of that exact SHA, resolve all
findings, and require CI to pass. Every new commit requires a new review.
Never treat queued reviews or elapsed time as approval.

For verifier changes, run the Python suite and real-browser integrity tests.
Treat evaluator hash updates as reviewed release changes, never as part of an
application repair. Keep the application's writable workspace separate from
trusted test contracts and evaluator files.

Keep private competition strategy, recording scripts and submission preparation
outside the repository. Preserve truthful runtime and evidence limitations.
