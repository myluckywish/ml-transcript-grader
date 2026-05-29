# Azure Deployment Guide (ScreeningAutomation)

This guide deploys:
- Next.js frontend from repo root
- FastAPI backend from `backend/`
- CI/CD via GitHub Actions to Azure Web Apps

## 1) Azure resources (one-time)

Set variables (replace `<unique>` values):

```bash
RG=rg-screening-prod
LOC=centralus
FRONT_APP=screening-frontend-<unique>
BACK_APP=screening-backend-<unique>
FRONT_PLAN=plan-screening-front
BACK_PLAN=plan-screening-back
```

Option A (recommended): run the repo setup script:

```bash
chmod +x scripts/azure-setup.sh
RG=$RG LOC=$LOC FRONT_APP=$FRONT_APP BACK_APP=$BACK_APP FRONT_PLAN=$FRONT_PLAN BACK_PLAN=$BACK_PLAN \
  ./scripts/azure-setup.sh
```

Option B (manual): create resource group + plans + web apps:

```bash
az group create -n $RG -l $LOC

az appservice plan create -g $RG -n $FRONT_PLAN --is-linux --sku B1
az appservice plan create -g $RG -n $BACK_PLAN --is-linux --sku B1

az webapp create -g $RG -p $FRONT_PLAN -n $FRONT_APP --runtime "NODE:20-lts"
az webapp create -g $RG -p $BACK_PLAN -n $BACK_APP --runtime "PYTHON:3.11"
```

Set backend startup command:

```bash
az webapp config set -g $RG -n $BACK_APP \
  --startup-file "gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 main:app" \
  --generic-configurations '{"alwaysOn": true}'
```

## 2) App settings

Backend settings:

```bash
az webapp config appsettings set -g $RG -n $BACK_APP --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true \
  AZURE_DOC_INTEL_ENABLED=false \
  AZURE_OPENAI_ENABLED=false
```

Frontend settings (important: code uses `NEXT_PUBLIC_PARSER_API_BASE`):

```bash
az webapp config appsettings set -g $RG -n $FRONT_APP --settings \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true \
  NEXT_PUBLIC_PARSER_API_BASE="https://$BACK_APP.azurewebsites.net"
```

If enabling Azure AI later, add backend app settings:

```bash
az webapp config appsettings set -g $RG -n $BACK_APP --settings \
  AZURE_DOC_INTEL_ENABLED=true \
  AZURE_DOC_INTEL_ENDPOINT="https://<docintel-name>.cognitiveservices.azure.com" \
  AZURE_DOC_INTEL_API_KEY="<key>" \
  AZURE_DOC_INTEL_API_VERSION="2024-11-30" \
  AZURE_DOC_INTEL_MODEL_ID="prebuilt-layout" \
  AZURE_OPENAI_ENABLED=true \
  AZURE_OPENAI_ENDPOINT="https://<aoai-name>.openai.azure.com" \
  AZURE_OPENAI_API_KEY="<key>" \
  AZURE_OPENAI_API_VERSION="2024-10-21" \
  AZURE_OPENAI_DEPLOYMENT="<deployment-name>"
```

## 3) GitHub repo secrets for CI/CD

In GitHub: `Settings -> Secrets and variables -> Actions -> New repository secret`.

Add these secrets:
- `AZUREAPPSERVICE_PUBLISHPROFILE_FRONTEND`: publish profile XML for frontend web app
- `AZUREAPPSERVICE_PUBLISHPROFILE_BACKEND`: publish profile XML for backend web app
- `AZURE_WEBAPP_NAME_FRONTEND`: frontend app name (example `screening-frontend-abc123`)
- `AZURE_WEBAPP_NAME_BACKEND`: backend app name (example `screening-backend-abc123`)

Get publish profiles:

```bash
az webapp deployment list-publishing-profiles -g $RG -n $FRONT_APP --xml
az webapp deployment list-publishing-profiles -g $RG -n $BACK_APP --xml
```

## 4) Workflows in this repo

- `.github/workflows/deploy-frontend.yml`
- `.github/workflows/deploy-backend.yml`

Deployment triggers:
- Frontend deploys on push to `main` when frontend files change
- Backend deploys on push to `main` when `backend/**` changes
- Both support manual trigger via `workflow_dispatch`

## 5) Validation checklist

After first deploy:

1. Open frontend URL: `https://<frontend-app>.azurewebsites.net`
2. Verify backend health: `https://<backend-app>.azurewebsites.net/health`
3. Upload a transcript and confirm batch jobs progress in UI
4. If upload fails due to size/timeouts, tune App Service and request timeout settings

## 6) Troubleshooting

- CORS: backend currently allows all origins.
- 500 at startup: confirm backend startup command and dependencies installed.
- Frontend cannot reach API: confirm `NEXT_PUBLIC_PARSER_API_BASE` exact name and value.
- Slow long-running jobs: consider scaling backend plan to `S1` and increasing worker count.
