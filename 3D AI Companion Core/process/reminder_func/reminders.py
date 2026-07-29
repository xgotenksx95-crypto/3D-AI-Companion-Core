"""3D-AI-Companion-Core Reminder-Tool – plant Erinnerungen, die nach X Minuten unaufgefordert ausgesprochen werden."""

import threading

_speak_callback = None
_active_reminders = {}  # id -> {"timer": Timer, "message": str}
_next_id = 1
_lock = threading.Lock()


def set_speak_callback(callback):
    """Registriert die Funktion, die beim Fälligwerden einer Erinnerung aufgerufen wird: callback(text: str)."""
    global _speak_callback
    _speak_callback = callback


def set_reminder(message: str, minutes: str) -> str:
    """Tool-Funktion: plant eine Erinnerung, die nach 'minutes' Minuten ausgesprochen wird."""
    global _next_id
    try:
        delay_seconds = float(minutes) * 60
    except ValueError:
        return f"Ungültige Zeitangabe: '{minutes}'. Bitte eine Zahl in Minuten angeben."

    with _lock:
        reminder_id = _next_id
        _next_id += 1

    def fire():
        with _lock:
            _active_reminders.pop(reminder_id, None)
        if _speak_callback:
            _speak_callback(message)

    timer = threading.Timer(delay_seconds, fire)
    timer.daemon = True

    with _lock:
        _active_reminders[reminder_id] = {"timer": timer, "message": message}
    timer.start()

    return f"Erinnerung gestellt: in {minutes} Minuten wird gesagt: '{message}'"


def cancel_reminder(message: str = "") -> str:
    """Tool-Funktion: bricht eine oder mehrere aktive Erinnerungen ab.
    Ohne Angabe von 'message' werden alle aktiven Erinnerungen abgebrochen."""
    with _lock:
        if not _active_reminders:
            return "Es gibt aktuell keine aktiven Erinnerungen."

        if message:
            matches = [
                (rid, info) for rid, info in _active_reminders.items()
                if message.lower() in info["message"].lower()
            ]
        else:
            matches = list(_active_reminders.items())

        if not matches:
            return f"Keine aktive Erinnerung gefunden, die zu '{message}' passt."

        cancelled = []
        for rid, info in matches:
            info["timer"].cancel()
            del _active_reminders[rid]
            cancelled.append(info["message"])

    return f"Abgebrochen: {', '.join(cancelled)}"
