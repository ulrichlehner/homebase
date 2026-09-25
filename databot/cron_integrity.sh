#!/bin/bash

script_path=$(dirname "$(readlink -f "$0")")
source "${script_path}/.env.sh"
cd /app
# Fetch older data the portal still has, then re-fetch days with missing intervals
python3 /app/databot.py backfill
python3 /app/databot.py repair --days 60
