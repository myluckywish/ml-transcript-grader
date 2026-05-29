#!/usr/bin/env bash
set -euo pipefail

if ! command -v az >/dev/null 2>&1; then
  echo "Azure CLI ('az') is required."
  exit 1
fi

if [ -z "${RG:-}" ] || [ -z "${LOC:-}" ] || [ -z "${FRONT_APP:-}" ] || [ -z "${BACK_APP:-}" ] || [ -z "${FRONT_PLAN:-}" ] || [ -z "${BACK_PLAN:-}" ]; then
  cat <<'EOF'
Set required environment variables first:
  RG=<resource-group>
  LOC=<azure-region>
  FRONT_APP=<frontend-app-name>
  BACK_APP=<backend-app-name>
  FRONT_PLAN=<frontend-plan-name>
  BACK_PLAN=<backend-plan-name>
EOF
  exit 1
fi

echo "Creating resource group and App Service plans..."
az group create -n "$RG" -l "$LOC" >/dev/null
az appservice plan create -g "$RG" -n "$FRONT_PLAN" --is-linux --sku B1 >/dev/null
az appservice plan create -g "$RG" -n "$BACK_PLAN" --is-linux --sku B1 >/dev/null

echo "Creating web apps..."
az webapp create -g "$RG" -p "$FRONT_PLAN" -n "$FRONT_APP" --runtime "NODE:20-lts" >/dev/null
az webapp create -g "$RG" -p "$BACK_PLAN" -n "$BACK_APP" --runtime "PYTHON:3.11" >/dev/null

echo "Configuring backend startup command..."
az webapp config set -g "$RG" -n "$BACK_APP" \
  --startup-file "gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 main:app" \
  --generic-configurations '{"alwaysOn": true}' >/dev/null

echo "Applying app settings..."
az webapp config appsettings set -g "$RG" -n "$BACK_APP" --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true \
  AZURE_DOC_INTEL_ENABLED=false \
  AZURE_OPENAI_ENABLED=false >/dev/null

az webapp config appsettings set -g "$RG" -n "$FRONT_APP" --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true \
  NEXT_PUBLIC_PARSER_API_BASE="https://$BACK_APP.azurewebsites.net" >/dev/null

echo
echo "Done. Next commands:"
echo "  az webapp deployment list-publishing-profiles -g $RG -n $FRONT_APP --xml"
echo "  az webapp deployment list-publishing-profiles -g $RG -n $BACK_APP --xml"
