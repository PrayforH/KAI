# Qwen dictation trial on 174

The 174 trial now transcribes audio with `Qwen3-ASR-1.7B` through New API channel 17.
The channel remains bound to the existing Qwen service. The 173 deployment is unchanged.
This is utterance-incremental transcription over the existing dictation protocol, not native
Qwen acoustic streaming. Stopping recording still triggers the existing text-only refinement
route (`deepseek-v4-flash`); it does not send the whole recording to another ASR.

## Runtime layout

The isolated `qwen-dictation` sidecar runs the existing deployed API image with the new
`harness.dictation.qwen_bridge` module mounted read-only. It has no published host port,
a read-only root filesystem, dropped capabilities and its own health check. Gateway credentials
are copied only into a mode-0600 deployment configuration, never into this repository.

Each connection owns its PCM buffer, confirmed sentences and pending hypothesis. Silero VAD
skips silence and identifies utterance endpoints. Qwen transcribes bounded utterance snapshots
at a 1.5-second polling interval; confirmed text remains separate from the current hypothesis.
Audio snapshots are capped at 12 seconds and the pending buffer at the configured maximum.
An unchanged final snapshot reuses the latest hypothesis rather than re-transcribing it.
Silence does not trigger ASR or insert empty-result notices.

The API keeps its existing `funasr_realtime` setting as the legacy WS **protocol selector**.
The actual provider is selected by `HARNESS_DICTATION_REALTIME_URL=ws://qwen-dictation:18014`.
The sidecar's `/health` response identifies `Qwen3-ASR-1.7B` and `utterance_incremental` mode.
Its adapter always calls the configured gateway with that Qwen model name. No Nano audio
transcription is involved in this trial.

## Cutover and rollback

Deployment directory: `/data/kai-qwen-trial-20261016`.

- `compose.bridge.private.json` starts the private adapter on `agent-studio-174_default`.
- `compose.api.before.private.json` preserves the complete prior API configuration.
- `api.before.private.json` preserves the old container inspection for configuration comparison.
- `compose.api.qwen.private.json` records the candidate API configuration.
- `kai-qwen-cutover.py` rejects concurrent API/configuration changes, checks the rendered
  environment differs only in the dictation URL, and restores the prior configuration if
  API startup fails.

The authoritative API configuration is
`/data/kai-intelligent-ui-develop-b6289d48/compose.runtime.private.json`.
Only its dictation WS URL changed. The current API image and its other environment values
were preserved. There were zero established Nano dictation connections before the cutover.
No worker, database, shared model service or Web image was rebuilt or restarted.

To roll back, wait for active Qwen recordings to finish, restore
`compose.api.before.private.json` to that authoritative configuration path, and recreate only
its `api` service with the same project name (`agent-studio-174`) and
`up -d --no-deps --no-build --wait api`. After API health and the old Nano path are verified,
the separate `kai-qwen-trial-174` Compose project can be stopped.

## Verification and limits

- Fifteen dictation tests passed, including live drafts through the existing API session manager,
  finalization without duplication, independent simultaneous clients, cancellation/capacity,
  invalid PCM rejection, utterance rollover and skipping silence.
- Ruff and targeted Pyright checks passed with zero errors.
- Direct New API transcription of the public Qwen Chinese WAV returned the expected sentence.
- A candidate replay produced a draft before STOP at 1.89 seconds and completed correctly.
- A replay after the API restart used the actual deployed settings and produced a draft before
  STOP at 1.74 seconds. Its final text was `甚至出现交易几乎停滞的情况。`.
- The API and adapter report healthy; the public HTTPS entry returned 200 using the known CA.
- New API channel metadata confirms channel 17, model `Qwen3-ASR-1.7B`, enabled status and
  the existing Qwen upstream.
- The deployed bridge SHA256 is
  `dee73ba0fe3b1b8b57c3d4ede66433c95c898c48b3db4c91b1338bcc5afa2a9a`.

These latency observations are two public-sample smoke tests, not an office accuracy or latency
benchmark. The existing limit of 32 sessions remains in both the API and bridge; 32 concurrent
speakers have not been load-tested against the Qwen model. The full Web/browser authentication
flow was not re-run; the existing frontend and BFF are unchanged and the real-audio replay used
the production API's session-manager implementation. No user microphone was accessed.

The public sample is available from the official Qwen repository's example host:
<https://qianwen-res.oss-cn-beijing.aliyuncs.com/Qwen3-ASR-Repo/asr_zh.wav>.
