# mindie-agent-cc 0.1.3

[中文](mindie-agent-cc-0.1.3.md) · **English**

Candidate publication-contract validation now inherits system and user Git configuration, preserving enterprise proxies, CA paths, and Windows schannel trust. It retains the temporary bare repository, disabled Git hooks, and exact publication commit verification.

The plugin pins `mindie-knowledge 0.13.4`, whose independent component builder uses the installer's default system certificate policy. The existing formal `remote-dev 0.10.6` and `mindie-diagnostics 0.5.2` pins remain.

The repaired validator completed a real Git fetch and publication-contract read on the Windows enterprise network, and the original fix passed Ubuntu, macOS, and Windows CI. This release's workflow verifies its exact dependencies, full component contracts, and benchmarks and publishes the reports as release assets. Validator verification and complete native Claude Code session acceptance are recorded separately.

Updates continue to prepare runtimes in the background and switch when idle, preserving saved contribution settings and explicit session knowledge authorization.
