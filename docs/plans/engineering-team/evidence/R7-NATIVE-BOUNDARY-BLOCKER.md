# Native Council boundary remains unavailable

The October 2 isolated source gate did not make a generating native request.
MacOS 27.0 (26A5378n), `/usr/bin/sandbox-exec`, and the exact ChatGPT bundled
Codex CLI 0.159.2 were used. The last bounded metadata probe at 22:35 UTC sent
app-server `initialize`, `initialized`, and `model/list` over stdio, with copied
private subscription auth in a canonical private scratch HOME/CODEX_HOME.
All processes were owned-group cleaned and streams closed. The scratch/auth
copy was removed; no provider diagnostics or credentials are tracked.

The exact default-deny profile's own role evidence and scratch were readable;
peer directories, ledger, private logs and artifacts were not. Binary/system
dependencies were limited to the exact executable, `/usr/lib` and
`/System/Library`, plus literal ancestors. Narrow managed-config metadata (not
config bytes), `/dev/urandom`, CFPreferences daemon/agent Mach services, exact
UID/daemon CFPreferences shared-memory names, and outbound TCP port 443 were
added. Initialization worked and model/list returned eight catalog entries.
Those entries do not prove a successful backend request.

The last diagnostic variant also allowed these facilities, none committed as
native support: Mach services `com.apple.system.opendirectoryd.libinfo`,
`com.apple.mDNSResponder`, `com.apple.SystemConfiguration.configd`,
`com.apple.networkd`; outbound UDP port 53; `system-info net.link.addr`; exact
`/private/etc/hosts` read. It still reported
`failed to refresh available models: Connection failed: error sending request`.
There was no successful HTTPS response or HTTP status. Error classification:
network/backend attestation unavailable, underlying cause **unknown**.

Earlier local sandbox logs demonstrated denied `system-info net.link.addr`
and `/private/etc/hosts` reads. Allowing them was not sufficient. DNS-related
Mach/UDP additions were diagnostic hypotheses, not a proven root cause.
Other denied notification/logging/user-preference/Info.plist/networkd-plist
operations were not shown necessary and were not broadly granted. No broad
filesystem, managed configuration, MCP or Mach-lookup exception was added.

`verify_native_network` now fails capability-unavailable before any native role
launch. Doctor reports implemented partial mechanics but native-ready false.
Next: root-owned bounded non-generating backend attestation under the exact
frozen boundary (such as a genuinely successful native account/rate-limits
response), with the same peer/runtime/log/artifact/symlink/child denial probes.
Initialization, cached/fallback model lists, network error suppression, fixture
quality or a requested-only model ID must not lift this gate.

The R6/Council reconciliation delegates version and bootstrap cleanup to the
shared bounded probe helper, refusing missing captured ownership before any
bootstrap RPC. Shared ownership repair
`093ed0f397289bffe9ea249c10796cb436027f00` is a separate explicit acceptance
dependency, not imported into this isolated Council checkpoint. Earlier local
cleanup observations above are not acceptance of the helper's ownership logic.
Root must accept and integrate that repair before new native probes; exact
backend attestation remains independently unavailable afterwards.
