#!/bin/bash

script_path=$(dirname "$(readlink -f "$0")")
source "${script_path}/.env.sh"
cd /app
# Fetch new readings into the CSV files, then render the current chart
python3 /app/databot.py csv && python3 /app/render.py
