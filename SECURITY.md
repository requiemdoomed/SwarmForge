# Security Notes

**Author: Swayam Shakya**

SwarmForge is an educational prototype and should be run on trusted machines and networks.

- The default bind address is `127.0.0.1`; keep it local unless you understand the network exposure.
- There is no peer authentication, encryption, authorization, signed manifest, or protection against a malicious tracker.
- SHA-256 detects accidental or adversarial piece corruption relative to the manifest, but it does not prove who created the manifest.
- Do not expose the tracker, peer, or local control API to the public Internet.
- Use disposable test files and avoid sharing confidential data during experiments.
- Report reproducible vulnerabilities to the project maintainer rather than testing against systems you do not own.
