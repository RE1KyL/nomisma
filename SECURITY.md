# Security

Nomisma is research software. Application permission checks do not provide an
operating-system sandbox for arbitrary processes running as the same user.

The managed application uses project-bound tools, a provider gateway, credential
storage and redacted diagnostics. Production runtime checks reject missing,
modified, additional or redirected files. A worker failure or missing reply
rejects runtime authorization. These controls remain enabled for MIT publication.

Source publication excludes credentials, local profiles, logs, research projects,
installed dependencies, caches and external runtime binaries. Do not commit such
files when configuring your own checkout.

Known limitations include development-machine paths, remaining third-party
advisory/reachability review, independent native-window acceptance and Windows
installation lifecycle tests. A public source release is not a claim that these
Windows binary release checks have passed.

Do not post credentials or private research data in public issues. Use a private
reporting channel offered by the repository owner for sensitive reports.
