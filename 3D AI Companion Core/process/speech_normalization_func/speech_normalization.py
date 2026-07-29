"""3D-AI-Companion-Core Speech-Normalisierung – wandelt Zahlen in Text in ausgeschriebene Wörter um,
damit TTS-Engines sie natürlich aussprechen, statt Ziffer für Ziffer vorzulesen."""

import re

from num2words import num2words

_NUMBER_PATTERN = re.compile(r"-?\d+(?:[.,]\d+)?")


def normalize_for_speech(text: str, lang: str = "en") -> str:
    """Ersetzt alle Zahlen in 'text' durch ihre ausgeschriebene Wortform.
    lang="en" passend zu Akis Antworten (immer Englisch), bei Bedarf z.B. "de" übergeben."""

    def _replace(match: re.Match) -> str:
        raw = match.group(0)
        normalized = raw.replace(",", ".")
        try:
            if "." in normalized:
                return num2words(float(normalized), lang=lang)
            return num2words(int(normalized), lang=lang)
        except (ValueError, NotImplementedError):
            return raw  # im Zweifel unverändert lassen, statt abzustürzen

    return _NUMBER_PATTERN.sub(_replace, text)
