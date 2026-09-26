---
name: audio-transcribe
description: Transcribe speech in audio files to text with on-device Whisper. Use when the user says transcribe audio, speech to text, meeting transcription, voice memo to text, subtitles from audio, or needs words out of mp3/wav/m4a recordings without cloud APIs.
license: MIT
compatibility: opencode
metadata:
  version: 1.0.0
  deps: faster-whisper
---

# Audio Transcribe

On-device speech recognition. Nothing leaves the machine.

## Setup

```bash
pip install faster-whisper
```

The model downloads once on first run (`tiny` ~75MB, `base` ~150MB,
`small` ~500MB). Larger models are more accurate and slower.

## Commands

```bash
# Plain text to stdout (language auto-detected)
python3 scripts/transcribe.py meeting.m4a --model base

# Timestamped segments as JSON, or straight to a file
python3 scripts/transcribe.py voice.wav --json -o subs.json
python3 scripts/transcribe.py call.mp3 --lang en -o transcript.txt
```

## Rules

- `tiny` for triage, `base` default, `small`+ when accuracy matters.
- Noisy audio transcribes poorly; say so instead of presenting guesses
  as fact.
