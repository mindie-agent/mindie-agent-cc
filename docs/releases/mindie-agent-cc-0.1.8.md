# mindie-agent-cc 0.1.8

Release preparation is in progress. The remote-dev 0.11.0 formal dependency
revision and final platform evidence must be recorded before this candidate is
published.

This update integrates remote-dev's shared SSH authentication. After one
password entry, remote-dev verifies access and attempts to install and verify a
managed public key. A server that requires password authentication can reuse
the saved credential in later chats and independent plugin runtimes. Failed key
installation remains visible even when password access succeeds.

The default shared credential file is `~/.mindie/ssh/credentials.json`, scoped by
SSH host, port, and user through remote-dev profiles. Claude Code stores no
second password copy and does not invoke an OS keychain or request the computer
account password. Public-key failure alone is not treated as proof that the
server has disabled that authentication method.

The runtime retains knowledge 0.13.4 and diagnostics 0.5.4. Existing native
`PreToolUse` identity binding, manual knowledge activation, and component
installation boundaries remain authoritative.

Validation must identify the exact installed component revisions and separate
real OpenSSH checks from native Claude Code host acceptance. Shared component
checks cover key installation, password-only access across fresh processes,
virtual environments and harness profiles, and recovery before one business
command. They do not establish that an existing Claude Code host has loaded
this release. Final tag test and benchmark reports and checksums accompany the
formal release.
