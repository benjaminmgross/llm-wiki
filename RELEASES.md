# Releases

Release notes are written as version-specific documents so the README can stay
focused on the current package, install flow, command surface, and operating
model.

| Release | Notes |
| --- | --- |
| Unreleased | Session CLI provider, provider/CLI/model overrides, OpenAI-compatible `api_key_env`, and configured-provider vision during registration. Built-in CLI subscription checks have documented limits; Claude OCR requires restricted file access. Timeout/cancellation cleans up child processes, and partial discovery preserves successful source metadata. See [session configuration](README.md#session-mode-no-api-key) and [OpenAI-compatible keys](README.md#openai-compatible-providers). |
| Unreleased capture | Immutable captured conclusions with provenance, optional exports, deterministic supersession, pinned standing rules, retirement, and undo. Both configured-provider ingest and native-session prepare/apply are supported; the native path requires no provider or embedding inference. See [captured conclusions](README.md#captured-conclusions). |
| v1.3.0 | [RELEASE_v1.3.0.md](RELEASE_v1.3.0.md) |
| v1.1.0 and earlier | [CHANGES_GUIDE.md](CHANGES_GUIDE.md) |
