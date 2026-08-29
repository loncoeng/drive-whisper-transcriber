#!/usr/bin/env python3
"""Count media files by sibling-file rules using Drive metadata only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import google.auth
from googleapiclient.discovery import build


FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_MIME = "application/vnd.google-apps.document"
MEDIA_EXTENSIONS = {
    ".mp3", ".mp4", ".m4a", ".mpeg", ".mpga", ".wav", ".webm",
    ".mov", ".aac", ".flac", ".ogg", ".wma",
}


def quote(value: str) -> str:
    return value.replace("'", "\\'")


def list_children(drive, parent_id: str) -> list[dict]:
    items = []
    page_token = None
    while True:
        response = drive.files().list(
            q=f"'{quote(parent_id)}' in parents and trashed = false",
            fields=(
                "nextPageToken,files(id,name,mimeType,size,"
                "videoMediaMetadata(durationMillis))"
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


def find_folder(drive, parent_id: str, name: str) -> dict | None:
    return next((item for item in list_children(drive, parent_id)
                 if item["mimeType"] == FOLDER_MIME and item["name"] == name), None)


def walk(drive, root_id: str):
    stack = [root_id]
    while stack:
        folder_id = stack.pop()
        children = list_children(drive, folder_id)
        yield children
        stack.extend(item["id"] for item in children if item["mimeType"] == FOLDER_MIME)


def duration_ms(item: dict) -> int | None:
    value = item.get("videoMediaMetadata", {}).get("durationMillis")
    return int(value) if value is not None else None


def summarize(items: list[dict]) -> dict:
    durations = [value for item in items if (value := duration_ms(item)) is not None]
    return {
        "files": len(items),
        "duration_known": len(durations),
        "duration_unknown": len(items) - len(durations),
        "known_total_hours": round(sum(durations) / 3_600_000, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-folder-id", required=True)
    parser.add_argument("--years", nargs="+", default=["2023年", "2024年", "2025年"])
    args = parser.parse_args()

    credentials, _ = google.auth.default()
    drive = build("drive", "v3", credentials=credentials)
    all_media = []
    no_docs_or_txt = []
    media_only = []
    folders_with_media = 0
    folders_no_docs_or_txt = 0
    folders_media_only = 0

    for year in args.years:
        year_folder = find_folder(drive, args.root_folder_id, year)
        if not year_folder:
            continue
        for children in walk(drive, year_folder["id"]):
            files = [item for item in children if item["mimeType"] != FOLDER_MIME]
            media = [item for item in files
                     if Path(item["name"]).suffix.lower() in MEDIA_EXTENSIONS]
            if not media:
                continue
            folders_with_media += 1
            all_media.extend(media)
            has_doc_or_txt = any(
                item["mimeType"] == DOC_MIME or Path(item["name"]).suffix.lower() == ".txt"
                for item in files
            )
            if not has_doc_or_txt:
                folders_no_docs_or_txt += 1
                no_docs_or_txt.extend(media)
            if len(files) == len(media):
                folders_media_only += 1
                media_only.extend(media)

    print(json.dumps({
        "all_media": summarize(all_media),
        "no_google_docs_or_txt": summarize(no_docs_or_txt),
        "strictly_media_only": summarize(media_only),
        "folders_with_media": folders_with_media,
        "folders_no_google_docs_or_txt": folders_no_docs_or_txt,
        "folders_strictly_media_only": folders_media_only,
        "downloaded_files": 0,
    }, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


