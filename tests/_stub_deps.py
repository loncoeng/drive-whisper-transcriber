"""外部ライブラリを実際に import せずに対象モジュールを読み込むためのスタブ。

`vm_drive_whisper` と `drive_folder_inventory` は faster-whisper と
google-api-python-client をモジュール冒頭で import する。faster-whisper は
ctranslate2 を通じて数百MBを引き込むため、純粋関数を検査するだけのために
入れたくない。`sys.modules` へ最小限の代役を差し込んでから対象を import する。

代役は「import が通ること」だけを保証する。検査対象の純粋関数はこれらに
触れないので、中身は不要。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, **attrs: object) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def install() -> None:
    """スタブを登録し、リポジトリ直下を import path に加える。冪等。"""
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

    if "faster_whisper" in sys.modules:
        return

    sys.modules["faster_whisper"] = _module("faster_whisper", WhisperModel=object)

    google = sys.modules.get("google") or _module("google")
    google_auth = _module("google.auth", default=lambda **_: (None, None))
    google.auth = google_auth
    sys.modules["google"] = google
    sys.modules["google.auth"] = google_auth

    sys.modules["googleapiclient"] = _module("googleapiclient")
    sys.modules["googleapiclient.discovery"] = _module(
        "googleapiclient.discovery", build=lambda *_, **__: None
    )
    sys.modules["googleapiclient.errors"] = _module(
        "googleapiclient.errors", HttpError=type("HttpError", (Exception,), {})
    )
    sys.modules["googleapiclient.http"] = _module(
        "googleapiclient.http",
        MediaIoBaseDownload=object,
        MediaInMemoryUpload=object,
    )
