import threading
import queue
import time

from process.tts_func.tts import speak as kokoro_speak

# =========================
# AI RUNTIME CORE
# =========================

class AiRuntime:
    def __init__(self):
        self.job_queue = queue.Queue()
        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.running = True

        # Interrupt flag (für neue Speech)
        self.current_job_id = 0
        self.lock = threading.Lock()

        # Callback für die zentrale Nachrichten-Verarbeitung (main.py)
        self._message_handler = None

        self.worker_thread.start()

    # =========================
    # PUBLIC API
    # =========================

    def set_message_handler(self, handler):
        """Registriert die Funktion, die für jede eingehende Nutzer-Nachricht aufgerufen wird.
        Signatur: handler(text: str, source: str) -> None"""
        self._message_handler = handler

    def send_message(self, text: str, source: str = "stt"):
        """Fügt eine neue Nutzer-Nachricht (aus Mikrofon ODER Text-Eingabe) zur Verarbeitung hinzu.
        Beide Quellen landen in derselben Queue und werden vom selben Worker nacheinander
        abgearbeitet -- dadurch kann nie mehr als eine Nachricht gleichzeitig chat_history
        oder das LLM anfassen, egal wie schnell gesprochen oder getippt wird."""
        self.job_queue.put({
            "type": "message",
            "text": text,
            "source": source,
        })

    def speak(self, text: str, voice: str = "af_heart"):
        """Fügt einen neuen Speech Job hinzu."""
        with self.lock:
            self.current_job_id += 1
            job_id = self.current_job_id

        self.job_queue.put({
            "type": "tts",
            "text": text,
            "voice": voice,
            "id": job_id
        })

    def stop(self):
        self.running = False

    # =========================
    # WORKER LOOP
    # =========================

    def _worker(self):
        while self.running:
            try:
                job = self.job_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if job["type"] == "tts":
                self._handle_tts(job)
            elif job["type"] == "message":
                self._handle_message(job)

    # =========================
    # MESSAGE HANDLER (Mikrofon + Text-Eingabe)
    # =========================

    def _handle_message(self, job):
        if not self._message_handler:
            print("⚠️ Kein message_handler registriert -- Nachricht verworfen.")
            return
        try:
            self._message_handler(job["text"], job["source"])
        except Exception as e:
            import traceback
            traceback.print_exc()  # Zeigt die exakte Fehlerstelle im Stack Trace
            print(f"❌ Fehler bei Nachrichten-Verarbeitung ({job['source']}): {e}")

    # =========================
    # TTS HANDLER
    # =========================

    def _handle_tts(self, job):
        job_id = job["id"]

        # Kleine Verzögerung erlaubt "cancel overwrite" bei schnellen Folge-Jobs
        time.sleep(0.01)

        # Wenn ein neuerer Job reingekommen ist → alten überspringen
        if job_id != self.current_job_id:
            return

        try:
            # NEUTRALISIERT: "Aki spricht" zu "Assistant speaks" geändert
            print(f"🔊 Assistant speaks: {job['text']}")
            kokoro_speak(job["text"], job["voice"])

        except Exception as e:
            print(f"❌ TTS Fehler: {e}")
