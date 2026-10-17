# Spectra

[日本語](README.md) | **English**

Local TTS harness for voice cloning.

[![License: GPL-v3.0](https://img.shields.io/badge/License-GPLv3-blue.svg)](/LICENSE) 

## Features

- Generation requires at least one reference audio clip.
- Add [Hugging Face](https://hf.co) repo to download models into `%USERPROFILE%\.spectra\models` and Spectra will automatically detect model architecture. Models that are already downloaded are never downloaded twice.
- Each architecture runs in its own isolated Python environment (via `uv`). This prevents dependency conflicts, and switching models ensures that the VRAM space which was used by previous process will be fully released.
- Simple WebUI
- Voice Library: Save reference clips and their corresponding transcriptions with custom names.
- Auto Transcript: Fill in reference transcripts with [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) (Apache-2.0, multilingual including Japanese, language is detected automatically).
- Generation History: Stores takes, reference clips, and settings, allowing you to restore a previous session in the Studio with a single click.
- Reading Conversion: For Irodori-TTS, English words and acronyms in Japanese text are converted to katakana with [kanalizer](https://github.com/VOICEVOX/kanalizer) (MIT) and a built-in dictionary. You can add your own readings, and an optional context model, [d1-omni-600M](https://huggingface.co/LiquidAI/d1-omni-600M), picks readings that depend on the sentence.

## Supported Engines

| Engine | Language | Recommended Models | Reference Transcription | License |
|---|---|---|---|---|
| Irodori-TTS | Japanese | `Aratako/Irodori-TTS-v4.1-Small`, `Aratako/Irodori-TTS-v4-Large` | Not required | Code: MIT [*](#notes) |
| Qwen3-TTS | English and other non-Japanese languages | `Qwen/Qwen3-TTS-12Hz-1.7B-Base` | Recommended (For ICL mode) | Apache-2.0 |
| VoxCPM | English and other non-Japanese languages | `openbmb/VoxCPM2` | Optional (Improves similarity in "continuation" mode) | Apache-2.0 |

Japanese text should be generated with Irodori-TTS. Qwen3-TTS and VoxCPM are kept for English.

For Irodori-TTS, the text box has an emoji button that opens a palette of the emoji the model understands. Click an emoji to insert it at the cursor.

Irodori-TTS was trained on utterances of up to 30 seconds and breaks down when it reads longer text in one pass. Spectra therefore splits text that is predicted to run longer than 20 seconds at sentence boundaries, generates each part with the same reference, and joins the parts with pauses that follow the punctuation.

### Reading Conversion

Irodori-TTS reads text through an LLM tokenizer without a pronunciation step, and it was trained on Japanese text, so English mixed into a sentence is often garbled. Spectra converts that English to katakana before generation, the way a Japanese speaker would say it.

- `Graphical User Interface` becomes グラフィカルユーザーインターフェース, `GUI` becomes ジーユーアイ and `button` becomes ボタン.
- Your dictionary comes first, then a built-in dictionary of common words, brands and acronyms, then kanalizer for every other word. Acronyms are spelled out letter by letter.
- The Reading box under the text shows the result. Click any word to pick another reading, type your own, or save it to the dictionary. Manage the dictionary in **Models > Reading**. It also accepts Japanese words, for example 神戸 → こうべ.
- Words whose reading depends on the sentence, such as `sake` (サケ or セイク), `live` (ライブ or リブ) and `No.` (ナンバー or ノー), are decided by the d1-omni-600M context model. It runs on the CPU, so the TTS model keeps all of the VRAM. Set it up once in **Models > Reading > Install** (about 2.5 GB). It was right in about 4 out of 5 cases in our tests, so check the words highlighted in yellow. Without it, the most common reading is used.
- Turn the feature off with `Reading conversion` (Studio > Settings > Advanced). History keeps both the original text and the text that was read.

### Performance Reference (RTX 5070 12GB, short single sentence, model already loaded)

| Model | Precision | Peak VRAM | Audio Duration / Generation Time |
|---|---|---|---|
| Irodori-TTS v4.1 Small | FP32 | 4.5 GB | 5.4 s / 1.9 s |
| Irodori-TTS v4 Large | BF16 | 8.5 GB | 5.0 s / 3.3 s |
| Qwen3-TTS 1.7B Base | BF16 | 4.5 GB | 4.2 s / 7.4 s |
| VoxCPM2 | BF16 | 5.9 GB | 5.3 s / 5.7 s |

Initial worker startup and model loading may take an additional 10 seconds to 1 minute.

## Requirements

- Windows 11 and an NVIDIA GPU with CUDA 12.8 support
- [uv](https://docs.astral.sh/uv/)
- ffmpeg 

## Setup

```bash
uv sync
```

Environments for individual engines can be set up via the WebUI under the `Models > Engines > Install environment.` To set them up manually, use the following:

```bash
uv sync --project engines/irodori
```

## Launch

Double-click `start.bat` or execute the following command: This will open `http://127.0.0.1:7433` in your browser.

```bash
uv run python -m spectra --open
```

Options: `--host` (Default: `127.0.0.1`), `--port` (Default: `7433`)

When `--host` is not a loopback address (for example `0.0.0.0`), Spectra prints a URL with an access token. Other devices must open that URL once, after which a cookie keeps them signed in. Requests from the same PC through `127.0.0.1` need no token. A new token is generated on every launch unless you set the `SPECTRA_TOKEN` environment variable.

## Usage

1. In the **Models** tab, add Hugging Face repo (Ex: `hf.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base`), click **Check** to verify its details and size, then click **Download**.
2. In the **Studio** tab, select a model, drop in a reference audio file, and provide a transcript if necessary. Click **Generate** next to the transcript box to transcribe the clip automatically. This needs a one-time setup in **Models > Transcription > Install** (about 4.7 GB).
3. Enter your text and click **Generate**. The results will be saved in the **History** section.
4. You can save any reference set you like to your voice library by selecting **Save as voice**.

If you are using private or gated repositories, setting the `HF_TOKEN` environment variable before launching the application is required. 

## Storage

Spectra keeps datas in `.spectra`.

| Path | Contents |
|---|---|
| `models\<engine>\<owner>--<name>` | Downloaded checkpoints |
| `data\voices` | Voice library |
| `data\history` | Generated takes and their settings |
| `data\clips` | Reference clips converted to WAV |
| `data\cache` | Hugging Face cache, codec weights and other caches |
| `models\reading\LiquidAI--d1-omni-600M` | Reading context model |
| `data\reading_dictionary.json` | Your reading dictionary |

To download a model again, delete it in **Models > Installed models** first.

Set the `SPECTRA_HOME` environment variable to keep everything in another folder instead of `%USERPROFILE%\.spectra`.

## Notes

- Irodori-TTS embeds the SilentCipher digital watermark used by the upstream implementation. Turn on `Disable SilentCipher watermark` (Studio > Advanced) to save takes without it.
- Please ensure you have a right from the individual before cloning their voice.
- `Aratako/Irodori-TTS-v4-Large` is open weight and licensed under MIT, but [**Gemma Terms of Use**](https://ai.google.dev/gemma/terms) is applied for the part of the model. Read the docs before using the model.
- `LiquidAI/d1-omni-600M` is licensed under the [**LFM Open License v1.0**](https://huggingface.co/LiquidAI/d1-omni-600M/blob/main/LICENSE). Commercial use is only allowed for organizations with less than 10 million USD in annual revenue. Read the license before using the model.

## License

Spectra is free and open source software licensed under **GPL (GNU Public License v3.0)**. Read [LICENSE](/LICENSE) for details.
