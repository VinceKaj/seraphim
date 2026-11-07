#!/bin/sh
# Live logs of the record-store service in tmux session "record-store" (default socket).
# The server itself runs under systemd; Ctrl-c here only stops the log view.
tmux has-session -t record-store 2>/dev/null || \
  tmux new-session -d -s record-store 'journalctl -u record-store -f -n 500 -o cat'
[ -n "$1" ] && exit 0
[ -n "$TMUX" ] && exec tmux switch-client -t record-store
exec tmux attach -t record-store
