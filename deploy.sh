#!/usr/bin/env bash
# Build and deploy TenderLens to Google Cloud Run (Johannesburg). See README "Hosting on Google Cloud Run"
# for the full walkthrough, including the exact commands to run before this script (gcloud auth, project
# selection, billing, the budget alert) and after it (viewing logs, rolling back).
#
# This script does NOT run itself - you run it, after reading it, with the variables below set. It is safe
# to run again later: it's how you push an update (a new image, a new revision with the same settings).
#
# Nothing here is a secret or a project ID. Set these in your own shell before running this script:
#   export GCP_PROJECT_ID=your-project-id
#   export TENDERLENS_LLM_API_KEY=...       # your DeepInfra key - never put this in a file you commit
#   ./deploy.sh
#
# TENDERLENS_VISION_API_KEY defaults to the same value as TENDERLENS_LLM_API_KEY (one DeepInfra account,
# one model, both roles - see .env.example) - export it separately only if you use a different key/account
# for vision.

set -euo pipefail

# ---------------------------------------------------------------------------------------------- variables
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your Google Cloud project ID first}"
REGION="${GCP_REGION:-africa-south1}"                      # Johannesburg
SERVICE_NAME="${TENDERLENS_SERVICE_NAME:-tenderlens}"
REPO_NAME="${TENDERLENS_REPO_NAME:-tenderlens}"             # Artifact Registry repository name
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:$(date +%Y%m%d-%H%M%S)"

LLM_API_KEY="${TENDERLENS_LLM_API_KEY:?Set TENDERLENS_LLM_API_KEY to your DeepInfra key first (never commit it)}"
VISION_API_KEY="${TENDERLENS_VISION_API_KEY:-$LLM_API_KEY}"

echo "Project:  $PROJECT_ID"
echo "Region:   $REGION (Johannesburg)"
echo "Service:  $SERVICE_NAME"
echo "Image:    $IMAGE"
echo

# ------------------------------------------------------------------------------------ 1. enable the APIs
gcloud config set project "$PROJECT_ID" --quiet
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  --quiet

# ---------------------------------------------------------------------- 2. create the Artifact Registry repo
# (a no-op, not an error, if it already exists - this is what makes re-running this script safe)
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --quiet >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="TenderLens images" \
    --quiet
fi

# ------------------------------------------------------------------------------- 3. build and push the image
# Builds in Google Cloud Build from the current directory (respects .dockerignore) - no local Docker needed
# for this step, which also works if you don't have Docker Desktop installed at all.
gcloud builds submit --tag "$IMAGE" --quiet

# --------------------------------------------------------------------- 4. store the key(s) in Secret Manager
create_or_update_secret() {
  local name="$1" value="$2"
  if gcloud secrets describe "$name" --quiet >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- --quiet
  else
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --quiet
  fi
}
create_or_update_secret "tenderlens-llm-api-key" "$LLM_API_KEY"
create_or_update_secret "tenderlens-vision-api-key" "$VISION_API_KEY"

# ------------------------------------------------------------------------------------- 5. deploy to Cloud Run
# Resource/scaling choices here are deliberate, not defaults - see README "Hosting on Google Cloud Run":
#   --max-instances=1   pilot/pilot.db is a SQLite file baked into the image, not a shared database (see
#                        README "Pilot mode"); more than one instance means its pack counters would not be
#                        shared, and the pilot's own race-safety guarantee would only hold within one
#                        instance. Fix this (e.g. move counters to Supabase) before raising this past 1.
#   --no-cpu-throttling  a vision-reading pack can run for minutes of real CPU time; Cloud Run throttles CPU
#                        to near-zero outside an active request by default, which would stall mid-run work.
#   --timeout=3600       generous headroom for a large Complete check with retries.
#   --concurrency=2      two requests per instance before Cloud Run starts a new one (kept low since a run
#                        is CPU/memory heavy, not I/O-bound).
#   --session-affinity   keeps a given browser session on the same instance across reruns (Streamlit's
#                        in-memory session state does not work if a rerun lands on a different instance).
gcloud run deploy "$SERVICE_NAME" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --memory=4Gi \
  --cpu=2 \
  --no-cpu-throttling \
  --timeout=3600 \
  --concurrency=2 \
  --min-instances=0 \
  --max-instances=1 \
  --session-affinity \
  --allow-unauthenticated \
  --set-secrets="TENDERLENS_LLM_API_KEY=tenderlens-llm-api-key:latest,TENDERLENS_VISION_API_KEY=tenderlens-vision-api-key:latest" \
  --set-env-vars="TENDERLENS_LLM_BASE_URL=https://api.deepinfra.com/v1/openai,TENDERLENS_LLM_MODEL=meta-llama/Llama-4-Scout-17B-16E-Instruct,TENDERLENS_VISION_BASE_URL=https://api.deepinfra.com/v1/openai,TENDERLENS_VISION_MODEL=meta-llama/Llama-4-Scout-17B-16E-Instruct,TENDERLENS_PILOT=1,TENDERLENS_MAX_UPLOAD_MB=30,TENDERLENS_PURGE_HOURS=2,TENDERLENS_HOSTING_NOTE=Documents are processed on Google Cloud (Johannesburg); page images are sent to DeepInfra (USA) for reading." \
  --quiet

echo
echo "Deployed. URL:"
gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)"
