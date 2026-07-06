# ABOUTME: Factory package — the job orchestration spine for the Fable system.
# ABOUTME: Owns the job store (SQLite, supervisor-sole-writer), state machine,
# ABOUTME: per-tick integrity checks, build-jobs.md mirror, and the two intake
# ABOUTME: seams (/factory Telegram command + tasks.md factory: tag).
# ABOUTME: Every later phase (worker, gauntlet, merge) reads/writes jobs here.
