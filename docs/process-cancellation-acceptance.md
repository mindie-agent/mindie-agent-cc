# Maintenance process cancellation

macOS verification on 2026-09-21, based on `b7e1241a7ccd72db48ce0dfef63a445838432570` plus the narrow `scripts/bounded.py` group-ownership correction.

The core owns the organizer's process group. Previously, the adapter started its native child in another group, so core cancellation killed the organizer while the native process and its descendant survived. Under explicit core maintenance ownership, the child now inherits that group. Local cleanup kills only its direct child; the core cleans the whole group after the organizer returns or is cancelled. Ordinary standalone adapter calls retain separate process groups.

Actual local OS processes, using the real core, Claude adapter organizer and bounded runner, reproduced the old defect with a controlled sleeping native child and descendant. After correction, the same cancellation returned in 1.602 seconds with all three processes gone. A separate successful invocation returned the organizer's empty result in 1.064 seconds without killing the organizer before it could report; no processes remained.

A further successful invocation deliberately left a sleeping descendant for the core to clean. It returned in 1.438 seconds; `ps` found neither the organizer, native child nor descendant after the core returned. An earlier harness used only `kill(pid, 0)` and could not distinguish transient process teardown from a running process; that observation is not used to claim a leak or successful cleanup.

The actual Claude Code 2.1.269 executable also completed `--version` through the core-managed adapter runner. The wrapper returned normally and its process group no longer existed afterward.

These runs made zero model calls, created no task captures or knowledge contributions, and did not change any installed plugin profile. The controlled sleep cases prove cancellation and normal-return semantics. The native CLI case proves host startup and exit only; it does not add a business-task or provider acceptance claim. Existing native Stop/organizer evidence keeps its original scope. Windows process ownership remains unverified.

Development checks: 53 tests ran: 52 passed and one conditional native-install check was skipped. This is separate from the actual local process and CLI results above.
