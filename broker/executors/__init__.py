# ABOUTME: Broker-side egress executors — the ONLY code that performs a real send
# ABOUTME: or push. Runs inside the broker process, which holds the credentials the
# ABOUTME: assistant does not. message_executor performs a platform message send;
# ABOUTME: git_push_executor performs a real `git push`. Both fail-closed when the
# ABOUTME: required credential is absent (never fall back to a partial/direct path).
