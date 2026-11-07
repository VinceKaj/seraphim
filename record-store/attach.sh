#!/bin/sh
# Attach to the record-store tmux session. Detach with Ctrl-b d (do NOT Ctrl-c: that stops the server; systemd restarts it after 5 s).
exec tmux -L record-store attach -t record-store
