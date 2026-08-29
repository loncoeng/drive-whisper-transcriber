#!/usr/bin/env python3
"""Transcribe missing media in a specific Google Drive folder tree."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path

from faster_whisper import WhisperModel
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
MEDIA_EXTENSIONS = {
    ".mp3", ".mp4", ".m4a", ".mpeg", ".mpga", ".wav", ".webm",
    ".mov", ".aac", ".flac", ".ogg", ".wma",
}

TERM_REPLACEMENTS = (
    ("トレロー", "Trello"),
    ("トレロ", "Trello"),
    ("スラップ", "Slack"),
    ("スラッグ", "Slack"),
    ("ローム部", "労務部"),
    ("ローム", "労務"),
    ("シャローシ", "社労士"),
    ("者労使", "社労士"),
    ("車両市", "社労士"),
    ("車両子", "社労士"),
)


def q(value: str) -> str:
    return value.replace("'", "\\'")


def load_credentials():
    """Use the service account attached to the Compute Engine VM."""
    creds, _ = google.auth.default()
    return creds


def list_children(drive, parent_id: str) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        response = drive.files().list(
            q=f"'{q(parent_id)}' in parents and trashed = false",
            fields=(
                "nextPageToken,files(id,name,mimeType,appProperties,parents,size,"
                "videoMediaMetadata(durationMillis,width,height))"
            ),
            pageSize=1000,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        items.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return items


def find_named_folder(drive, parent_id: str, name: str) -> dict | None:
    for item in list_children(drive, parent_id):
        if item["mimeType"] == FOLDER_MIME and item["name"] == name:
            return item
    return None


def walk_folders(drive, root_id: str):
    stack = [(root_id, "")]
    while stack:
        folder_id, relative_path = stack.pop()
        children = list_children(drive, folder_id)
        yield folder_id, relative_path, children
        for child in reversed(children):
            if child["mimeType"] == FOLDER_MIME:
                child_path = f"{relative_path}/{child['name']}".lstrip("/")
                stack.append((child["id"], child_path))


def matching_document(media: dict, children: list[dict], doc_prefix: str) -> dict | None:
    expected = f"{doc_prefix}{Path(media['name']).stem}"
    for child in children:
        if child["mimeType"] != DOC_MIME:
            continue
        if child.get("appProperties", {}).get("sourceMediaFileId") == media["id"]:
            return child
        if child["name"] == expected:
            return child
    return None


def download_file(drive, file_id: str, destination: Path) -> None:
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with destination.open("wb") as output:
        downloader = MediaIoBaseDownload(output, request, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            status, done = downloader.next_chunk()
            if status:
                print(f"  download {status.progress() * 100:.0f}%", flush=True)


def format_transcript(media: dict, segments, info) -> str:
    lines = [
        f"文字起こし：{media['name']}",
        "",
        f"検出言語: {info.language}（確率 {info.language_probability:.2f}）",
        f"元ファイルID: {media['id']}",
        "",
    ]
    for segment in segments:
        minutes, seconds = divmod(int(segment.start), 60)
        hours, minutes = divmod(minutes, 60)
        stamp = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        text = segment.text.strip()
        if text:
            for source, target in TERM_REPLACEMENTS:
                text = text.replace(source, target)
            text = re.sub(r"(会社の){3,}", "会社の", text)
            lines.append(f"[{stamp}] {text}")
    return "\n".join(lines) + "\n"


def create_google_doc(
    drive, parent_id: str, media: dict, transcript: str, doc_prefix: str
) -> dict:
    metadata = {
        "name": f"{doc_prefix}{Path(media['name']).stem}",
        "mimeType": DOC_MIME,
        "parents": [parent_id],
        "appProperties": {
            "sourceMediaFileId": media["id"],
            "transcriber": "drive-whisper-worker",
        },
    }
    media_body = MediaInMemoryUpload(
        transcript.encode("utf-8"), mimetype="text/plain", resumable=False
    )
    return drive.files().create(
        body=metadata,
        media_body=media_body,
        fields="id,name,webViewLink",
        supportsAllDrives=True,
    ).execute()


def request_ownership_transfer(drive, file_id: str, new_owner_email: str) -> dict:
    """Request a consumer-account ownership transfer for one created document."""
    permissions = drive.permissions().list(
        fileId=file_id,
        fields="permissions(id,emailAddress,role,pendingOwner,permissionDetails)",
        supportsAllDrives=True,
    ).execute().get("permissions", [])
    existing = next(
        (p for p in permissions if p.get("emailAddress", "").lower() == new_owner_email.lower()),
        None,
    )
    body = {"type": "user", "role": "writer", "pendingOwner": True}
    if existing:
        try:
            return drive.permissions().update(
                fileId=file_id,
                permissionId=existing["id"],
                body={"role": "writer", "pendingOwner": True},
                fields="id,emailAddress,role,pendingOwner",
                supportsAllDrives=True,
            ).execute()
        except HttpError as error:
            if error.resp.status not in (400, 403):
                raise
    body["emailAddress"] = new_owner_email
    return drive.permissions().create(
        fileId=file_id,
        body=body,
        sendNotificationEmail=True,
        fields="id,emailAddress,role,pendingOwner",
        supportsAllDrives=True,
    ).execute()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-folder-id", required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--initial-prompt")
    parser.add_argument("--source-file-id")
    parser.add_argument("--doc-prefix", default="【文字起こし】")
    parser.add_argument("--years", nargs="+", default=["2023年", "2024年", "2025年"])
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--metadata-summary",
        action="store_true",
        help="Summarize Drive playback metadata without downloading media files.",
    )
    parser.add_argument("--transfer-owner-to")
    parser.add_argument(
        "--cache-dir", default=str(Path.home() / ".cache" / "drive-whisper" / "transcripts")
    )
    args = parser.parse_args()

    drive = build("drive", "v3", credentials=load_credentials())
    year_roots: list[tuple[str, str]] = []
    for year in args.years:
        folder = find_named_folder(drive, args.root_folder_id, year)
        if folder:
            year_roots.append((folder["id"], year))
        else:
            print(f"WARN: year folder not found: {year}", flush=True)

    candidates: list[tuple[str, str, dict]] = []
    for year_id, year in year_roots:
        for folder_id, relative, children in walk_folders(drive, year_id):
            folder_path = f"{year}/{relative}".rstrip("/")
            files = [item for item in children if item["mimeType"] != FOLDER_MIME]
            if not args.source_file_id:
                non_media = [
                    item for item in files
                    if Path(item["name"]).suffix.lower() not in MEDIA_EXTENSIONS
                ]
                if non_media:
                    media_count = sum(
                        Path(item["name"]).suffix.lower() in MEDIA_EXTENSIONS
                        for item in files
                    )
                    if media_count:
                        print(
                            f"SKIP folder with non-media files: {folder_path} "
                            f"media={media_count} other={len(non_media)}",
                            flush=True,
                        )
                    continue
            for item in children:
                if item["mimeType"] == FOLDER_MIME:
                    continue
                if Path(item["name"]).suffix.lower() not in MEDIA_EXTENSIONS:
                    continue
                if args.source_file_id and item["id"] != args.source_file_id:
                    continue
                if matching_document(item, children, args.doc_prefix):
                    print(f"SKIP existing: {folder_path}/{item['name']}", flush=True)
                    continue
                candidates.append((folder_id, folder_path, item))

    print(json.dumps({"missing_transcripts": len(candidates)}, ensure_ascii=False), flush=True)
    if args.metadata_summary:
        known_durations_ms = []
        unknown_duration = 0
        total_bytes = 0
        for _, _, item in candidates:
            duration = item.get("videoMediaMetadata", {}).get("durationMillis")
            if duration is None:
                unknown_duration += 1
            else:
                known_durations_ms.append(int(duration))
            total_bytes += int(item.get("size", 0))
        total_seconds = sum(known_durations_ms) / 1000
        print(json.dumps({
            "media_files": len(candidates),
            "duration_known": len(known_durations_ms),
            "duration_unknown": unknown_duration,
            "known_total_hours": round(total_seconds / 3600, 2),
            "known_average_minutes": round(
                total_seconds / 60 / len(known_durations_ms), 2
            ) if known_durations_ms else None,
            "total_size_gib": round(total_bytes / (1024 ** 3), 2),
            "downloaded_files": 0,
        }, ensure_ascii=False), flush=True)
        return 0
    if args.dry_run:
        for _, path, item in candidates[: max(args.limit, 20)]:
            print(f"WOULD PROCESS: {path}/{item['name']}", flush=True)
        return 0

    cache_dir = Path(args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = None
    completed = 0
    failed = 0
    for parent_id, folder_path, media in candidates:
        if args.limit > 0 and completed >= args.limit:
            break
        try:
            suffix = Path(media["name"]).suffix or ".media"
            cache_path = cache_dir / f"{media['id']}.txt"
            print(f"PROCESS: {folder_path}/{media['name']}", flush=True)
            started = time.monotonic()
            if cache_path.exists():
                transcript = cache_path.read_text(encoding="utf-8")
                print(f"  cache hit: {cache_path.name}", flush=True)
            else:
                if model is None:
                    model = WhisperModel(
                        args.model, device="cpu", compute_type="int8",
                        cpu_threads=4, num_workers=1,
                    )
                with tempfile.TemporaryDirectory(prefix="drive-whisper-") as temp_dir:
                    source = Path(temp_dir) / f"source{suffix}"
                    download_file(drive, media["id"], source)
                    segments, info = model.transcribe(
                        str(source), language="ja", beam_size=1, vad_filter=True,
                        condition_on_previous_text=False,
                        initial_prompt=(
                            f"{args.initial_prompt}。ファイル名: {media['name']}"
                            if args.initial_prompt else f"ファイル名: {media['name']}"
                        ),
                    )
                    transcript = format_transcript(media, segments, info)
                    cache_path.write_text(transcript, encoding="utf-8")
                    os.chmod(cache_path, 0o600)
                    print(f"  cached transcript: {cache_path.name}", flush=True)
            result = create_google_doc(
                drive, parent_id, media, transcript, args.doc_prefix
            )
            elapsed = time.monotonic() - started
            print(
                f"CREATED: {result['name']} "
                f"{result.get('webViewLink', result['id'])} ({elapsed:.0f}s)",
                flush=True,
            )
            if args.transfer_owner_to:
                try:
                    permission = request_ownership_transfer(
                        drive, result["id"], args.transfer_owner_to
                    )
                    print(
                        f"OWNERSHIP REQUESTED: {args.transfer_owner_to} "
                        f"pending={permission.get('pendingOwner', False)}",
                        flush=True,
                    )
                except Exception as error:
                    print(
                        f"OWNERSHIP ERROR: {result['id']} "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )
            cache_path.unlink(missing_ok=True)
            completed += 1
        except Exception as error:
            failed += 1
            print(
                f"ERROR: {folder_path}/{media['name']} "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

    print(json.dumps({"completed": completed, "failed": failed}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

