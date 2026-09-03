# Runtime and recovery

Use `python <skill-dir>/scripts/run.py --workspace <workspace>` as the command prefix. It resolves the skill's own environment and source without changing the caller's directory. Keep private workspaces outside the installed skill.

## Configuration

`init-config` creates `<workspace>/config/config.yaml` without replacing existing files. Global `--config path` selects another YAML file, relative to the workspace. CLI overrides apply only to that invocation.

For polishing set `llm.base_url` and `llm.model`, or `BILIDRAFT_BASE_URL` and `BILIDRAFT_MODEL`. `BILIDRAFT_API_KEY` supplies the key unless YAML selects another variable. Local OpenAI-compatible servers and remote HTTPS providers are supported. HTTP is allowed only on loopback; credentials/query parameters in provider URLs are rejected. Model redirects are not followed with transcript data.

The default polishing window is 2400 characters. A failed block can split twice, so only the difficult portion returns to roughly 1200 and then 600 characters before raw fallback. `structure.max_batch_chars` defaults to 100000, allowing an ordinary course to receive one global semantic outline; later batches for exceptionally long text receive recent accepted headings as context. Reduce these limits for endpoints with smaller context windows. `llm.max_workers` continues to control polishing concurrency independently.

`run <video> --dry-run` reveals effective endpoint/modes without exposing keys, writing files or networking. `doctor --offline --json` checks local dependencies. Online doctor also contacts Bilibili and, in polishing mode with a configured model, makes a short model request. Raw mode never calls the LLM, including online doctor.

For restricted data use `run <video> --text-mode raw --images none --resume --strict --json`. This does not repair ASR errors. To include reference pictures omit `--images none`. OCR is optional in representative mode; enable `slides.representative_ocr` only when text matching is useful. `ocr_max_side` controls analysis size; output remains full-frame.

## Dependencies

`python <skill-dir>/scripts/setup.py` installs the base runtime in the skill's `.venv`; `--check` only inspects it. `--asr` adds local speech recognition, `--ocr` adds RapidOCR. `--offline` disables package-index access; missing build/dependency packages will fail explicitly. Setup does not install FFmpeg or log in.

Install FFmpeg separately on PATH, or configure its full executable path with `ffmpeg`. Avoid multiple OpenCV variants in one environment: they share `cv2`. Minimal Linux may require `libgl1` and `libglib2.0-0`. Windows can also discover a standard WinGet Gyan.FFmpeg installation.

For local ASR, `asr.model` and `asr.vad_model` can point to existing model directories to avoid first-run downloads. `asr.engine: disabled` rejects videos without available subtitles. Bilibili subtitles are tried first.

## Failures and output

- Missing model: configure one or select raw mode; do not invent a paid/cloud model.
- Login/permissions: use QR `login` when authorized; do not circumvent restrictions.
- HTTP 401/403/404: repair endpoint/model/credentials before retrying. Transient errors already have bounded backoff.
- Strict exit 2: the draft exists; report warnings and raw fallback instead of automatically rerunning everything.
- Derived artifacts: `--resume` validates signatures and rebuilds dependents. Upgrades can invalidate implementation fingerprints; use `--skip-download` for a complete cached video.
- Locked task: another process owns the output. Wait or use a different workspace, rather than deleting its lock.

Default outputs are `<workspace>/notes/<BV>/note.md`, `report.json`, `frames/` and private `.task/` evidence. Share only authorized outputs; `.task/` includes original subtitles and model input/output details.
