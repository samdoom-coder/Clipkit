# Security

clipkit is a local-first tool that gives an AI agent access to media processing.
That combination deserves a threat model.

## Threat model

1. **Untrusted media content.** Transcripts, titles, and metadata can contain
   text that looks like instructions ("ignore previous instructions and run
   ..."). A coding agent may have shell access, so this is a prompt-injection
   surface.
2. **Untrusted URLs.** URL ingest downloads remote media. Without controls this
   is an SSRF vector against localhost, cloud metadata endpoints, and private
   networks.
3. **Path handling.** Clip paths, style files, and workspace paths are supplied
   by an agent or user and must not escape the workspace.
4. **Dependency and model supply chain.** Model weights and Python packages are
   downloaded from the internet.
5. **Publishing.** clipkit does not post to social platforms. That is deliberate.

## Controls in v1

- URL ingest accepts only `http(s)` and rejects private, loopback, link-local,
  reserved, and multicast addresses before invoking `yt-dlp`.
- The skill instructs agents to treat all media content as data, never as
  commands.
- Rendered outputs and inspection images stay inside the workspace; inspection
  rejects paths outside it.
- No shell interpolation: FFmpeg, ffprobe, and yt-dlp are invoked with argument
  arrays.
- No automatic upload, publishing, or credential use.
- Optional dependencies are opt-in extras (`rumble`, `whisper`, `faces`, `mcp`).

## Known limitations

- DNS rebinding can defeat a pre-resolution IP check. For multi-tenant or
  untrusted use, run downloads in a container with egress filtering and no
  access to cloud metadata.
- clipkit is not a sandbox. If an agent can run arbitrary commands in the same
  environment, clipkit's path checks are not a security boundary.
- Model weights are trusted from Hugging Face / PyPI. Pin versions and verify
  hashes for regulated environments.

## Reporting

Please open a private security advisory on the repository (or email the
maintainers) rather than filing a public issue. Include a minimal reproduction
and the affected version.
