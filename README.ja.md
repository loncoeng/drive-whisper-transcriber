# drive-whisper-transcriber

[![check](https://github.com/lon-coeng/drive-whisper-transcriber/actions/workflows/check.yml/badge.svg)](https://github.com/lon-coeng/drive-whisper-transcriber/actions/workflows/check.yml)

*[English version](README.md)*

Google Drive 上の動画・音声を Google Compute Engine 上の Whisper で文字起こしし、
元フォルダへ Google ドキュメントとして書き戻すバッチ処理です。

数百件規模のメディアを、**8GB メモリの VM で、途中で落ちても人手を介さず完走させる**
ことを目的に設計しています。

## 設計上の判断

**落ちる前提で組む。** Whisper medium は 8GB VM では OOM で落ちます。スワップを永続化した上で、
`set -e` により異常終了時は VM を停止せず systemd に再試行させ、**全件正常完了したときだけ**
VM を停止します。「終わったから止める」と「落ちたから止まった」を取り違えない構造です。

**復旧の担当を失敗の種類で分ける。** プロセスが落ちたのか、API が一時的に失敗しただけなのかで、
必要な復旧手段は違います。前者は systemd の再起動、後者はスクリプト内での再走査に担当を分けています。

**やり直しを安くする。** 文字起こし結果はキャッシュへ書き、中断後の再開時に再計算しません。
1件の失敗で全体を止めず、エラーをログに残して次へ進みます。

**二重処理を条件で防ぐ。** 動画・音声以外のファイルが混在するフォルダは処理済みとみなして
対象外にします。フォルダの状態そのものを冪等性の判定に使う方式です。

**誤認識を後段で直す。** 固有名詞は Whisper が高い確率で取り違えます。ファイル名を認識ヒント
として渡した上で、既知の誤認識パターンを補正表 (`TERM_REPLACEMENTS`) でドキュメント生成前に
置換します。

**実行前に必ず数えられるようにする。** `--dry-run` と、ファイル本体を取得せずメタデータだけで
件数・再生時間を集計する `drive_folder_inventory.py` を用意しています。課金と実行時間を
事前に見積もるためです。

## 主な機能

- 指定フォルダ配下を Drive API で走査（共有ドライブ対応）
- 動画・音声以外のファイルがあるフォルダを対象外にして重複処理を防止
- `faster-whisper` の medium モデルによる日本語文字起こし
- ファイル名と専門用語を認識ヒントとして利用
- 固有名詞の表記揺れをドキュメント作成前に補正
- タイムスタンプ付き Google ドキュメントを元フォルダへ作成
- 作成文書の所有権移行申請（任意）
- 1件の失敗で全体を停止せず、エラーをログに記録
- 中断時の文字起こしキャッシュと再開
- メモリ不足対策用スワップの永続化
- 異常終了時の自動再開（30秒後）
- 一時的な通信エラーが残った場合の自動再走査とキャッシュ再利用
- 全件完了後の VM 自動停止
- ファイル本体を取得しないメタデータ集計

## セキュリティ

このリポジトリには、OAuthトークン、クライアントシークレット、DriveフォルダID、実在メールアドレス、実行ログ、動画・音声、文字起こし本文を保存しません。実運用値はVM側の保護された設定として渡してください。

## セットアップ概要

```bash
python3 -m venv ~/drive-whisper-venv
source ~/drive-whisper-venv/bin/activate
pip install -r requirements.txt
```

VMにDriveアクセス用の認証情報を安全に配置し、次のように実行します。

```bash
python vm_drive_whisper.py \
  --root-folder-id "$DRIVE_ROOT_FOLDER_ID" \
  --model medium \
  --initial-prompt "専門用語をカンマ区切りで指定" \
  --limit 0 \
  --transfer-owner-to "$TRANSFER_OWNER_TO"
```

対象確認のみの場合：

```bash
python vm_drive_whisper.py \
  --root-folder-id "$DRIVE_ROOT_FOLDER_ID" \
  --dry-run \
  --limit 20
```

フォルダ条件別の件数と再生時間を集計する場合：

```bash
python drive_folder_inventory.py \
  --root-folder-id "$DRIVE_ROOT_FOLDER_ID"
```

## 運用上の注意

- 自動文字起こしは下書きです。人名、組織名、専門用語は必要に応じて確認してください。
- 所有権移行は移行先アカウントでの承認が必要な場合があります。
- Google Cloudの予算アラートは通知機能であり、VMを自動停止する機能ではありません。
- 本番前に必ず`--dry-run`で対象件数と除外条件を確認してください。

## VMのメモリ不足対策

Whisper mediumを8GBメモリのVMで長時間実行する場合は、8GBのスワップを追加します。

```bash
sudo bash scripts/setup_swap.sh /swapfile 8G
```

このスクリプトは`/etc/fstab`と`/etc/sysctl.d/99-drive-whisper-swap.conf`も設定するため、VM再起動後も有効です。`vm.swappiness`は10に設定します。

## systemdによる自動復旧

サンプルを実環境に合わせて配置し、サービスを有効化します。

```bash
sudo cp systemd/drive-whisper.service.example /etc/systemd/system/drive-whisper-production.service
sudo systemctl daemon-reload
sudo systemctl enable --now drive-whisper-production.service
```

`scripts/run_production.sh.example` は `scripts/run_production_once.sh.example` を繰り返し実行します。
失敗の種類によって復旧の担当を分けているのがポイントです。

| 失敗の種類 | 誰が復旧するか |
|---|---|
| OOM などでプロセス自体が落ちた | VM を停止せず、systemd が30秒後に再実行 |
| Google API の一時的な通信エラーが記録された | スクリプトが60秒後に Drive を再走査し、失敗分だけ再試行 |
| エラー0件で完走した | VM を自動停止 |

文字起こし済みの音声はキャッシュを使うため、再試行しても同じ音声認識処理を繰り返しません。

状態確認：

```bash
systemctl status drive-whisper-production.service --no-pager
free -h
swapon --show
```

## テスト

```sh
python -m unittest discover -s tests -t tests
```

依存のインストールは要りません。検査対象は外部サービスに触れない純粋関数
（Drive クエリのエスケープ / 既存ドキュメントの照合 / 文字起こしの整形と
誤認識の補正 / 件数と再生時間の集計）で、faster-whisper と
google-api-python-client はスタブに差し替えて読み込みます。

重点は**冪等性**です。既存ドキュメントの照合が崩れると、同じ音声を
二重に文字起こしすることになり、課金と実行時間に直接響きます。

## ライセンス

MIT License. 詳細は [LICENSE](LICENSE) を参照してください。

委託元の承諾を得て、本番稼働中のシステムを匿名化した公開版です。
