# Service continuity during plugin updates

An updater used to stop an idle knowledge service and install the next plugin
without restoring the service. A later query hid the defect by starting it;
a task that only used remote development could then deliver a Stop event to
no service and lose that increment.

The update transaction now confirms that the exact old loopback endpoint has
exited, switches the native package and committed runtime tuple, and makes
one bounded restoration attempt while retaining the exclusive operation lock.
Restoration uses the selected interpreter and engine configuration directly,
never a shared-lock launcher. Core startup remains one spawn, five seconds,
and three readiness probes; the returned service must be unfrozen.

Only a service stopped by this updater invocation is eligible for updater
restoration. A service already absent before the update stays absent; revoked
or circuit-paused task leases do not authorize updater restoration. No task is
activated, no transcript is replayed, and no model call belongs to the updater.

The lifecycle repair candidate handles Stop separately: an activated task with
contribution enabled first durably hands off its capture reference, then may
request one coalesced wake when the service is absent. Knowledge queries retain
on-demand startup. Hook acceptance, a live worker, organized experience and a
submitted PR are separate outcomes. This change requires new native acceptance;
the historical update-continuity evidence below does not establish it.

Rollback restores a service only after both the retained native package and
the exact old pointer are proven restored. Failed or interrupted handoff is
kept in the existing updater status as service_handoff. Same-revision checks
and successful Feed sync cannot hide it. A later scheduler invocation does
not interpret that marker as permission to spawn or retry restoration.

The selected native profile is retained for the replacement service. Claude
Code imports only supported provider/model/effort values from that profile's
user settings into its isolated organizer; it does not import hooks, tools,
MCP configuration or project settings. Secret values never enter updater status.

## Validation boundary

Real local-daemon acceptance covers live, absent, revoked and rollback paths
for Kimi and Claude Code. Those cases use a controlled native-install boundary;
they establish actual process/endpoint/admission behavior, not native package
installation or model authentication. Native acceptance is recorded separately.
Windows real-machine acceptance remains with the maintainer after merge.

Real Claude Code 2.1.269 acceptance used the existing DSV4 route in an owned
persistent native profile, with provider/model variables absent from the updater
and service shell environment. After an explicit old-service setup, actual native
installation replaced the service PID/endpoint in 7.32 s, preserved the same task
lease and returned an unfrozen service. A retained task then completed one default
permission-mode turn in 1.07 s with zero tools. Native Stop produced one organized
capture and one successful organizer attempt, returning zero entries for the
configuration-only material. No post-update ensure/query, draft or new PR occurred.
Contribution was disabled after 1.9 s; provider settings and all owned processes
were cleaned up.

The business result directly reports deepseek-flash. The selected persistent
profile configured high effort, but a short-lived organizer command escaped the
independent process sampler; its exact runtime argv/effort is not separately
claimed. The harness retained that final assertion failure without rerunning a
model. An earlier no-model harness readback used the wrong package path; actual
installation had passed, and the corrected forward-switch case above is separate.
The final exception-readback fix was also checked through the actual native API
against the registered stable package, without another install or model call.
