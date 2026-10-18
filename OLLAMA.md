# Connecting Llama vision (Ollama on your laptop)

**This is the local alternative.** The default setup (`.env.example`, README's "Using Llama") is a hosted endpoint
(OpenRouter: Llama 4 Scout for both text and vision). Use this page instead if you want
everything to stay on your own machine - the trade-off is much slower runs on ordinary laptop hardware, and a
known Ollama bug (see "If something goes wrong" below). Llama 4 Scout is too large to run this way; the local path
below still uses Llama 3.2 Vision.

TenderLens works without a vision model, but then it cannot read ticks, handwritten names, dates, amounts or the
commissioner's stamp. With one connected it also looks for crossed-out text, overwriting, notes in the margin, rotated or
cut-off pages and signatures that look like they were written by different people.

Nothing leaves your laptop when you use Ollama: the page images are sent to `localhost`.

The quick way to do everything below automatically: `./install.sh` (or `install.ps1` on Windows), then
`python -m tenderlens ui`. It installs Ollama, starts it, pulls whichever model `.env` names, and opens the app - see the
README's Quick start. The manual steps are still here for when you want to understand or change what it is doing, or set
things up by hand.

## 1. Install the models

```
ollama pull llama3.2-vision     # reads the page images (about 8 GB download, needs roughly 8 GB of free RAM)
ollama pull llama3.2            # optional: reads the buyer's email and specification (about 2 GB)
ollama list                     # confirm both are there
```

Check the names against `ollama list`: Ollama's library changes, and the model name you set below must match exactly.

## 2. Tell TenderLens where they are

macOS / Linux:
```
export TENDERLENS_VISION_BASE_URL=http://localhost:11434/v1
export TENDERLENS_VISION_MODEL=llama3.2-vision
export TENDERLENS_LLM_BASE_URL=http://localhost:11434/v1     # optional, for the buyer's documents
export TENDERLENS_LLM_MODEL=llama3.2
export TENDERLENS_LLM_TIMEOUT=900                            # seconds per call; a CPU-only laptop is slow
```

Windows PowerShell (the HP):
```
$env:TENDERLENS_VISION_BASE_URL="http://localhost:11434/v1"
$env:TENDERLENS_VISION_MODEL="llama3.2-vision"
$env:TENDERLENS_LLM_BASE_URL="http://localhost:11434/v1"
$env:TENDERLENS_LLM_MODEL="llama3.2"
$env:TENDERLENS_LLM_TIMEOUT="900"
```

You can mix: for example the text model on Groq (free tier) and the vision model on Ollama. The vision settings are
separate (`TENDERLENS_VISION_*`); if you leave them out, the text model is used for pages as well, so it must be a vision
model.

## 3. Check it works, then score it

```
python -m tenderlens check-llm        # pings both models and has the vision model describe page 1
python -m tenderlens eval-vision      # reads the winning pack and compares with the transcript; writes out/eval_vision.md
```

`eval-vision` is the important one. The winning pack has a human transcription of every handwritten entry and no real
problems, so:

- entries the model reads differently from the transcript are **misreadings**;
- any Yes, blank, double tick, or "crossed out" it reports is a **false alarm**.

Read `out/eval_vision.md` before trusting the model on a real pack. If it gets fewer than about 9 in 10 entries right, use a
transcript for ticks and dates, or a larger vision model. If it confirms false alarms about crossed-out text, turn that
option off (`--vision-inspect off`, or the toggle in the app).

## 4. Run

```
python -m tenderlens run --rfq ... --spec ... --pack ... --blank samples/blank --out out
streamlit run app.py
```

Options: `--passes 1` skips the second look (faster, and a single vision reading can then only be an observation);
`--vision-inspect off` skips the page inspection.

## What to expect

- **Calls.** One call per handwritten page for the entries (about 20 pages in the sample pack), one more per page for
  the inspection, plus a second look for every suspicious reading and about a dozen for the signature comparison. A clean
  pack is roughly 45 to 60 calls. On a CPU-only laptop, a page can take minutes. Try
  `--passes 1 --vision-inspect off` first to see how long the first look takes. If the HP is too slow, run the audit on
  the MacBook, or use the app's sample mode (which uses saved readings) for the live demo.
- **One image per call.** Ollama's Llama 3.2 vision handles a single image per request, so TenderLens sends one page at
  a time.
- **A model is not a witness.** A single vision reading is never enough for an issue: any reading that would become one
  (a Yes, a blank, a double tick, an impossible date, "crossed out") is asked again as a differently worded question. If
  the second look agrees it is an issue; if not, it is an observation. Signature "different hand" comparisons are
  always only observations.
- **Failures are shown.** If the endpoint is down or the model returns something unreadable, the report says how many
  calls failed and why, instead of quietly reading nothing.

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| `check-llm` says `URLError ... refused` | Ollama is not running: start the Ollama app or `ollama serve` |
| `HTTP 404` | the model name does not match `ollama list` |
| calls time out | raise `TENDERLENS_LLM_TIMEOUT`; close other apps; use a smaller or hosted model |
| every entry comes back wrong | the model is not seeing the page: run `check-llm` and read what it describes |
| `could not read` in the report | the model answered "unreadable" or something that is not an answer; a person reads those |
| vision: `HTTP 500 ... unknown model architecture: 'mllama'` | **a known, unfixed Ollama bug**, not this project's: several recent Ollama releases cannot load `llama3.2-vision` at all, on any endpoint (confirmed by a maintainer, [ollama/ollama#16490](https://github.com/ollama/ollama/issues/16490)). `ollama run llama3.2-vision` fails the same way outside TenderLens, which confirms it. The maintainer's own suggested fix is rolling Ollama back to v0.24.0 (download `Ollama.dmg`/`.exe` from that release tag directly, not from ollama.com, which always serves the latest). **Reinstalling the Ollama app can silently auto-update it back to the broken version on first launch** - if so, redo the rollback with Wi-Fi off, or quit the app immediately after installing and run the plain `ollama` CLI binary's `serve` directly instead of opening the GUI. Meanwhile the text step is unaffected if its model is a plain, non-vision Llama (`.env.example` defaults to `llama3.2:3b` for exactly this reason). |
