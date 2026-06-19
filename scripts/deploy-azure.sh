#!/usr/bin/env bash
set -euo pipefail

RG="Azure-AI-Solution"
FRONT_APP="screening-frontend-screeningwebapp"
BACK_APP="screening-backend-screeningwebapp"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/deploy-azure.sh frontend
  ./scripts/deploy-azure.sh backend
  ./scripts/deploy-azure.sh both

Environment overrides:
  RG
  FRONT_APP
  BACK_APP
EOF
}

deploy_frontend() {
  local api_base
  local zip_path
  local stage_dir
  api_base="${NEXT_PUBLIC_PARSER_API_BASE:-https://${BACK_APP}.azurewebsites.net}"
  zip_path="/tmp/screening-frontend-$(date +%s)-$$.zip"
  stage_dir="/tmp/screening-frontend-stage-$(date +%s)-$$"
  rm -f "$zip_path"
  rm -rf "$stage_dir"
  trap 'rm -f "$zip_path"; rm -rf "$stage_dir"' RETURN

  echo "Building frontend with NEXT_PUBLIC_PARSER_API_BASE=$api_base"
  NEXT_PUBLIC_PARSER_API_BASE="$api_base" npm run build

  echo "Staging standalone frontend into $stage_dir"
  mkdir -p "$stage_dir/.next"
  cp -R .next/standalone/. "$stage_dir/"
  cp -R .next/static "$stage_dir/.next/static"
  rm -f "$stage_dir/.env"

  echo "Packaging frontend into $zip_path"
  (
    cd "$stage_dir"
    zip -rq "$zip_path" .
  )

  echo "Deploying frontend to $FRONT_APP"
  az webapp deploy \
    --resource-group "${RG}" \
    --name "${FRONT_APP}" \
    --src-path "$zip_path" \
    --type zip
}

deploy_backend() {
  local zip_path
  zip_path="/tmp/screening-backend-$(date +%s)-$$.zip"
  rm -f "$zip_path"
  trap 'rm -f "$zip_path"' RETURN

  echo "Packaging backend into $zip_path"
  (
    cd backend
    zip -rq "$zip_path" . \
      -x ".venv/*" \
         "__pycache__/*" \
         "*.pyc"
  )

  echo "Deploying backend to $BACK_APP"
  az webapp deploy \
    --resource-group "${RG}" \
    --name "${BACK_APP}" \
    --src-path "$zip_path" \
    --type zip
}

TARGET="${1:-}"
case "$TARGET" in
  frontend)
    deploy_frontend
    ;;
  backend)
    deploy_backend
    ;;
  both)
    deploy_frontend
    deploy_backend
    ;;
  *)
    usage
    exit 1
    ;;
esac
