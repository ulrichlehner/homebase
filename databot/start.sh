#!/bin/bash

script_path=$(dirname "$(readlink -f "$0")")

# Expose env vars to the cron jobs (cron starts with an empty environment).
# `export -p` quotes values safely, so passwords with special characters survive.
export -p > "$script_path/.env.sh"
chmod +x "${script_path}/.env.sh"

# Run `cron -f` in foreground
cron -f
