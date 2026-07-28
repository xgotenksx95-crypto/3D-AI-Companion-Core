import os
import torch
import yaml
import datetime
import re
import threading
from time import sleep as python_sleep

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from process.aki_runtime_func import AiRuntime
from process.stt_func.stt import load_model as load_whisper, transcribe, set_speaking_state
from process.llm_func.llm import init as init_llm, get_response
from process.speech_normalization_func.speech_normalization import normalize_for_speech
from process.web_search_func.web_search import search_web
from process.tts_func.tts import speak, set_socketio
from process.memory_func.memory import (
    init as init_memory, data as memory,
    add_message, extract_from_text, extract_facts_llm, build_context, save, query_memory
)

# ==========================================
# FLASK & SOCKETIO SETUP
# ==========================================
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")
set_socketio(socketio)

# ==========================================
# CONFIG & PATHS (NEUTRAL)
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
MEMORY_PATH = os.path.join(BASE_DIR, "memory.db")

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

# Falls Name in Config fehlt, Fallback nutzen
ASSISTANT_NAME = cfg.get("character", {}).get("name", "Companion")

# ==========================================
# INITIALISIERUNG
# ==========================================
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"📦 System initialisiert auf: {device}")

init_memory(MEMORY_PATH, cfg["memory"])
init_llm(cfg["llm"]["model_path"], cfg["llm"])
load_whisper(cfg["stt"]["model"], device)

current_mode = "normal"
chat_history = []

# =========================
# SYSTEM PROMPT (DYNAMISCH)
# =========================
def get_system_prompt() -> str:
    personality = cfg["character"].get("personality", "").strip()
    info_parts = []
    info_parts.append(f"Current date and time: {datetime.datetime.now().strftime('%A, %d.%m.%Y, %H:%M')}.")
    
    # Nutzerdaten dynamisch aus dem Speicher laden
    if memory.get("user_name"):
        info_parts.append(f"Der Name des Nutzers ist {memory['user_name']}.")
    if memory.get("favorite_anime"):
        info_parts.append(f"Sein Lieblingsanime ist {memory['favorite_anime']}.")
    if memory.get("interests"):
        info_parts.append(f"Seine Interessen: {', '.join(memory['interests'])}.")
        
    user_info = "\n".join(info_parts)
    context = build_context()
    return f"{personality}\n\n{user_info}\n{context}".strip()


# =========================
# AUDIO-AUSGABE / SPEECH
# =========================
def assistant_speak(text: str):
    reiner_text = re.sub(r'\*[^*]+\*', '', text)
    reiner_text = re.sub(r'\s+', ' ', reiner_text).strip()
    reiner_text = normalize_for_speech(reiner_text)

    if not reiner_text:
        return

    print(f"\n[{ASSISTANT_NAME}]: {reiner_text}")

    set_speaking_state(True)
    try:
        speak(reiner_text)
    finally:
        set_speaking_state(False)
        print("🎙️ Mikrofon wieder aktiv.")


# =========================
# TOOLS & FUNCTION CALLING
# =========================
def get_datetime() -> str:
    return datetime.datetime.now().strftime("%A, %d.%m.%Y, %H:%M")


ARG_PATTERN = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
TOOL_CALL_PATTERN = re.compile(r'TOOL:\s*(\w+)\((.*?)\)')

TOOLS = {
    "query_memory": query_memory,
    "search_web": search_web,
    "get_datetime": get_datetime,
}


def execute_tool_call(tool_name: str, args_str: str) -> str:
    if tool_name not in TOOLS:
        available = ", ".join(TOOLS.keys())
        return f"Unbekanntes Tool '{tool_name}'. Verfügbar: {available}."
    kwargs = dict(ARG_PATTERN.findall(args_str))
    try:
        return TOOLS[tool_name](**kwargs)
    except Exception as e:
        return f"Fehler beim Ausführen von {tool_name}: {e}"


def get_response_with_tools(history: list, max_iterations: int = 3) -> str:
    antwort = ""
    for _ in range(max_iterations):
        antwort = get_response(history)
        if not antwort:
            return antwort

        match = TOOL_CALL_PATTERN.search(antwort)
        if not match:
            return antwort

        tool_name, args_str = match.group(1), match.group(2)
        kommentar = antwort[:match.start()].strip()

        ergebnis = execute_tool_call(tool_name, args_str)
        print(f"[Tool]: {tool_name}({args_str}) → {ergebnis}")

        if kommentar:
            history.append({"role": "assistant", "content": kommentar})
        history.append({"role": "system", "content": f"Tool-Ergebnis ({tool_name}): {ergebnis}"})

    fallback = TOOL_CALL_PATTERN.sub("", antwort).strip()
    return fallback or "Ugh, I'm getting confused trying to figure that out. Ask me again?"


def verabschiedung():
    print(f"\n[{ASSISTANT_NAME}]: Goodbye! ❤️")
    assistant_speak("Goodbye! Take care of yourself.")
    print("👋 System wird sauber beendet...")
    os._exit(0)


# ==========================================
# ZENTRALER PROCESS-HANDLER
# ==========================================
def handle_incoming_message(user_text: str, source: str):
    """Verarbeitet Sprach- und Texteingaben sequenziell."""
    print(f"[{source}] Verarbeite: {user_text}")

    if any(w in user_text.lower() for w in cfg["exit_commands"]):
        verabschiedung()
        return

    chat_history[0]["content"] = get_system_prompt()
    extract_from_text(user_text, chat_history, get_system_prompt)
    chat_history.append({"role": "user", "content": user_text})

    # History auf die letzten N Nachrichten begrenzen (Index 0 System-Prompt bleibt)
    MAX_HISTORY = 20  
    if len(chat_history) > MAX_HISTORY + 1:
        chat_history[1:] = chat_history[-MAX_HISTORY:]

    response_text = get_response_with_tools(chat_history)
    if response_text:
        chat_history.append({"role": "assistant", "content": response_text})
        add_message("user", user_text)
        add_message("assistant", response_text)
        assistant_speak(response_text)
        extract_facts_llm(user_text)


# ==========================================
# OPTIMIERUNG: INTERFACE VIA WEBSOCKET EVENTS
# ==========================================
@socketio.on('set_mode')
def handle_set_mode(data):
    global current_mode
    current_mode = data.get("mode", "normal")
    chat_history[0]["content"] = get_system_prompt()
    print(f"[Mode] WebSocket-Wechsel zu: {current_mode}")

@socketio.on('agent_input')
def handle_agent_input(data):
    user_text = data.get("value", "").strip()
    if user_text:
        print(f"[Input] WebSocket-Texteingabe: {user_text}")
        runtime.send_message(user_text, source="text")


# ==========================================
# BACKGROUND THREADS
# ==========================================
def stt_loop():
    print("🎙️ STT-Spracherkennung aktiv...")
    while True:
        user_text = transcribe(cfg["stt"]["language"])
        if not user_text:
            continue
        print(f"[STT] Erkannt: {user_text}")
        runtime.send_message(user_text, source="stt")


if __name__ == "__main__":
    print(f"\n🌸 {ASSISTANT_NAME} Core-Engine startet via Flask-SocketIO Server!")

    runtime = AiRuntime()
    runtime.set_message_handler(handle_incoming_message)

    chat_history = [{"role": "system", "content": get_system_prompt()}]

    threading.Thread(target=stt_loop, daemon=True).start()

    socketio.run(app, host="127.0.0.1", port=8765, debug=False, use_reloader=False)
