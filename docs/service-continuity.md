# Fixed-runtime service continuity

[中文](service-continuity.zh.md) | English · [Documentation Index](README.en.md)

The unpublished 0.0.1 candidate uses native Git marketplace selection and an
immutable uv runtime. One adapter installation config names the selected fixed
binding; the shared Core owns the exact service identity and process lifetime.
There is no version ordering, compatible-client fallback or successor chain.

First preparation initializes an empty profile after checking durable formats.
Release preparation builds immutable candidate bytes outside operation admission
and does not let a new Core open the active store. Business facade operations read
the complete current helper under a shared lock. Concurrent status and
cancellation requests also use shared admission, so long work does not block
its own cancellation.

Only update activation takes exclusive admission, without waiting for active
operations. A busy Core defers the switch. An idle exact owner is retired, the
new owner is observed ready, and the existing canonical installation config
is written last. Failure to publish keeps the new owner's known readiness in
the result; subsequent recovery recognizes that exact owner without attempting
to retire the old configuration again. A retired marker without new readiness
does not waive old-owner cleanup. A 30-second control observation expiry never
kills useful work or becomes a success receipt.

Existing native MCP processes keep their facade but select the current complete
helper for their next operation. They never choose a Release or reverse the
binding. New calls during exclusive activation may wait briefly or receive an
explicit temporary-unavailable, not-started result. There is no generation
chain or semver-derived service compatibility.

Stop takes one complete immutable binding snapshot without service admission,
then writes the existing capture queue. A retired helper may enqueue an
authorized event but cannot restart its retired owner. The new owner consumes
that same queue; repeated native event identities are deduplicated. Current
consent, scope, task revocation and durable-format checks still apply. Known
capture failures produce a static native warning without continuing the model.
This preserves delivery during the installation gate without a second queue.

Contribution and reporting choices are independent user data. Native code
selection or host reload does not ask for authorization again, reset scope,
or change a recorded disabled choice. Paid and uncertain outcomes remain
preserved. Unsupported earlier development profiles are not automatically
migrated. Release compatibility comes from the declared Harness interfaces,
facade/helper protocol and actual durable formats.

The following notes describe earlier updater implementations and their historical
acceptance. They do not claim that the replacement has passed native acceptance.

## Historical implementation

# Service continuity during plugin updates

An updater used to stop an idle knowledge service and install the next plugin
without restoring the service. A later query hid the defect by starting it;
a task that only used remote development could then deliver a Stop event to
no service and lose that increment.

The historical update transaction confirmed that the exact old loopback endpoint
had exited, switched the native package and committed runtime tuple, and made
one bounded restoration attempt while retaining the exclusive operation lock.
Restoration used the selected interpreter and engine configuration directly,
never a shared-lock launcher. Core startup remains one spawn, five seconds,
and three readiness probes; the returned service must be unfrozen.

Only a service stopped by this updater invocation is eligible for updater
restoration. A service already absent before the update stays absent; revoked
or explicitly unbound task leases do not authorize updater restoration. No task is
activated, no transcript is replayed, and no model call belongs to the updater.

The lifecycle repair candidate handles Stop separately: an activated task with
contribution enabled first durably hands off its capture reference, then may
request one coalesced wake when the service is absent. Knowledge queries retain
on-demand startup. Hook acceptance, a live worker, organized experience and a
submitted PR are separate outcomes. This change requires new native acceptance;
the historical update-continuity evidence below does not establish it.

Historical rollback restored a service only after both the retained native
package and exact old pointer were proven restored. Failed or interrupted
handoff was kept in updater status as service_handoff. That updater, pointer
and scheduler have been removed. The current implementation uses the fixed installation and exact Core owner
described above; it does not retain that update transaction.

The selected native profile is retained for the replacement service. Claude
Code imports only supported provider/model/effort values from that profile's
user settings into its isolated organizer; it does not import hooks, tools,
MCP configuration or project settings. Secret values never enter updater status.

Knowledge MCP requests use the shared core's ready-service path. Reuse requires
the selected engine's exact configuration fingerprint, an active configuration
and a healthy worker. A reachable endpoint in the same data domain is
insufficient: it may belong to another generation or a retired configuration.
The adapter does not substitute that endpoint or start a second owner when the
core reports an unknown or failed owner. A matching healthy service is reused
without another startup. These checks do not impose a deadline on the subsequent
knowledge query or body read.

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
