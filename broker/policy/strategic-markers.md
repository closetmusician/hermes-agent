# Strategic markers (C3)

If a payload contains any marker below (case-insensitive substring), safe-lane
condition C3 (operational-not-strategic) fails and the action holds for approval.
These are conservative keywords that flag board/exec/legal/pricing/confidential
content — the class of message that must never auto-send.

board
exec
executive
legal
pricing
confidential
acquisition
layoff
fundraise

<!-- one marker per line; broker/safe_lane.py seeds a subset conservatively -->
