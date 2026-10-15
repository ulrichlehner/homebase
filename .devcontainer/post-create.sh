#!/bin/sh
# Dev container setup (after the playbook's dev-environment component), run once by
# VS Code after the container is created. Safe to run again.
set -e

# The checkout is bind-mounted from the host and owned by the host user, not root:
# without this git refuses it as "dubious ownership". System level, because VS Code
# copies the host's ~/.gitconfig over the global one.
git config --system --add safe.directory '*'

# Tools from Debian's own packages:
# - ripgrep for Todo Tree (devcontainer.json points it at /usr/bin/rg)
# - fzf: Ctrl-R fuzzy history search, Ctrl-T file search. Its zsh key bindings ship
#   under /usr/share/doc, which the slim image strips, so that folder is let back in.
# - zsh-autosuggestions, zsh-syntax-highlighting (.devcontainer/zshrc)
if [ ! -f /usr/share/doc/fzf/examples/key-bindings.zsh ]; then
  echo 'path-include=/usr/share/doc/fzf/*' > /etc/dpkg/dpkg.cfg.d/zz-fzf-keybindings
  apt-get update -qq
  apt-get install -y -qq --no-install-recommends --reinstall \
    ripgrep fzf zsh-autosuggestions zsh-syntax-highlighting >/dev/null
  rm -rf /var/lib/apt/lists/*
fi

# Shell: Oh My Zsh plugins, then the project's zshrc (prompt, history, suggestions,
# highlighting), sourced at the end of ~/.zshrc.
sed -i 's/^plugins=(.*)$/plugins=(git gh docker fzf)/' ~/.zshrc
# No Oh My Zsh theme: Starship draws the prompt.
sed -i 's/^ZSH_THEME=.*$/ZSH_THEME=""/' ~/.zshrc
if ! grep -q '.devcontainer/zshrc' ~/.zshrc; then
  echo '[ -f "$APP_DIR/.devcontainer/zshrc" ] && source "$APP_DIR/.devcontainer/zshrc"' >> ~/.zshrc
fi

# GitHub's host keys, so the first fetch over SSH does not stop at a prompt.
if ! grep -q '^github.com ' /etc/ssh/ssh_known_hosts 2>/dev/null; then
  mkdir -p /etc/ssh
  ssh-keyscan -t ed25519,ecdsa,rsa github.com 2>/dev/null >> /etc/ssh/ssh_known_hosts
fi

if [ ! -f "$APP_DIR/.env" ]; then
  echo "No .env yet: ./homebase needs one (README, 'Run'). Set DATA_DIR to an absolute path."
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not logged in yet: run 'gh auth login' once. The login is kept"
  echo "in the devcontainer-gh-config volume for every project on this machine."
fi
