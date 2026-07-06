# ABOUTME: Worker package — process-group-based launcher for factory workers.
# ABOUTME: Provides the Worker ABC (base.py), a local subprocess implementation
# ABOUTME: (local_subprocess.py), and a remote-worker stub (remote_stub.py).
# ABOUTME: All three share the same four-method interface so the fleet supervisor
# ABOUTME: can dispatch to either through a single code path (P1a-6/7).
"""Worker package: Worker ABC + LocalSubprocessWorker + RemoteWorker stub."""

__version__ = "0.1.0"
