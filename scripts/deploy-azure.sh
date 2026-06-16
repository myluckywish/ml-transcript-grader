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
  local zip_path
  zip_path="$(mktemp /tmp/screening-frontend-XXXXXX.zip)"
  trap 'rm -f "$zip_path"' RETURN

  echo "Packaging frontend into $zip_path"
  zip -rq "$zip_path" . \
    -x ".git/*" \
       "node_modules/*" \
       ".next/*" \
       "frontend.zip" \
       "backend.zip" \
       "backend/.venv/*" \
       "backend/__pycache__/*" \
       "backend/**/*.pyc"

  echo "Deploying frontend to $FRONT_APP"
  az webapp deploy \
    --resource-group "${RG}" \
    --name "${FRONT_APP}" \
    --src-path "$zip_path" \
    --type zip
}

deploy_backend() {
  local zip_path
  zip_path="$(mktemp /tmp/screening-backend-XXXXXX.zip)"
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
