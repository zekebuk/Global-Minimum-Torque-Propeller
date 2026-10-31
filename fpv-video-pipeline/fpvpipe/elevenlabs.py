"""Tiny ElevenLabs client (stdlib only): sound effects and music.

Docs: elevenlabs.io/docs/api-reference/text-to-sound-effects/convert and .../music/compose
  POST /v1/sound-generation  {text, duration_seconds 0.5-30, prompt_influence 0-1, loop, model_id}
  POST /v1/music             {prompt, music_length_ms 3000-600000, model_id, force_instrumental, seed}
Both return audio bytes in ?output_format (default mp3_44100_128 / auto). Auth: xi-api-key header.
API key comes from the ELEVENLABS_API_KEY environment variable, never from files in the repo.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API = "https://api.elevenlabs.io/v1"


class ElevenLabsError(RuntimeError):
    pass


def _key() -> str:
    k = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not k:
        raise ElevenLabsError("set ELEVENLABS_API_KEY (ElevenLabs > Developers > API keys)")
    return k


def _post(path: str, body: dict, output_format: str, timeout: float) -> bytes:
    req = urllib.request.Request(
        f"{API}{path}?output_format={output_format}",
        data=json.dumps(body).encode(),
        headers={"xi-api-key": _key(), "Content-Type": "application/json", "Accept": "audio/*"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise ElevenLabsError(f"HTTP {e.code} on {path}: {detail}") from e


def sound_effect_body(text: str, seconds: float | None, influence: float = 0.4, loop: bool = False,
                      model_id: str = "eleven_text_to_sound_v2") -> dict:
    body = {"text": text, "prompt_influence": influence, "loop": loop, "model_id": model_id}
    if seconds is not None:
        body["duration_seconds"] = round(min(30.0, max(0.5, seconds)), 2)
    return body


def sound_effect(text: str, seconds: float | None = None, influence: float = 0.4, loop: bool = False,
                 model_id: str = "eleven_text_to_sound_v2", output_format: str = "mp3_44100_192") -> bytes:
    return _post("/sound-generation", sound_effect_body(text, seconds, influence, loop, model_id), output_format, 120)


def music_body(prompt: str, seconds: float, model_id: str = "music_v1", instrumental: bool = True,
               seed: int | None = None) -> dict:
    body = {"prompt": prompt[:4100], "music_length_ms": int(min(600000, max(3000, seconds * 1000))),
            "model_id": model_id, "force_instrumental": instrumental}
    if seed is not None:
        body["seed"] = seed
    return body


def music(prompt: str, seconds: float, model_id: str = "music_v1", instrumental: bool = True,
          seed: int | None = None, output_format: str = "mp3_44100_192") -> bytes:
    return _post("/music", music_body(prompt, seconds, model_id, instrumental, seed), output_format, 600)
