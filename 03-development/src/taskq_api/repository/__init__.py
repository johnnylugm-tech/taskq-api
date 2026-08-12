"""[FR-01] Repository layer.

Citations: SAD.md §2.5 — repository is the only layer that may
import SQLAlchemy; owns `Session` lifecycle and exposes per-aggregate
repositories (task_repo, key_repo, rate_repo).
"""
