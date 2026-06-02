#!/bin/sh
set -eu

python -m pip install --no-cache-dir -r requirements.txt
exec gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000 main:app
