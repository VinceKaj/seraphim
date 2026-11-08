#!/usr/bin/env python3
"""Live dashboard of tmux sessions + record store. Stdlib only, Linux /proc.

  python3 tools/tdash.py            # refresh every 2 s, Ctrl-c to quit
  python3 tools/tdash.py -n 5       # 5 s interval
  python3 tools/tdash.py -l 2       # at most 2 output lines per session
  python3 tools/tdash.py --once     # print once and exit (uses $LINES/$COLUMNS or 40x120)

The layout follows the terminal size on every refresh:
- every session always gets one status line;
- rows that are left are shared out as "last output" lines;
- columns of lower priority are dropped when the window is narrow;
- in a very short window the header and store lines merge into one.
"""
import argparse
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

STORE_DB = os.environ.get("STORE_DB", str(Path(__file__).resolve().parents[1] / "record-store" / "records.db"))
STORE_ADDR = ("127.0.0.1", 8100)
CLK = os.sysconf("SC_CLK_TCK")
PAGE = os.sysconf("SC_PAGE_SIZE")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

B, DIM, RST = "\x1b[1m", "\x1b[2m", "\x1b[0m"
GRN, YEL, RED, CYN, MAG = "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[36m", "\x1b[35m"


# ---------- data ----------

def sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def proc_table() -> dict[int, tuple[int, int, int]]:
    """pid -> (ppid, cpu_ticks, rss_bytes)"""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                st = f.read()
            with open(f"/proc/{d}/statm") as f:
                rss = int(f.read().split()[1]) * PAGE
        except OSError:
            continue
        rest = st[st.rindex(")") + 2:].split()   # fields after "(comm)"
        out[int(d)] = (int(rest[1]), int(rest[11]) + int(rest[12]), rss)
    return out


def tree(root: int, table: dict) -> list[int]:
    kids: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in table.items():
        kids.setdefault(ppid, []).append(pid)
    todo, seen = [root], []
    while todo:
        p = todo.pop()
        if p in table:
            seen.append(p)
            todo += kids.get(p, [])
    return seen


def store_stats() -> dict | None:
    """Counts read straight from SQLite (read-only), so the dashboard adds no lines to the API log."""
    try:
        c = sqlite3.connect(f"file:{STORE_DB}?mode=ro", uri=True, timeout=2)
        count, max_seq = c.execute("SELECT COUNT(*), COALESCE(MAX(seq),0) FROM records").fetchone()
        by_kind = dict(c.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind"))
        c.close()
        return {"count": count, "max_seq": max_seq, "by_kind": by_kind}
    except sqlite3.Error:
        return None


def port_open(addr) -> bool:
    try:
        with socket.create_connection(addr, timeout=1):
            return True
    except OSError:
        return False


def service_state(name: str) -> tuple[str, int]:
    out = sh("systemctl", "show", "-p", "ActiveState", "-p", "MainPID", "--value", name).split()
    return (out[1], int(out[0])) if len(out) == 2 else ("unknown", 0)


# ---------- formatting ----------

def fmt_bytes(n: float) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def fmt_age(sec: float) -> str:
    sec = int(sec)
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    if sec < 86400:
        return f"{sec // 3600}h{sec % 3600 // 60:02d}m"
    return f"{sec // 86400}d{sec % 86400 // 3600:02d}h"


def pct_color(pct: float) -> str:
    return GRN if pct < 50 else YEL if pct < 90 else RED


def bar(pct: float, width: int = 6) -> str:
    fill = min(width, round(pct / 100 * width))
    return f"{pct_color(pct)}{'█' * fill}{DIM}{'·' * (width - fill)}{RST}"


def vlen(s: str) -> int:
    return len(ANSI.sub("", s))


def cut(s: str, width: int) -> str:
    """Truncate to `width` visible chars, keeping ANSI codes intact."""
    if vlen(s) <= width:
        return s
    out, n, i = [], 0, 0
    while i < len(s) and n < width - 1:
        m = ANSI.match(s, i)
        if m:
            out.append(m.group()); i = m.end(); continue
        out.append(s[i]); n += 1; i += 1
    return "".join(out) + "…" + RST


def fit(segments: list[tuple[int, str]], width: int, sep: str = "  ") -> str:
    """segments = [(priority, text)] in display order; 0 = always kept.
    Drop the highest priority numbers first until the line fits, then hard-cut."""
    segs = [s for s in segments if s[1]]
    while len(segs) > 1 and vlen(sep.join(t for _, t in segs)) > width:
        worst = max(range(len(segs)), key=lambda i: (segs[i][0], i))
        if segs[worst][0] == 0:
            break
        segs.pop(worst)
    return cut(sep.join(t for _, t in segs), width)


# ---------- dashboard ----------

class Dash:
    def __init__(self, max_lines: int | None):
        self.max_lines = max_lines
        self.prev_ticks: dict[int, int] = {}
        self.prev_t = time.monotonic()
        self.count_hist: list[tuple[float, int]] = []

    def usage(self, root: int, table: dict, ticks_now: dict, dt: float) -> tuple[float, int, int]:
        pids = tree(root, table) if root else []
        ticks = sum(ticks_now.get(p, 0) for p in pids)
        prev = sum(self.prev_ticks.get(p, ticks_now.get(p, 0)) for p in pids)
        return (ticks - prev) / CLK / dt * 100, sum(table[p][2] for p in pids), len(pids)

    def render(self, rows: int, cols: int) -> list[str]:
        now, mono = time.time(), time.monotonic()
        dt = max(mono - self.prev_t, 1e-6)
        table = proc_table()
        ticks_now = {pid: t for pid, (_, t, _) in table.items()}

        # --- host ---
        load = os.getloadavg()
        mem = dict(l.split(":", 1) for l in open("/proc/meminfo").read().splitlines())
        mt = int(mem["MemTotal"].split()[0]) * 1024
        mu = mt - int(mem["MemAvailable"].split()[0]) * 1024
        host = [
            (0, f"{B}{CYN}seraphim{RST}"),
            (1, f"{DIM}{time.strftime('%H:%M:%S')}{RST}"),
            (2, f"load {load[0]:.2f}"),
            (5, f"{DIM}{load[1]:.2f} {load[2]:.2f}{RST}"),
            (3, f"mem {pct_color(mu / mt * 100)}{fmt_bytes(mu)}{RST}/{fmt_bytes(mt)}"),
        ]

        # --- record store ---
        h = store_stats()
        svc, main_pid = service_state("record-store")
        up = svc == "active" and port_open(STORE_ADDR)
        cpu, rss, _ = self.usage(main_pid, table, ticks_now, dt)
        store = [(0, f"{GRN if up else RED}●{RST} {B}store{RST}" + ("" if up else f" {RED}{svc}/port closed{RST}"))]
        if h:
            self.count_hist.append((mono, h["count"]))
            self.count_hist = [(t, c) for t, c in self.count_hist if mono - t <= 300]
            t0, c0 = self.count_hist[0]
            rate = (h["count"] - c0) / (mono - t0) * 60 if mono - t0 >= 1 else 0.0
            store += [
                (0, f"{B}{h['count']:,}{RST}"),
                (1, f"{MAG}+{rate:,.1f}/m{RST}"),
                (4, f"cpu {cpu:.0f}%"),
                (6, f"{fmt_bytes(rss)}"),
                (7, DIM + " ".join(f"{k}={v}" for k, v in sorted(h["by_kind"].items())) + RST),
            ]
        else:
            store.append((0, f"{RED}db unreadable{RST}"))

        # --- tmux sessions ---
        fmt = "|".join(["#{session_name}", "#{pane_pid}", "#{pane_current_command}", "#{session_created}",
                        "#{session_attached}", "#{pane_dead}", "#{pane_dead_status}",
                        "#{window_index}.#{pane_index}", "#{session_windows}", "#{window_panes}"])
        panes = [l.split("|") for l in sh("tmux", "list-panes", "-a", "-F", fmt).splitlines() if l]
        sess_lines, targets = [], []
        for name, pid, cmd, created, attached, dead, dstat, wp, nwin, npanes in panes:
            cpu, rss, nproc = self.usage(int(pid), table, ticks_now, dt)
            if dead == "1":
                dot = f"{RED}✖{RST}"; state = f"{RED}exit {dstat}{RST}"
            else:
                dot = f"{GRN}●{RST}" if cpu >= 1 else f"{YEL}●{RST}"; state = ""
            label = name if nwin == "1" and npanes == "1" else f"{name}:{wp}"
            sess_lines.append(fit([
                (0, f"{dot} {B}{label}{RST}"),
                (0, state),
                (1, f"{pct_color(cpu)}{cpu:5.1f}%{RST}"),
                (5, bar(cpu)),
                (2, f"{fmt_bytes(rss):>5}"),
                (3, f"{DIM}{fmt_age(now - int(created)):>6}{RST}"),
                (4, f"{DIM}{cmd}{RST}"),
                (7, f"{DIM}p{nproc}{RST}"),
                (6, f"{CYN}att{RST}" if attached != "0" else ""),
            ], cols, sep=" "))
            targets.append(f"{name}:{wp}")
        self.prev_ticks, self.prev_t = ticks_now, mono

        # --- layout ---
        n = len(sess_lines)
        out: list[str] = []
        if rows >= n + 3:                        # header + store on own lines
            out += [fit(host, cols), fit(store, cols)]
            if rows >= n + 4 + n:                # room for a rule and at least 1 output line each
                out.append(DIM + "─" * cols + RST)
        else:                                    # one merged top line
            out.append(fit([(p, t) for p, t in host[:1]] + [(p + 1, t) for p, t in store] +
                           [(p + 3, t) for p, t in host[1:]], cols))
        free_for_sessions = rows - len(out)
        if n > free_for_sessions:                # more sessions than rows: show what fits
            keep = max(free_for_sessions - 1, 0)
            out += sess_lines[:keep] + [f"{DIM}+{n - keep} more (enlarge window){RST}"]
            return out[:rows]
        if not panes:
            out.append(f"{DIM}no tmux sessions on the default socket{RST}")
            return out[:rows]

        spare = free_for_sessions - n
        per = spare // n if n else 0
        extra = spare - per * n
        if self.max_lines is not None:
            per, extra = min(per, self.max_lines), 0 if per >= self.max_lines else extra
        for i, (line, tgt) in enumerate(zip(sess_lines, targets)):
            out.append(line)
            k = per + (1 if i < extra else 0)
            if k <= 0:
                continue
            cap = sh("tmux", "capture-pane", "-p", "-J", "-t", tgt)
            tail = [ANSI.sub("", l).rstrip() for l in cap.splitlines() if l.strip()][-k:]
            out += [cut(f"{DIM}│{RST} {l}", cols) for l in tail]
        return out[:rows]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--interval", type=float, default=2.0)
    ap.add_argument("-l", "--lines", type=int, default=None, help="max output lines per session (default: fill window)")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    d = Dash(a.lines)
    if a.once:
        size = shutil.get_terminal_size((120, 40))
        d.render(size.lines, size.columns)   # first pass primes the CPU counters
        time.sleep(0.5)
        print("\n".join(d.render(size.lines, size.columns)))
        return
    sys.stdout.write("\x1b[?25l\x1b[2J")     # hide cursor, clear once
    try:
        while True:
            size = shutil.get_terminal_size((120, 40))
            lines = d.render(size.lines, size.columns)
            # redraw in place (no full clear, no flicker); never write past the last row
            sys.stdout.write("\x1b[H" + "\n".join(l + "\x1b[K" for l in lines) + "\x1b[J")
            sys.stdout.flush()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h\n")


if __name__ == "__main__":
    main()
