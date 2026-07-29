"""3D-AI-Companion-Core Web-Search-Tool – nutzt DuckDuckGo (ddgs), kein API-Key nötig."""

from ddgs import DDGS


def search_web(query: str, max_results: int = 3) -> str:
    """Tool-Funktion: durchsucht das Web und gibt eine Kurzfassung der Treffer zurück."""
    try:
        results = DDGS().text(query, max_results=max_results)
    except Exception as e:
        return f"Websuche fehlgeschlagen: {e}"

    if not results:
        return "Keine Ergebnisse gefunden."

    lines = []
    for r in results:
        title = r.get("title", "")
        url = r.get("href") or r.get("url", "")
        body = r.get("body", "")
        lines.append(f"{title} ({url}): {body}")

    return "\n".join(lines)
