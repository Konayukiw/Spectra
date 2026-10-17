# Spectra

**日本語** | [English](README.En.md)

音声クローン向けローカルTTSハーネス。

[![License: GPL-v3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](/LICENSE) 

## 主な機能

- 音声の生成には最低1つの参照音声クリップが必要です。
- [Hugging Face](https://hf.co) のリポジトリを指定して `%USERPROFILE%\.spectra\models` にモデルをダウンロードでき、Spectra がモデルアーキテクチャを自動検出します。ダウンロード済みのモデルが重複してダウンロードされることはありません。
- 各アーキテクチャは（`uv` に寄って）それぞれ独立したPython環境で実行されます。これにより依存関係の競合を防ぎ、モデル切り替え時には直前のプロセスで使用していたVRAM領域を解放します。
- シンプルなWebUI
- ボイスライブラリ: 参照クリップと対応する文字起こしを、任意の名前を付けて保存できます。
- 自動文字起こし: [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)（Apache-2.0、日本語を含む多言語対応・言語自動検出）を使用して、参照音声の文字起こしを自動補完します。
- 生成履歴: テイク、参照クリップ、設定が保存され、Studio 上でワンクリックで過去のセッションを復元できます。

## 対応エンジン

| エンジン | 言語 | 推奨モデル | 参照音声の文字起こし | ライセンス |
|---|---|---|---|---|
| Irodori-TTS | 日本語 | `Aratako/Irodori-TTS-v4.1-Small`, `Aratako/Irodori-TTS-v4-Large` | 不要 | コード: MIT [*](#注意事項) |
| Qwen3-TTS | 英語およびその他の非日本語 | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | 推奨（ICLモード時） | Apache-2.0 |
| VoxCPM | 英語およびその他の非日本語 | `openbmb/VoxCPM2` | 任意（continuationモードでの類似性向上） | Apache-2.0 |

日本語テキストの生成には Irodori-TTS を推奨します。Qwen3-TTS および VoxCPM は英語等向けとなっています。

Irodori-TTS では、テキスト入力欄の絵文字ボタンからモデルが対応している絵文字のパレットを開くことができます。絵文字をクリックするとカーソル位置に挿入されます。

Irodori-TTS は最大30秒程度までの発話を対象に学習されているため、長文を一度に読み上げさせると品質が破綻します。そのため Spectra では、発話時間が20秒を超えると予測されるテキストを文境界で自動分割し、同じ参照音声を用いて各パートを生成した上で、句読点に応じた適切なポーズを挟んで結合します。

### パフォーマンスの目安 (RTX 5070 12GB、短い1文、モデルロード済みの場合)

| モデル | 精度 | ピークVRAM | 音声長 / 生成時間 |
|---|---|---|---|
| Irodori-TTS v4.1 Small | FP32 | 4.5 GB | 5.4秒 / 1.9秒 |
| Irodori-TTS v4 Large | BF16 | 8.5 GB | 5.0秒 / 3.3秒 |
| Qwen3-TTS 1.7B Base | BF16 | 4.5 GB | 4.2秒 / 7.4秒 |
| VoxCPM2 | BF16 | 5.9 GB | 5.3秒 / 5.7秒 |

初回のワーカー起動やモデルの読み込みには、追加で10秒〜1分ほどかかる場合があります。

## 動作要件

- Windows 11 および CUDA 12.8 をサポートする NVIDIA GPU
- [uv](https://docs.astral.sh/uv/)
- ffmpeg 

## セットアップ

```bash
uv sync
```

各エンジンの個別環境は、WebUI の `Models > Engines > Install environment` からセットアップできます。手動でセットアップする場合は、以下のコマンドを実行してください:

```bash
uv sync --project engines/irodori
```

## 起動方法

`start.bat` をダブルクリックするか、以下のコマンドを実行します。ブラウザで `http://127.0.0.1:7433` が開きます。

```bash
uv run python -m spectra --open
```

オプション: `--host` (デフォルト: `127.0.0.1`), `--port` (デフォルト: `7433`)

`--host` にループバックアドレス以外（例: `0.0.0.0`）を指定した場合、Spectra はアクセストークン付きのURLを出力します。他のデバイスからは最初にそのURLを開く必要があり、以降はCookieによってサインイン状態が維持されます。同一PCから `127.0.0.1` 経由でアクセスする場合はトークン不要です。環境変数 `SPECTRA_TOKEN` を設定していない限り、起動ごとに新しいトークンが自動生成されます。

## 使い方

1. **Models** タブで Hugging Face リポジトリ（例: `hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base`）を入力し、**Check** をクリックして詳細やファイルサイズを確認後、**Download** をクリックします。
2. **Studio** タブでモデルを選択し、参照音声ファイルをドロップします。必要に応じて文字起こしを入力してください。文字起こし入力欄の横にある **Generate** をクリックすると、自動で文字起こしを実行できます（初回のみ **Models > Transcription > Install** でのセットアップが必要です。約4.7 GB）。
3. 生成したいテキストを入力して **Generate** をクリックします。生成結果は **History** セクションに保存されます。
4. 気に入った参照音声のセットは、**Save as voice** をクリックしてボイスライブラリに保存できます。

プライベートリポジトリや Gated リポジトリを使用する場合は、アプリケーション起動前に環境変数 `HF_TOKEN` を設定してください。

## データの保存先

Spectra はデータを `.spectra` ディレクトリに保存します。

| パス | 内容 |
|---|---|
| `models\<engine>\<owner>--<name>` | ダウンロードされたチェックポイント |
| `data\voices` | ボイスライブラリ |
| `data\history` | 生成されたテイクおよび設定 |
| `data\clips` | WAV形式に変換された参照クリップ |
| `data\cache` | Hugging Face キャッシュ、コーデックの重み、その他のキャッシュ |

モデルを再ダウンロードしたい場合は、先に **Models > Installed models** から該当モデルを削除してください。

## 注意事項

- Irodori-TTS には、アップストリームの実装による SilentCipher 電子透かし（デジタルウォーターマーク）が埋め込まれます。透かしなしでテイクを保存したい場合は、`Disable SilentCipher watermark` (Studio > Advanced) を有効にしてください。
- 音声をクローンする際は、必ず対象者本人の許諾を得てから行ってください。
- `Aratako/Irodori-TTS-v4-Large` はオープンウェイトであり MIT ライセンスが適用されますが、モデルの一部に [**Gemma Terms of Use**](https://ai.google.dev/gemma/terms) が適用されます。モデルを使用する前に規約をご確認ください。

## ライセンス

Spectra は **GPL (GNU General Public License v3.0)** の下で公開されているフリー＆オープンソースソフトウェアです。詳細は [LICENSE](/LICENSE) をご覧ください。