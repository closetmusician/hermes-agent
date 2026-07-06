# P0-2c-FIX Report

## REQ-01: RED Reproduction

**Failing test:** `tests/plugins/email_send_guard/test_email_send_guard.py::TestOneTimeApprovalConsumption::test_approval_consumed_after_successful_send`

**Root cause confirmed:** In `_post_tool_call`, when `result=""`, the code does:

```python
try:
    parsed = json.loads(result)   # json.loads("") raises JSONDecodeError
    ...
except (json.JSONDecodeError, ValueError):
    send_failed = True            # ← incorrectly treats empty-string success as failure
```

`json.loads("")` raises `json.JSONDecodeError` (empty string is not valid JSON). The except branch then sets `send_failed = True`, causing the approval to be preserved instead of consumed. The one-time approval invariant is violated.

---

## REQ-02: Root-Cause Fix

**File:** `plugins/email-send-guard/__init__.py`, `_post_tool_call` (lines ~395–403)

**Diff:**
```diff
-    send_failed = False
-    if isinstance(result, str):
-        try:
-            parsed = json.loads(result)
-            if isinstance(parsed, dict) and parsed.get("error"):
-                send_failed = True
-        except (json.JSONDecodeError, ValueError):
-            # Non-JSON result (e.g. raw exception message) = failure
-            send_failed = True
+    send_failed = False
+    if isinstance(result, str):
+        stripped = result.strip()
+        if stripped:
+            try:
+                parsed = json.loads(stripped)
+                if isinstance(parsed, dict) and parsed.get("error"):
+                    send_failed = True
+            except (json.JSONDecodeError, ValueError):
+                # Non-JSON result (e.g. raw exception message) = failure
+                send_failed = True
+        # Empty/whitespace result is a benign success — do not set send_failed
```

**Interpretation chosen:** An empty or whitespace-only result string is a benign success. Only a non-empty string that fails JSON parsing is treated as a failure. This is the minimal fix; no other logic was changed.

---

## REQ-03: Full Suite GREEN

```
collected 16 items

tests/plugins/email_send_guard/test_email_send_guard.py::TestPluginLoads::test_module_loads_without_error PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestPluginLoads::test_register_function_exists PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestStateMachineGates::test_block_email_send_without_draft PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestStateMachineGates::test_block_email_send_if_not_previewed PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestStateMachineGates::test_block_email_send_if_not_approved PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestStateMachineGates::test_full_state_machine_allows_send PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestRecipientBinding::test_recipient_mismatch_blocks_send PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestRecipientBinding::test_extract_recipient_formats PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestApprovalExpiration::test_approval_expires_after_ttl PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestLongBodyRoundtrip::test_long_body_roundtrip PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestOneTimeApprovalConsumption::test_approval_consumed_after_successful_send PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestOneTimeApprovalConsumption::test_approval_preserved_on_failed_send PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestToolRegistration::test_email_load_draft_tool PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestToolRegistration::test_email_show_preview_tool PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestToolRegistration::test_approve_email_command PASSED
tests/plugins/email_send_guard/test_email_send_guard.py::TestDormantStatusInConfig::test_plugin_not_in_enabled_by_default PASSED

16 passed in 0.25s
```

**Result: 16/16 PASSED — 0 failures.**
