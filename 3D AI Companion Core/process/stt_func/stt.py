import os
import time
import torch
import whisper
import numpy as np
import sounddevice as sd

_model = None
_vad_model = None
_is_speaking = False

# Silero VAD benötigt exakt 16000 Hz Samplerate
SAMPLE_RATE = 16000
WINDOW_SIZE_SAMPLES = 512  # Verarbeitungsfenster (ca. 32ms)


def load_model(model_size: str, device: str):
    global _model, _vad_model
    print(f"⏳ Lade Whisper {model_size} Modell...")
    _model = whisper.load_model(model_size, device=device)

    print("⏳ Lade Silero VAD (Neuronale Spracherkennung)...")
    # Lädt das extrem treffsichere Silero-Modell direkt über torch.hub
    model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad',
                                  model='silero_vad',
                                  force_reload=False,
                                  trust_repo=True)
    _vad_model = model.to(device)
    print("✅ Whisper & Silero VAD erfolgreich auf der GPU geladen!")


def set_speaking_state(state: bool):
    global _is_speaking
    _is_speaking = state


def transcribe(language=None) -> str | None:
    """Überwacht das Razer-Mikrofon per Silero VAD und übergibt Sprache an Whisper."""
    while _is_speaking:
        time.sleep(0.1)

    audio_buffer = []
    vocal_detected = False
    triggered = False

    # Schwellenwert: Ab 0.5 Wahrscheinlichkeit gilt es als menschliche Stimme
    vad_threshold = 0.5

    # Wie viele leere Fenster warten wir ab, bevor wir das Sprechen als 'beendet' ansehen
    num_padding_chunks = 30
    padding_counter = 0

    print("\n🎤 [Silero VAD] Lausche aktiv... (Warte auf Stimme)")

    # Wir öffnen einen direkten Hardware-Stream für dein Razer Seiren Mini
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype='float32') as stream:
        while True:
            # Wenn Aki mitten in der Aufnahme anfängt zu sprechen, brechen wir sofort ab
            if _is_speaking:
                return None

            chunk, overflow = stream.read(WINDOW_SIZE_SAMPLES)
            chunk_array = chunk.flatten()

            if not triggered:
                audio_buffer.extend(chunk_array)
                # Buffer klein halten, solange niemand spricht (die letzten 0.5 Sekunden behalten)
                if len(audio_buffer) > SAMPLE_RATE * 0.5:
                    audio_buffer = audio_buffer[-int(SAMPLE_RATE * 0.5):]

                # VAD-Analyse des aktuellen Fensters
                # VORHER:
                # tensor_chunk = torch.from_numpy(chunk_array)
                # speech_prob = _vad_model(tensor_chunk, SAMPLE_RATE).item()

                # NACHHER (Mit GPU-Routing und Dimensionen):
                tensor_chunk = torch.from_numpy(chunk_array).unsqueeze(0).to(next(_vad_model.parameters()).device)
                speech_prob = _vad_model(tensor_chunk, SAMPLE_RATE).item()

                if speech_prob > vad_threshold:
                    print("🎙️ [VAD] Stimme erkannt! Nehme auf...")
                    triggered = True
                    padding_counter = 0
            else:
                audio_buffer.extend(chunk_array)
                tensor_chunk = torch.from_numpy(chunk_array).unsqueeze(0).to(next(_vad_model.parameters()).device)
                speech_prob = _vad_model(tensor_chunk, SAMPLE_RATE).item()

                if speech_prob < vad_threshold:
                    padding_counter += 1
                else:
                    padding_counter = 0  # Zurücksetzen, wenn du weitersprichst

                # Wenn du aufgehört hast zu sprechen (ca. 1 Sekunde Stille nach dem Reden)
                if padding_counter > num_padding_chunks:
                    print("🛑 [VAD] Ende der Sprache erkannt. Verarbeite mit Whisper...")
                    break

    # Audiodaten für Whisper vorbereiten (muss float32 sein)
    audio_data = np.array(audio_buffer, dtype=np.float32)

    # Sicherheitscheck: Wenn die Aufnahme zu kurz war, verwerfen
    if len(audio_data) < SAMPLE_RATE * 0.5:
        return None

    try:
        # Whisper transkribiert den VAD-Buffer direkt aus dem RAM (keine temporäre Datei mehr nötig!)
        result = _model.transcribe(
            audio_data,
            language="de",
            task="transcribe",
            fp16=torch.cuda.is_available()
        )

        text = result["text"].strip()

        # Whisper Halluzinations-Schutz bei Atmen/Rauschen
        if not text or len(text) < 2 or "untertitel" in text.lower() or "amara" in text.lower():
            return None

        print(f"[Du]: {text}")
        return text

    except Exception as e:
        print(f"❌ Whisper-Fehler: {e}")
        return None
