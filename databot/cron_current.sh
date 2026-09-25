#!/bin/bash

script_path=$(dirname "$(readlink -f "$0")")
source "${script_path}/.env.sh"
cd /app
python3 /app/databot.py update && python3 /app/render.py
