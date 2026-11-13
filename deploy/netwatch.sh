#!/usr/bin/env bash
# Record the Mac's TCP connection state, so a network outage leaves evidence.
# Run every minute by the com.julybot.netwatch launchd agent
# (./deploy/install-netwatch.sh); also safe to run by hand.
#
# On 2026-11-13 the Mac stopped opening new connections for two hours
# ("Can't assign requested address", Chrome's ERR_ADDRESS_INVALID), most
# likely because something used up the ~16k local ports. A reboot fixed it
# and erased any trace of which program did it. This keeps that trace:
#
#   logs/netwatch/netwatch-<day>.log  one line a minute: connection counts,
#                                     the programs and remote hosts holding
#                                     the most, and whether a test connection
#                                     worked.
#   logs/netwatch/snapshot-<time>.txt every connection with its program, saved
#                                     when local ports run high or the test
#                                     connection fails.
#
# Nothing here needs the network except the test connection, so it still
# records while connections are failing. Files older than KEEP_DAYS are deleted.
set -euo pipefail

# Everything runs inside main(), like auto-update.sh: this script is updated by
# git pulls while launchd may be running it.
main() {
  local ROOT
  ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  local OUT="$ROOT/logs/netwatch"
  # Normal is under 100. macOS has 16384 local ports (49152-65535).
  local PORT_ALERT=5000
  local SNAPSHOT_GAP_SECONDS=600
  local KEEP_DAYS=7
  local TOP_N=3
  local PROBE_HOST="1.1.1.1"
  local PROBE_PORT=443
  local PROBE_TIMEOUT_SECONDS=3

  mkdir -p "$OUT"
  local now stamp ns first
  now=$(date '+%Y-%m-%d %H:%M:%S')
  stamp=$(date '+%Y%m%d-%H%M%S')
  ns=$(netstat -anv -p tcp)
  first=$(sysctl -n net.inet.ip.portrange.first)

  # Columns 1-10 are fixed; the process ("name:pid") starts at 11 and its name
  # may contain spaces. TIME_WAIT rows have no name, only ":pid".
  local summary
  summary=$(awk -v first="$first" -v top="$TOP_N" '
    function topn(arr,   out, i, k, best, bestk, used) {
      out = ""
      for (i = 0; i < top; i++) {
        best = 0; bestk = ""
        for (k in arr) if (!(k in used) && arr[k] > best) { best = arr[k]; bestk = k }
        if (bestk == "") break
        used[bestk] = 1
        out = out (out ? "," : "") bestk "=" best
      }
      return out ? out : "-"
    }
    NR > 2 && $1 ~ /^tcp/ {
      total++
      states[$6]++
      n = split($4, lp, ".")
      if (lp[n] + 0 >= first) ephemeral++
      if ($6 == "LISTEN") next
      proc = ""
      for (i = 11; i <= NF; i++) { proc = proc (proc ? " " : "") $i; if ($i ~ /:[0-9]+$/) break }
      split(proc, p, ":")
      name = (p[1] == "" ? "?" : p[1])
      gsub(/ /, "_", name)
      procs[name "[" p[2] "]"]++
      m = split($5, rp, ".")
      host = rp[1]
      for (j = 2; j < m; j++) host = host "." rp[j]
      remotes[host]++
    }
    END {
      printf "tcp=%d ports=%d estab=%d time_wait=%d close_wait=%d procs=%s remotes=%s\n",
        total, ephemeral, states["ESTABLISHED"], states["TIME_WAIT"], states["CLOSE_WAIT"],
        topn(procs), topn(remotes)
    }' <<<"$ns")

  local probe probe_ok=1 err
  # -v, or nc prints nothing on failure and the reason is lost.
  if err=$(nc -vz -G "$PROBE_TIMEOUT_SECONDS" "$PROBE_HOST" "$PROBE_PORT" 2>&1); then
    probe="ok"
  else
    probe_ok=0
    # "nc: connectx to 1.1.1.1 port 443 (tcp) failed: Can't assign requested address"
    err="${err##*: }"
    probe="FAIL(${err:-unknown})"
  fi

  local daily="$OUT/netwatch-${now%% *}.log"
  echo "$now $summary probe=$probe" >>"$daily"

  local ports
  ports=$(sed -E 's/.* ports=([0-9]+).*/\1/' <<<"$summary")
  if (( ports >= PORT_ALERT || probe_ok == 0 )); then
    local last=0 marker="$OUT/.last-snapshot"
    [[ -f "$marker" ]] && last=$(<"$marker")
    if (( $(date +%s) - last >= SNAPSHOT_GAP_SECONDS )); then
      local snap="$OUT/snapshot-$stamp.txt"
      {
        echo "# $now  $summary probe=$probe"
        echo; echo "## lsof -nP -iTCP"
        lsof -nP -iTCP 2>&1 || true
        echo; echo "## netstat -anv -p tcp"
        echo "$ns"
      } >"$snap"
      date +%s >"$marker"
      echo "$now SNAPSHOT $snap" >>"$daily"
    fi
  fi

  find "$OUT" -type f \( -name 'netwatch-*.log' -o -name 'snapshot-*.txt' \) \
    -mtime +"$KEEP_DAYS" -delete
}

main "$@"
exit
