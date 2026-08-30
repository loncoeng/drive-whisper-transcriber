# drive-whisper-transcriber

[![check](https://github.com/lon-coeng/drive-whisper-transcriber/actions/workflows/check.yml/badge.svg)](https://github.com/lon-coeng/drive-whisper-transcriber/actions/workflows/check.yml)

*[日本語版 / Japanese version](README.ja.md)*

Transcribes video and audio in Google Drive with Whisper on a Google Compute
Engine VM, and writes the result back into the source folder as a Google Doc.

It is built around one goal: get through **hundreds of files on an 8 GB VM,
unattended, even though the process will die partway.**

> Note: the code comments and the primary README are in Japanese, matching the
> deployment this was built for. This page covers the design.

---

## Design decisions

**Assume it will crash.** Whisper medium runs out of memory on an 8 GB VM. Swap is
made persistent, and `set -e` means an abnormal exit does *not* stop the VM —
systemd retries 30 seconds later. The VM shuts down **only** when every file
completed cleanly. "Finished, so stop" and "crashed, so it stopped" must not look
alike.

**Split recovery by failure type.** A dead process and a transient API failure need
different remedies, so they get different owners.

| Failure | Who recovers |
|---|---|
| Process died (OOM etc.) | VM stays up; systemd re-runs after 30s |
| Transient Google API error was recorded | The script re-scans Drive after 60s and retries only what failed |
| Finished with zero errors | VM shuts itself down |

**Make retrying cheap.** Transcripts are written to a cache, so resuming after an
interruption does not recompute them. One failure does not stop the batch; it is
logged and the run moves on. The cache entry is removed once the document has been
created successfully — it is a crash-recovery cache, not a permanent one.

**Use folder state for idempotency.** A folder that already contains non-media
files is treated as processed and skipped. The state of the folder itself is the
signal, so no external bookkeeping is required.

**Fix misrecognition downstream.** Whisper reliably mangles proper nouns. The file
name is passed as a recognition hint, and known misrecognitions are corrected
through a replacement table (`TERM_REPLACEMENTS`) before the document is created.

**Always be able to count first.** `--dry-run`, plus `drive_folder_inventory.py`
which totals file counts and durations from metadata alone without fetching any
media, exist so that cost and runtime can be estimated before committing to a run.

## What it does

- Walks a folder tree through the Drive API (shared drives supported)
- Skips folders containing non-media files, to avoid reprocessing
- Japanese transcription with the `faster-whisper` medium model
- Uses the file name and domain terms as recognition hints
- Corrects known misrecognitions of proper nouns before writing
- Creates a timestamped Google Doc in the source folder
- Optionally requests ownership transfer of the created document
- Logs errors without stopping the batch
- Caches transcripts and resumes after interruption
- Persists swap for memory headroom
- Auto-restarts on abnormal exit (30s)
- Re-scans and retries when transient network errors remain
- Shuts the VM down once everything completes
- Summarises metadata without downloading file contents

## Security

This repository stores no OAuth tokens, client secrets, Drive folder IDs, real
email addresses, run logs, media, or transcript text. Production values are passed
in as protected configuration on the VM.

## Usage

```bash
python3 -m venv ~/drive-whisper-venv
source ~/drive-whisper-venv/bin/activate
pip install -r requirements.txt

python vm_drive_whisper.py \
  --root-folder-id "$DRIVE_ROOT_FOLDER_ID" \
  --model medium \
  --limit 0 \
  --transfer-owner-to "$TRANSFER_OWNER_TO"
```

Count the work first:

```bash
python vm_drive_whisper.py --root-folder-id "$DRIVE_ROOT_FOLDER_ID" --dry-run --limit 20
python drive_folder_inventory.py --root-folder-id "$DRIVE_ROOT_FOLDER_ID"
```

## Tests

```sh
python -m unittest discover -s tests -t tests
```

No dependency install required. The tests cover pure functions that never touch an
external service — Drive query escaping, matching an existing document,
transcript formatting and misrecognition correction, and count/duration
aggregation. `faster-whisper` and `google-api-python-client` are replaced with
stubs at import time, because `faster-whisper` pulls in hundreds of megabytes
through ctranslate2 and that is not worth paying to test pure functions.

The emphasis is on **idempotency**: if matching an existing document breaks, the
same audio gets transcribed twice, which costs both money and VM hours.

## License

MIT. See [LICENSE](LICENSE).

A sanitised, public edition of a system still running in production, published
with the client's permission.
