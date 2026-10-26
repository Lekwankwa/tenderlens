"""The pre-upload privacy notice and consent checkbox shown in the sidebar - see PRIVACY.md for the full text.
One wording, used only here, so the in-app notice never drifts from this file.
"""
from __future__ import annotations

CONSENT_LABEL = "I have the right to upload this document and I accept the privacy notice."

# TenderLens's only hosted model provider - see llm.py. Keep this in sync with that module: a privacy
# notice naming the wrong processor would be worse than none.
_PROVIDER_NAME = "Meta's Model API"
_PROVIDER_HOST = "api.meta.ai"


def notice_short() -> str:
    return (
        f"Files you upload are processed on this app's host and deleted after a few hours. With Complete "
        f"check, page images and extracted text are also sent to {_PROVIDER_NAME} ({_PROVIDER_HOST}) to "
        f"read handwriting, ticks and stamps - that may mean they are processed outside South Africa. "
        f"Redaction hides some fields in the report's images; it does not change what was sent to "
        f"{_PROVIDER_NAME}. See the full privacy notice (PRIVACY.md) for what is sent, what is kept, and "
        f"for how long."
    )


def consent_required(ready: bool, consent: bool) -> bool:
    """Whether the run button should be enabled: every other readiness condition, AND the privacy checkbox."""
    return ready and consent
