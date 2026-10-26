"""The pre-upload privacy notice and consent checkbox shown in the sidebar - see PRIVACY.md for the full text.
One wording, used only here, so the in-app notice never drifts from this file.
"""
from __future__ import annotations

from .llm import provider_name

CONSENT_LABEL = "I have the right to upload this document and I accept the privacy notice."

# Name and host of whichever provider is reading page images for Complete check - must track llm.py's
# MODEL_PROVIDER switch, since a privacy notice naming the wrong processor would be worse than none.
_PROVIDER_TEXT = {
    "meta": "Meta's Model API (api.meta.ai)",
    "openrouter": "OpenRouter (openrouter.ai)",
}


def notice_short(provider: str | None = None) -> str:
    """provider defaults to the currently configured MODEL_PROVIDER; pass one explicitly in a test."""
    who = _PROVIDER_TEXT.get(provider or provider_name(), (provider or provider_name()))
    return (
        f"Files you upload are processed on this app's host and deleted after a few hours. With Complete "
        f"check, page images and extracted text are also sent to {who} to read handwriting, ticks and "
        f"stamps - that may mean they are processed outside South Africa. Redaction hides some fields in "
        f"the report's images; it does not change what was sent to {who.split(' (')[0]}. See the full "
        f"privacy notice (PRIVACY.md) for what is sent, what is kept, and for how long."
    )


def consent_required(ready: bool, consent: bool) -> bool:
    """Whether the run button should be enabled: every other readiness condition, AND the privacy checkbox."""
    return ready and consent
