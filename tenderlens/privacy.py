"""The pre-upload privacy notice and consent checkbox shown in the sidebar - see PRIVACY.md for the full text.
One wording, used only here, so the in-app notice never drifts from this file.
"""
from __future__ import annotations

CONSENT_LABEL = "I have the right to upload this document and I accept the privacy notice."

NOTICE_SHORT = (
    "Files you upload are processed on this app's host and deleted after a few hours. With Complete check, "
    "page images and extracted text are also sent to OpenRouter (openrouter.ai) to read handwriting, ticks and "
    "stamps - that may mean they are processed outside South Africa. Redaction hides some fields in the report's "
    "images; it does not change what was sent to OpenRouter. See the full privacy notice (PRIVACY.md) for what "
    "is sent, what is kept, and for how long."
)


def consent_required(ready: bool, consent: bool) -> bool:
    """Whether the run button should be enabled: every other readiness condition, AND the privacy checkbox."""
    return ready and consent
