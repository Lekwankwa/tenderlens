# Privacy notice

**Lekwankwa Corporation (Pty) Ltd** operates TenderLens. This notice explains what happens to a document you
upload. It is written in plain language, not legal language, and describes what the code actually does - see
`README.md` for how to check any of this yourself.

## Where documents are processed
TenderLens runs from one of two places, depending on how this instance is deployed:
- **Google Cloud Run, Johannesburg (`africa-south1`)** - the hosting this project packages for (`deploy.sh`,
  see README "Hosting on Google Cloud Run"). If you were given a Cloud Run URL, this is where your upload is
  processed.
- **Streamlit Community Cloud** - while the pilot is still running there instead, your upload is processed on
  Streamlit's own infrastructure, not Cloud Run. Streamlit Community Cloud's own hosting region and data
  handling are Streamlit's, not this project's - see Streamlit's own privacy documentation.

Either way, running TenderLens entirely on your own machine (`streamlit run app.py` locally, or self-hosting
with Ollama - see `OLLAMA.md`) keeps your files on that machine instead of a shared host.

## The AI provider (Complete check)
Running a **Complete check** sends page images and extracted text to whichever AI provider this deployment is
configured for (`MODEL_PROVIDER`), so handwriting, ticks, stamps and signatures can be read:

- **Meta's Model API** (`api.meta.ai`, model Muse Spark) - the default. One model reads both the buyer's
  documents and the scanned pages.
  **[CONFIRM: Meta's processing location and data-retention/training policy for Standard-mode Model API
  traffic, from Meta's own documentation.]** This deployment uses Standard mode, not Contributor mode -
  Contributor mode is cheaper but lets Meta train future models on what you send, which this project does
  not opt into.
- **OpenRouter** (`openrouter.ai`, model Llama 4 Scout) - the switchable fallback (`MODEL_PROVIDER=openrouter`).
  OpenRouter is a routing service: it forwards the request to an underlying model provider, which may not be
  OpenRouter itself.
  **[CONFIRM: OpenRouter's processing location and data-retention/training policy, from OpenRouter's own
  documentation.]**

Until either is confirmed, assume sending data to either provider may mean it is processed outside South
Africa.

A **Fast check** does not call any AI provider - it reads files with OCR and fixed rules only, and nothing
leaves the host.

## What is sent, what is kept, and for how long
- **Sent to the AI provider** (Complete check only): the page images of whatever you uploaded, and text
  extracted from them. The RFQ/Tender documents and response pack are both affected, since both can be read
  this way.
- **Kept on the host**: the files you upload, and the page images rendered from them, are written to a working
  directory on the host for the duration of the audit. They are deleted automatically after at most
  `TENDERLENS_PURGE_HOURS` (24 hours by default; the Cloud Run deployment in `deploy.sh` sets this to 2 hours).
  Nothing is kept beyond that window by design.
- **The report itself** (the HTML/JSON you download) is generated in your browser session and offered to you
  directly as a download - it is not written to server storage separately from the working files above.
- **Operational logging** (`tenderlens/check_log.py`) records only counts and booleans for monitoring usage and
  spend (which check mode ran, how many AI calls it made, whether it succeeded) - never document contents,
  filenames, bidder names or RFQ references.
- **Pilot access codes** are stored only as a one-way hash (SHA-256); the code itself is shown once and never
  stored.

## Who can see it
- Whoever operates the host this instance runs on (Lekwankwa Corporation (Pty) Ltd, for the Cloud Run and
  Streamlit Community Cloud deployments; you, if you self-host).
- Meta (or, under the OpenRouter fallback, OpenRouter and whichever upstream model provider it routes to),
  only when Complete check is used.
- No one else. TenderLens does not share documents with any other third party.

## Redaction
The "Hide IDs, tax numbers and signatures in page images" option blacks out IDs, tax numbers, bank numbers,
phone numbers, PINs, handwritten ID/tax fields, the director table and signatures in the **report's own
embedded images** before you download or share it. It reduces what's exposed in that report. It does **not**
change what was sent to the AI provider during a Complete check - that model needs to see the unredacted page to
read handwriting, IDs and signatures in the first place. Redaction is layered but not perfect: look at the
report before you share it further.

This notice makes no claim of "data sovereignty" or that personal data never leaves South Africa - see
"The AI provider" above.

## Handwriting is read by an AI model
Any tick, name, date, amount or other handwritten content reported by TenderLens was read by an AI vision
model during Complete check (or typed in by hand as a transcript). It can be misread. Nothing it reports is a
finding of fact - a person must confirm it against the original page before relying on it.

## Other providers (planned)
These are not yet part of TenderLens. This section will be filled in before any of them handle your data.
- **Supabase** - [ADD when used]
- **Paystack** - [ADD when used]
- **Email provider** - [ADD when used]

## Your rights, and using this responsibly
Upload only documents you have the right to upload and share for this purpose. TenderLens is a review aid, not
a verdict - see `README.md`.

## Contact
Privacy questions: contact **Lekwankwa Corporation (Pty) Ltd**. [CONFIRM: a contact email/address to publish
here.]

*Last updated: 2026-10-18.*
