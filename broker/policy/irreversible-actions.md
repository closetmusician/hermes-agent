# Irreversible actions (C5)

Action types listed here are non-recoverable and always fail safe-lane condition
C5 (mistake recoverable), forcing a hold. These are actions a wrong auto-send
cannot be retracted from: a force-push rewrites history; an external first-contact
email cannot be unsent; a prod deploy ships.

git_push_force
prod_deploy
external_first_contact

<!-- one action type per line; broker/safe_lane.py seeds git_push_force in P1a -->
