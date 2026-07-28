"""3D AI Companion Core TTS-Modul  – nutzt edge-tts für Sprachsynthese, mit Lippensynchronisation
über Flask-SocketIO. GPT-SoVITS-Integration folgt später als Alternative."""

import asyncio
import os
import tempfile
import time

import edge_tts
import numpy as np
import sounddevice as sd
import soundfile as sf

_voice = "de-DE-SeraphinaMultilingualNeural"
_socketio = None  # wird von main.py per set_socketio() gesetzt


def set_socketio(socketio_instance):
    """Verknüpft dieses Modul mit der Flask-SocketIO-Instanz aus main.py,
    damit speak() Events direkt ans Frontend pushen kann."""
    global _socketio
    _socketio = socketio_instance


def init(voice: str | None = None):
    """Setzt die zu verwendende edge-tts-Stimme (optional, sonst bleibt der Standard)."""
    global _voice
    if voice:
        _voice = voice


async def _synthesize(text: str, voice: str, output_path: str):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def speak(text: str, voice: str | None = None):
    """Synthetisiert Text per edge-tts, spielt ihn ab und pusht währenddessen
    Lipsync-Werte + den Untertitel-Text per SocketIO ans Frontend. Blockiert bis
    die Wiedergabe fertig ist -- passend zum Worker-Thread-Pattern in aki_runtime.py."""
    voice = voice or _voice

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        asyncio.run(_synthesize(text, voice, tmp_path))

        data, sample_rate = sf.read(tmp_path, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)  # Stereo -> Mono, falls nötig

        duration_ms = int(len(data) / sample_rate * 1000)
        if _socketio:
            _socketio.emit("subtitle", {"text": text, "duration": duration_ms})

        sd.play(data, sample_rate)

        window_ms = 50
        window_size = max(int(sample_rate * window_ms / 1000), 1)

        for start in range(0, len(data), window_size):
            chunk = data[start:start + window_size]
            if len(chunk) == 0:
                continue
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            mouth_value = min(rms * 6.0, 1.0)
            if _socketio:
                _socketio.emit("mouth", {"value": mouth_value})
            time.sleep(window_ms / 1000)

        sd.wait()

    except Exception as e:
        print(f"❌ TTS-Fehler: {e}")
    finally:
        if _socketio:
            _socketio.emit("mouth_close", {})
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def check_sovits() -> bool:
    """Platzhalter für später -- GPT-SoVITS ist in V2 noch nicht integriert."""
    return False