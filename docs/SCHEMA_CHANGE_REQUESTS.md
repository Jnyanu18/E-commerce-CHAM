# Schema Change Requests

Agents needing a schema change add an entry here instead of editing `supabase/`. The database agent reviews and implements via a new numbered migration.

Format: Date / Requested by / Table / Change / Why / Status.

_None yet._

## Database agent notes (2026-10-14)
- Implemented in 0005-0011: city tier enum, pincode CHECK, settlements/settlement_items/shipments, reaper, indexes, privileges. No pending requests.
- Heads-up (affects other owners, not a request to the database agent): `engine/seed/synthetic.py` uses `tier1..tier3` and must move to `tier_1..tier_3`; `engine/worker` should call `reap_stale_jobs()` periodically. Details in PROGRESS.md > Integration Needed.
