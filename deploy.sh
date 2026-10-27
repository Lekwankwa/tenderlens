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
#   export META_API_KEY=...                 # your Meta Model API key - never put this in a file you commit
#   ./deploy.sh
#
# The key is read from Secret Manager at container start, not baked into the image or set as a plain env
# var - see "4. store the key in Secret Manager" below.

set -euo pipefail

# ---------------------------------------------------------------------------------------------- variables
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your Google Cloud project ID first}"
REGION="${GCP_REGION:-africa-south1}"                      # Johannesburg
SERVICE_NAME="${TENDERLENS_SERVICE_NAME:-tenderlens}"
REPO_NAME="${TENDERLENS_REPO_NAME:-tenderlens}"             # Artifact Registry repository name
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${SERVICE_NAME}:$(date +%Y%m%d-%H%M%S)"

META_API_KEY="${META_API_KEY:?Set META_API_KEY to your Meta Model API key first (never commit it)}"

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
create_or_update_secret "tenderlens-meta-api-key" "$META_API_KEY"
SET_SECRETS="META_API_KEY=tenderlens-meta-api-key:latest"
SET_ENV_VARS="META_API_BASE=https://api.meta.ai/v1,MUSE_MODEL=muse-spark-1.3,TENDERLENS_PILOT=1,TENDERLENS_PURGE_HOURS=2,TENDERLENS_HOSTING_NOTE=Documents are processed on Google Cloud (Johannesburg); page images are sent to Meta's Model API (USA) for reading.,GCS_BUCKET=tenderlens-uploads-${PROJECT_ID},GCS_REGION=${REGION},MAX_UPLOAD_MB=300,UPLOAD_RETENTION_DAYS=7"

# ------------------------------------------------------------------------------------- 5. deploy to Cloud Run
# Resource/scaling choices here are deliberate, not defaults - see README "Hosting on Google Cloud Run":
#   --max-instances=1   pilot/pilot.db is a SQLite file baked into the image, not a shared database (see
#                        README "Pilot mode"); more than one instance means its pack counters would not be
#                        shared, and the pilot's own race-safety guarantee would only hold within one
#                        instance. Fix this (e.g. move counters to Supabase) before raising this past 1.
#   --no-cpu-throttling  a vision-reading pack can run for minutes of real CPU time; Cloud Run throttles CPU
#                        to near-zero outside an active request by default, which would stall mid-run work.
#   --timeout=3600       generous headroom for a large Complete check with retries.
#   --concurrency=8      raised from 2 after a real cold-start failure: with max-instances=1, a page load's
#                        own burst of small requests (JS bundles, the upload widget's own iframe asset
#                        fetch) could exceed concurrency=2 while the single instance was still booting, and
#                        Cloud Run hard-rejects the overflow ("aborted: no available instance") rather than
#                        queuing it, since there's no second instance to absorb it - this showed up as the
#                        upload widget failing to load. 8 gives a page load enough headroom; the ceiling
#                        this changes is how many simultaneous HEAVY audit runs could land on one instance
#                        at once (worst case 8, up from 2) - at 4Gi/2 vCPU that's a real OOM risk only if
#                        that many full audits truly overlap, which pilot/hackathon-scale traffic shouldn't.
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
  --concurrency=8 \
  --min-instances=0 \
  --max-instances=1 \
  --session-affinity \
  --allow-unauthenticated \
  --set-secrets="$SET_SECRETS" \
  --set-env-vars="$SET_ENV_VARS" \
  --quiet

echo
echo "Deployed. URL:"
gcloud run services describe "$SERVICE_NAME" --region="$REGION" --format="value(status.url)"
