import socket
import ssl
import threading
import time
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# ─── Broker Configuration ────────────────────────────────────────────────────
HOST           = "0.0.0.0"
PORT           = 5000
DASHBOARD_PORT = 8080

# ─── Shared State ─────────────────────────────────────────────────────────────
topics      = {}   # topic -> set of conn
client_info = {}   # conn  -> metadata dict
topic_stats = {}   # topic -> total message count
lock        = threading.Lock()

messages_processed = 0
start_time         = time.time()


# ═══════════════════════════════════════════════════════════════════════════════
#  Client lifecycle helpers
# ═══════════════════════════════════════════════════════════════════════════════

def remove_client(conn):
    """Cleanly remove a connection from all state."""
    with lock:
        info = client_info.pop(conn, None)
        for subs in topics.values():
            subs.discard(conn)
    if info:
        print(f"[REMOVED] {_fmt_addr(info['addr'])}")


def _fmt_addr(addr):
    return f"{addr[0]}:{addr[1]}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Command processing
# ═══════════════════════════════════════════════════════════════════════════════

def process_command(conn, addr, message):
    """Parse and execute a single protocol command."""
    global messages_processed

    # Split on first two colons only so message bodies can contain ':'
    parts = message.split(":", 2)

    if len(parts) < 2:
        _safe_send(conn, b"ERROR:INVALID_FORMAT\n")
        return

    command = parts[0]

    # ── IDENTIFY ──────────────────────────────────────────────────────────────
    if command == "IDENTIFY":
        role = parts[1].lower()
        with lock:
            if conn in client_info:
                client_info[conn]["role"] = role
        _safe_send(conn, f"IDENTIFIED:{role}\n".encode())
        print(f"[IDENTIFY] {_fmt_addr(addr)} → {role}")

    # ── SUBSCRIBE ─────────────────────────────────────────────────────────────
    elif command == "SUBSCRIBE":
        topic = parts[1]
        with lock:
            topics.setdefault(topic, set()).add(conn)
            if topic not in topic_stats:
                topic_stats[topic] = 0
            if conn in client_info:
                client_info[conn]["topics"].add(topic)
                if client_info[conn]["role"] == "unknown":
                    client_info[conn]["role"] = "subscriber"
        _safe_send(conn, f"SUBSCRIBED:{topic}\n".encode())
        print(f"[SUBSCRIBE] {_fmt_addr(addr)} → '{topic}'")

    # ── UNSUBSCRIBE ───────────────────────────────────────────────────────────
    elif command == "UNSUBSCRIBE":
        topic = parts[1]
        with lock:
            if topic in topics:
                topics[topic].discard(conn)
            if conn in client_info:
                client_info[conn]["topics"].discard(topic)
        _safe_send(conn, f"UNSUBSCRIBED:{topic}\n".encode())
        print(f"[UNSUBSCRIBE] {_fmt_addr(addr)} ← '{topic}'")

    # ── PUBLISH ───────────────────────────────────────────────────────────────
    elif command == "PUBLISH":
        if len(parts) < 3:
            _safe_send(conn, b"ERROR:INVALID_PUBLISH\n")
            return

        topic = parts[1]
        msg   = parts[2]

        with lock:
            subscribers = topics.get(topic, set()).copy()
            topic_stats[topic] = topic_stats.get(topic, 0) + 1
            if conn in client_info:
                if client_info[conn]["role"] == "unknown":
                    client_info[conn]["role"] = "publisher"
                client_info[conn]["msg_pub"] += 1

        payload = f"MESSAGE:{topic}:{msg}\n".encode()
        dead    = []
        for sub in subscribers:
            if sub is conn:
                continue
            if not _safe_send(sub, payload):
                dead.append(sub)
            else:
                with lock:
                    if sub in client_info:
                        client_info[sub]["msg_recv"] += 1

        for d in dead:
            remove_client(d)

        messages_processed += 1
        _safe_send(conn, b"OK\n")

    else:
        _safe_send(conn, b"ERROR:UNKNOWN_COMMAND\n")


def _safe_send(conn, data):
    """Send data, return False on failure."""
    try:
        conn.send(data)
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Per-client thread
# ═══════════════════════════════════════════════════════════════════════════════

def handle_client(conn, addr):
    print(f"[CONNECTED] {_fmt_addr(addr)}")

    with lock:
        client_info[conn] = {
            "addr":           addr,
            "role":           "unknown",
            "topics":         set(),
            "msg_pub":        0,
            "msg_recv":       0,
            "connected_since": time.time(),
        }

    buffer = ""
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            try:
                buffer += chunk.decode()
            except UnicodeDecodeError:
                continue

            # Process all complete (newline-terminated) lines
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if line:
                    process_command(conn, addr, line)

    except Exception as e:
        print(f"[ERROR] {_fmt_addr(addr)}: {e}")
    finally:
        remove_client(conn)
        conn.close()
        print(f"[DISCONNECTED] {_fmt_addr(addr)}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Dashboard data serialiser
# ═══════════════════════════════════════════════════════════════════════════════

def get_stats():
    """Return a JSON-serialisable snapshot of broker state."""
    with lock:
        clients_data = []
        for info in client_info.values():
            clients_data.append({
                "addr":      _fmt_addr(info["addr"]),
                "role":      info["role"],
                "topics":    sorted(info["topics"]),
                "msg_pub":   info["msg_pub"],
                "msg_recv":  info["msg_recv"],
                "duration":  round(time.time() - info["connected_since"], 1),
            })

        all_topics = set(topics.keys()) | set(topic_stats.keys())
        topics_data = sorted([
            {
                "name":        t,
                "subscribers": len(topics.get(t, set())),
                "messages":    topic_stats.get(t, 0),
            }
            for t in all_topics
        ], key=lambda x: x["name"])

    elapsed = max(time.time() - start_time, 1)
    return {
        "clients":        clients_data,
        "topics":         topics_data,
        "total_messages": messages_processed,
        "uptime":         round(elapsed, 1),
        "throughput":     round(messages_processed / elapsed, 2),
        "broker_host":    HOST,
        "broker_port":    PORT,
        "timestamp":      datetime.now().strftime("%H:%M:%S"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Web dashboard HTML
# ═══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>PubSub Broker Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:       #080c14;
      --surface:  #0d1424;
      --card:     #111827;
      --border:   #1e2d45;
      --cyan:     #00e5ff;
      --green:    #00ffa3;
      --amber:    #ffb347;
      --red:      #ff4d6d;
      --dim:      #4a5568;
      --text:     #c9d8f0;
      --muted:    #5a6e8a;
      --mono:     'Space Mono', monospace;
      --sans:     'DM Sans', sans-serif;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      min-height: 100vh;
      padding: 0;
    }

    /* ── scanline overlay ── */
    body::before {
      content: '';
      position: fixed; inset: 0;
      background: repeating-linear-gradient(
        0deg,
        transparent,
        transparent 2px,
        rgba(0,229,255,0.012) 2px,
        rgba(0,229,255,0.012) 4px
      );
      pointer-events: none;
      z-index: 9999;
    }

    /* ── header ── */
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 18px 32px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(90deg, #0a1020 0%, #0d1424 100%);
      position: sticky; top: 0; z-index: 100;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .brand-icon {
      width: 36px; height: 36px;
      border: 1.5px solid var(--cyan);
      border-radius: 6px;
      display: grid;
      place-items: center;
      box-shadow: 0 0 12px rgba(0,229,255,0.25);
    }

    .brand-icon svg { width: 20px; height: 20px; }

    .brand-title {
      font-family: var(--mono);
      font-size: 15px;
      font-weight: 700;
      color: var(--cyan);
      letter-spacing: 0.05em;
    }

    .brand-sub {
      font-family: var(--sans);
      font-size: 11px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
      margin-top: 2px;
    }

    .header-right {
      display: flex;
      align-items: center;
      gap: 24px;
    }

    .live-badge {
      display: flex;
      align-items: center;
      gap: 7px;
      font-family: var(--mono);
      font-size: 11px;
      color: var(--green);
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }

    .live-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      background: var(--green);
      animation: pulse 1.6s ease-in-out infinite;
      box-shadow: 0 0 8px var(--green);
    }

    @keyframes pulse {
      0%,100% { opacity: 1; transform: scale(1); }
      50%      { opacity: 0.4; transform: scale(0.85); }
    }

    #clock {
      font-family: var(--mono);
      font-size: 13px;
      color: var(--muted);
    }

    /* ── main layout ── */
    main {
      padding: 28px 32px;
      max-width: 1400px;
      margin: 0 auto;
    }

    /* ── section labels ── */
    .section-label {
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 14px;
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .section-label::after {
      content: '';
      flex: 1;
      height: 1px;
      background: var(--border);
    }

    /* ── stat cards ── */
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-bottom: 32px;
    }

    @media (max-width: 900px) {
      .stat-grid { grid-template-columns: repeat(2, 1fr); }
    }

    .stat-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px 22px;
      position: relative;
      overflow: hidden;
      transition: border-color 0.2s;
    }

    .stat-card:hover { border-color: var(--cyan); }

    .stat-card::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0;
      height: 2px;
    }

    .stat-card.c-cyan::before   { background: var(--cyan); box-shadow: 0 0 12px var(--cyan); }
    .stat-card.c-green::before  { background: var(--green); box-shadow: 0 0 12px var(--green); }
    .stat-card.c-amber::before  { background: var(--amber); box-shadow: 0 0 12px var(--amber); }
    .stat-card.c-dim::before    { background: var(--dim); }

    .stat-label {
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 10px;
    }

    .stat-value {
      font-family: var(--mono);
      font-size: 32px;
      font-weight: 700;
      line-height: 1;
      color: var(--text);
    }

    .stat-value.c-cyan  { color: var(--cyan);  text-shadow: 0 0 20px rgba(0,229,255,0.3); }
    .stat-value.c-green { color: var(--green); text-shadow: 0 0 20px rgba(0,255,163,0.3); }
    .stat-value.c-amber { color: var(--amber); text-shadow: 0 0 20px rgba(255,179,71,0.3); }

    .stat-sub {
      font-size: 11px;
      color: var(--muted);
      margin-top: 6px;
    }

    /* ── tables ── */
    .panel {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      overflow: hidden;
      margin-bottom: 24px;
    }

    .panel-header {
      padding: 14px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: rgba(0,0,0,0.2);
    }

    .panel-title {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--cyan);
      letter-spacing: 0.08em;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .panel-count {
      font-family: var(--mono);
      font-size: 11px;
      background: rgba(0,229,255,0.08);
      color: var(--cyan);
      border: 1px solid rgba(0,229,255,0.2);
      border-radius: 4px;
      padding: 2px 8px;
    }

    table {
      width: 100%;
      border-collapse: collapse;
    }

    thead th {
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
      padding: 10px 20px;
      text-align: left;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }

    tbody tr {
      border-bottom: 1px solid rgba(30,45,69,0.5);
      transition: background 0.15s;
    }

    tbody tr:last-child { border-bottom: none; }

    tbody tr:hover { background: rgba(0,229,255,0.03); }

    tbody td {
      padding: 12px 20px;
      vertical-align: middle;
    }

    .mono { font-family: var(--mono); font-size: 12px; }

    /* ── role badges ── */
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 3px 9px;
      border-radius: 4px;
      border: 1px solid;
      white-space: nowrap;
    }

    .badge-publisher  { color: var(--amber); border-color: rgba(255,179,71,0.3);  background: rgba(255,179,71,0.06); }
    .badge-subscriber { color: var(--green); border-color: rgba(0,255,163,0.3); background: rgba(0,255,163,0.06); }
    .badge-unknown    { color: var(--dim);   border-color: rgba(74,85,104,0.3); background: rgba(74,85,104,0.06); }

    /* ── topic pills ── */
    .topic-list { display: flex; flex-wrap: wrap; gap: 5px; }
    .topic-pill {
      font-family: var(--mono);
      font-size: 10px;
      padding: 2px 7px;
      border-radius: 3px;
      background: rgba(0,229,255,0.06);
      color: var(--cyan);
      border: 1px solid rgba(0,229,255,0.15);
    }

    /* ── number highlight ── */
    .num-pub  { color: var(--amber); font-family: var(--mono); }
    .num-recv { color: var(--green); font-family: var(--mono); }
    .num-msg  { color: var(--cyan);  font-family: var(--mono); }
    .num-sub  { color: var(--green); font-family: var(--mono); }

    .duration { color: var(--muted); font-family: var(--mono); font-size: 12px; }

    /* ── empty state ── */
    .empty-row td {
      text-align: center;
      padding: 32px;
      color: var(--muted);
      font-size: 12px;
    }

    /* ── broker info strip ── */
    .info-strip {
      display: flex;
      align-items: center;
      gap: 28px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 20px;
      margin-bottom: 28px;
      font-size: 12px;
    }

    .info-item { display: flex; align-items: center; gap: 8px; color: var(--muted); }
    .info-item strong { color: var(--text); font-weight: 500; }
    .info-sep { width: 1px; height: 16px; background: var(--border); }

    /* ── two-col layout ── */
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }

    /* ── chart panels ── */
    .chart-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin-bottom: 32px;
    }
    @media (max-width: 900px) { .chart-grid { grid-template-columns: 1fr; } }

    .chart-wrap {
      position: relative;
      height: 220px;
      padding: 16px 18px 10px;
    }

    .chart-wrap canvas {
      max-height: 100%;
    }

    .chart-title-row {
      display: flex;
      align-items: center;
      gap: 8px;
      font-family: var(--mono);
      font-size: 10px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 4px;
      padding: 14px 20px 0;
    }

    .chart-title-dot {
      width: 7px; height: 7px;
      border-radius: 50%;
      flex-shrink: 0;
    }

  </style>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
</head>
<body>

<header>
  <div class="brand">
    <div class="brand-icon">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
           style="color:var(--cyan)">
        <circle cx="5"  cy="12" r="2.5"/>
        <circle cx="19" cy="6"  r="2.5"/>
        <circle cx="19" cy="18" r="2.5"/>
        <line x1="7.5"  y1="11"  x2="16.5" y2="7"/>
        <line x1="7.5"  y1="13"  x2="16.5" y2="17"/>
      </svg>
    </div>
    <div>
      <div class="brand-title">PUBSUB BROKER</div>
      <div class="brand-sub">Real-Time Monitor</div>
    </div>
  </div>
  <div class="header-right">
    <div class="live-badge">
      <div class="live-dot"></div>
      LIVE
    </div>
    <div id="clock">--:--:--</div>
  </div>
</header>

<main>

  <!-- Broker connection info -->
  <div class="info-strip" id="broker-info">
    <div class="info-item">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>
        <line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>
      </svg>
      Broker: <strong id="b-addr">loading…</strong>
    </div>
    <div class="info-sep"></div>
    <div class="info-item">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/>
        <polyline points="12 6 12 12 16 14"/>
      </svg>
      Uptime: <strong id="b-uptime">—</strong>
    </div>
    <div class="info-sep"></div>
    <div class="info-item">
      Dashboard refreshes every <strong>2 s</strong>
    </div>
  </div>

  <!-- Stat cards -->
  <div class="section-label">System Metrics</div>
  <div class="stat-grid">
    <div class="stat-card c-cyan">
      <div class="stat-label">Connected Clients</div>
      <div class="stat-value c-cyan" id="s-clients">0</div>
      <div class="stat-sub">active connections</div>
    </div>
    <div class="stat-card c-green">
      <div class="stat-label">Active Topics</div>
      <div class="stat-value c-green" id="s-topics">0</div>
      <div class="stat-sub">with ≥1 subscriber</div>
    </div>
    <div class="stat-card c-amber">
      <div class="stat-label">Total Messages</div>
      <div class="stat-value c-amber" id="s-messages">0</div>
      <div class="stat-sub">since broker start</div>
    </div>
    <div class="stat-card c-dim">
      <div class="stat-label">Throughput</div>
      <div class="stat-value" id="s-throughput">0</div>
      <div class="stat-sub">messages / second</div>
    </div>
  </div>

  <!-- ── Live Charts ─────────────────────────────────────────────────────── -->
  <div class="section-label">Live Charts</div>
  <div class="chart-grid">

    <!-- Throughput over time -->
    <div class="panel">
      <div class="chart-title-row">
        <span class="chart-title-dot" style="background:var(--cyan);box-shadow:0 0 6px var(--cyan)"></span>
        Throughput over Time
        <span style="margin-left:auto;color:var(--muted);font-size:9px">msg / s</span>
      </div>
      <div class="chart-wrap"><canvas id="chart-throughput"></canvas></div>
    </div>

    <!-- Client count over time -->
    <div class="panel">
      <div class="chart-title-row">
        <span class="chart-title-dot" style="background:var(--green);box-shadow:0 0 6px var(--green)"></span>
        Connected Clients over Time
        <span style="margin-left:auto;color:var(--muted);font-size:9px">clients</span>
      </div>
      <div class="chart-wrap"><canvas id="chart-clients"></canvas></div>
    </div>

    <!-- Throughput vs Clients scatter -->
    <div class="panel">
      <div class="chart-title-row">
        <span class="chart-title-dot" style="background:var(--amber);box-shadow:0 0 6px var(--amber)"></span>
        Throughput vs Client Count
        <span style="margin-left:auto;color:var(--muted);font-size:9px">x = clients · y = msg/s</span>
      </div>
      <div class="chart-wrap"><canvas id="chart-scatter"></canvas></div>
    </div>

    <!-- Messages per topic -->
    <div class="panel">
      <div class="chart-title-row">
        <span class="chart-title-dot" style="background:var(--red);box-shadow:0 0 6px var(--red)"></span>
        Messages per Topic
        <span style="margin-left:auto;color:var(--muted);font-size:9px">total count</span>
      </div>
      <div class="chart-wrap"><canvas id="chart-topics"></canvas></div>
    </div>

  </div>

  <!-- Connected Clients table -->
  <div class="section-label">Connected Clients</div>
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
          <circle cx="9" cy="7" r="4"/>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
          <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
        CLIENTS
      </div>
      <div class="panel-count" id="client-count">0</div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Address</th>
          <th>Role</th>
          <th>Subscribed Topics</th>
          <th>Published</th>
          <th>Received</th>
          <th>Connected</th>
        </tr>
      </thead>
      <tbody id="client-tbody">
        <tr class="empty-row"><td colspan="6">No clients connected</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Topics + Activity side by side -->
  <div class="two-col">
    <!-- Topics table -->
    <div>
      <div class="section-label">Topics</div>
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2">
              <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/>
              <line x1="7" y1="7" x2="7.01" y2="7"/>
            </svg>
            TOPIC REGISTRY
          </div>
          <div class="panel-count" id="topic-count">0</div>
        </div>
        <table>
          <thead>
            <tr>
              <th>Topic Name</th>
              <th>Subscribers</th>
              <th>Messages</th>
            </tr>
          </thead>
          <tbody id="topic-tbody">
            <tr class="empty-row"><td colspan="3">No topics yet</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Role breakdown -->
    <div>
      <div class="section-label">Role Breakdown</div>
      <div class="panel" style="height: calc(100% - 30px);">
        <div class="panel-header">
          <div class="panel-title">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="2">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
            </svg>
            ACTIVITY BREAKDOWN
          </div>
        </div>
        <div style="padding: 24px 20px; display: flex; flex-direction: column; gap: 20px;">
          <div id="role-breakdown">
            <div style="color: var(--muted); font-size:12px; text-align:center; padding:20px 0;">
              Waiting for clients…
            </div>
          </div>
          <div style="margin-top: auto;">
            <div class="section-label" style="margin-bottom: 12px;">Connection Info</div>
            <div style="font-size: 12px; color: var(--muted); line-height: 2; font-family: var(--mono);">
              <div>Publisher endpoint: <span style="color:var(--amber)" id="pub-ep">—</span></div>
              <div>Subscriber endpoint: <span style="color:var(--green)" id="sub-ep">—</span></div>
              <div>Dashboard: <span style="color:var(--cyan)" id="dash-ep">—</span></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

</main>

<script>
  // ── Clock ──────────────────────────────────────────────────────────────────
  function tick() {
    document.getElementById('clock').textContent =
      new Date().toLocaleTimeString('en-GB');
  }
  tick();
  setInterval(tick, 1000);

  // ── Format uptime ──────────────────────────────────────────────────────────
  function fmtUptime(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    return [h,m,sec].map(n => String(n).padStart(2,'0')).join(':');
  }

  // ── Render helpers ─────────────────────────────────────────────────────────
  function roleBadge(role) {
    const map = {
      publisher:  ['badge-publisher',  '↑ Publisher'],
      subscriber: ['badge-subscriber', '↓ Subscriber'],
    };
    const [cls, label] = map[role] || ['badge-unknown', '? Unknown'];
    return `<span class="badge ${cls}">${label}</span>`;
  }

  function topicPills(topics) {
    if (!topics.length) return '<span style="color:var(--muted);font-size:11px">—</span>';
    return '<div class="topic-list">' +
      topics.map(t => `<span class="topic-pill">${t}</span>`).join('') +
      '</div>';
  }

  function renderBar(label, count, total, color) {
    const pct = total ? Math.round(count / total * 100) : 0;
    return `
      <div style="margin-bottom:14px">
        <div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:12px;">
          <span style="color:var(--text)">${label}</span>
          <span style="font-family:var(--mono);color:${color}">${count}</span>
        </div>
        <div style="height:4px;background:var(--border);border-radius:2px;overflow:hidden">
          <div style="height:100%;width:${pct}%;background:${color};
               border-radius:2px;transition:width 0.4s ease;box-shadow:0 0 6px ${color}44"></div>
        </div>
      </div>`;
  }

  // ── Chart theme helpers ────────────────────────────────────────────────────
  const CSS = getComputedStyle(document.documentElement);
  const C = {
    cyan:    CSS.getPropertyValue('--cyan').trim()   || '#00e5ff',
    green:   CSS.getPropertyValue('--green').trim()  || '#00ffa3',
    amber:   CSS.getPropertyValue('--amber').trim()  || '#ffb347',
    red:     CSS.getPropertyValue('--red').trim()    || '#ff4d6d',
    border:  CSS.getPropertyValue('--border').trim() || '#1e2d45',
    muted:   CSS.getPropertyValue('--muted').trim()  || '#5a6e8a',
    text:    CSS.getPropertyValue('--text').trim()   || '#c9d8f0',
    card:    CSS.getPropertyValue('--card').trim()   || '#111827',
    bg:      CSS.getPropertyValue('--bg').trim()     || '#080c14',
  };

  const CHART_DEFAULTS = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#0d1424',
        borderColor: C.border,
        borderWidth: 1,
        titleColor: C.text,
        bodyColor: C.muted,
        titleFont: { family: "'Space Mono', monospace", size: 11 },
        bodyFont:  { family: "'Space Mono', monospace", size: 11 },
        padding: 10,
      },
    },
    scales: {
      x: {
        grid:  { color: C.border + '88', drawBorder: false },
        ticks: { color: C.muted, font: { family: "'Space Mono', monospace", size: 9 }, maxTicksLimit: 8 },
        border: { color: C.border },
      },
      y: {
        grid:  { color: C.border + '88', drawBorder: false },
        ticks: { color: C.muted, font: { family: "'Space Mono', monospace", size: 9 }, maxTicksLimit: 5 },
        border: { color: C.border },
        beginAtZero: true,
      },
    },
  };

  function glowLine(color) {
    return {
      borderColor: color,
      backgroundColor: color + '18',
      pointBackgroundColor: color,
      pointBorderColor: color + '88',
      pointRadius: 3,
      pointHoverRadius: 5,
      borderWidth: 2,
      fill: true,
      tension: 0.45,
    };
  }

  // ── History buffers (max 40 points) ───────────────────────────────────────
  const MAX_PTS = 40;
  let histTime       = [];
  let histThroughput = [];
  let histClients    = [];
  let scatterData    = [];   // { x: clientCount, y: throughput }

  // ── Build charts ──────────────────────────────────────────────────────────
  const ctxThroughput = document.getElementById('chart-throughput').getContext('2d');
  const chartThroughput = new Chart(ctxThroughput, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Throughput (msg/s)',
        data: [],
        ...glowLine(C.cyan),
      }],
    },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        ...CHART_DEFAULTS.scales,
        y: { ...CHART_DEFAULTS.scales.y, title: { display: false } },
      },
    },
  });

  const ctxClients = document.getElementById('chart-clients').getContext('2d');
  const chartClients = new Chart(ctxClients, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Clients',
        data: [],
        ...glowLine(C.green),
      }],
    },
    options: { ...CHART_DEFAULTS },
  });

  const ctxScatter = document.getElementById('chart-scatter').getContext('2d');
  const chartScatter = new Chart(ctxScatter, {
    type: 'scatter',
    data: {
      datasets: [{
        label: 'Throughput vs Clients',
        data: [],
        backgroundColor: C.amber + 'aa',
        borderColor: C.amber,
        pointRadius: 5,
        pointHoverRadius: 7,
        borderWidth: 1.5,
      }],
    },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        x: {
          ...CHART_DEFAULTS.scales.x,
          title: {
            display: true,
            text: 'Clients',
            color: C.muted,
            font: { family: "'Space Mono', monospace", size: 9 },
          },
          ticks: { ...CHART_DEFAULTS.scales.x.ticks, stepSize: 1 },
        },
        y: {
          ...CHART_DEFAULTS.scales.y,
          title: {
            display: true,
            text: 'msg / s',
            color: C.muted,
            font: { family: "'Space Mono', monospace", size: 9 },
          },
        },
      },
    },
  });

  const ctxTopics = document.getElementById('chart-topics').getContext('2d');
  const chartTopics = new Chart(ctxTopics, {
    type: 'bar',
    data: {
      labels: [],
      datasets: [{
        label: 'Messages',
        data: [],
        backgroundColor: (ctx) => {
          const colors = [C.red, C.amber, C.cyan, C.green];
          return colors[ctx.dataIndex % colors.length] + 'cc';
        },
        borderColor: (ctx) => {
          const colors = [C.red, C.amber, C.cyan, C.green];
          return colors[ctx.dataIndex % colors.length];
        },
        borderWidth: 1.5,
        borderRadius: 4,
      }],
    },
    options: {
      ...CHART_DEFAULTS,
      indexAxis: 'y',
      scales: {
        x: {
          ...CHART_DEFAULTS.scales.x,
          beginAtZero: true,
        },
        y: {
          ...CHART_DEFAULTS.scales.y,
          grid: { display: false },
          ticks: {
            color: C.muted,
            font: { family: "'Space Mono', monospace", size: 9 },
            maxTicksLimit: 10,
          },
        },
      },
    },
  });

  // ── Update charts ──────────────────────────────────────────────────────────
  function updateCharts(data) {
    const ts = new Date().toLocaleTimeString('en-GB');
    const tput = data.throughput;
    const nclients = data.clients.length;

    // Rolling time-series
    histTime.push(ts);
    histThroughput.push(tput);
    histClients.push(nclients);
    scatterData.push({ x: nclients, y: tput });

    if (histTime.length > MAX_PTS) {
      histTime.shift(); histThroughput.shift();
      histClients.shift(); scatterData.shift();
    }

    // Throughput line
    chartThroughput.data.labels = [...histTime];
    chartThroughput.data.datasets[0].data = [...histThroughput];
    chartThroughput.update('none');

    // Clients line
    chartClients.data.labels = [...histTime];
    chartClients.data.datasets[0].data = [...histClients];
    chartClients.update('none');

    // Scatter
    chartScatter.data.datasets[0].data = [...scatterData];
    chartScatter.update('none');

    // Topics bar — show top 10 by message count
    const sorted = [...data.topics]
      .sort((a, b) => b.messages - a.messages)
      .slice(0, 10);
    chartTopics.data.labels = sorted.map(t => t.name);
    chartTopics.data.datasets[0].data = sorted.map(t => t.messages);
    chartTopics.update('none');
  }

  // ── Main fetch loop ────────────────────────────────────────────────────────
  async function fetchStats() {
    try {
      const res  = await fetch('/api/stats');
      const data = await res.json();

      // ─ Header metrics ─
      document.getElementById('b-addr').textContent =
        `${data.broker_host === '0.0.0.0' ? window.location.hostname : data.broker_host}:${data.broker_port}`;
      document.getElementById('b-uptime').textContent   = fmtUptime(data.uptime);
      document.getElementById('s-clients').textContent  = data.clients.length;
      document.getElementById('s-topics').textContent   =
        data.topics.filter(t => t.subscribers > 0).length;
      document.getElementById('s-messages').textContent = data.total_messages;
      document.getElementById('s-throughput').textContent = data.throughput;

      // ─ Connection endpoints ─
      const host = window.location.hostname;
      document.getElementById('pub-ep').textContent  = `${host}:${data.broker_port}`;
      document.getElementById('sub-ep').textContent  = `${host}:${data.broker_port}`;
      document.getElementById('dash-ep').textContent = `${host}:${window.location.port}`;

      // ─ Clients table ─
      document.getElementById('client-count').textContent = data.clients.length;
      const ctbody = document.getElementById('client-tbody');
      if (!data.clients.length) {
        ctbody.innerHTML = '<tr class="empty-row"><td colspan="6">No clients connected</td></tr>';
      } else {
        ctbody.innerHTML = data.clients.map(c => `
          <tr>
            <td class="mono">${c.addr}</td>
            <td>${roleBadge(c.role)}</td>
            <td>${topicPills(c.topics)}</td>
            <td><span class="num-pub">${c.msg_pub}</span></td>
            <td><span class="num-recv">${c.msg_recv}</span></td>
            <td><span class="duration">${fmtUptime(c.duration)}</span></td>
          </tr>`).join('');
      }

      // ─ Topics table ─
      document.getElementById('topic-count').textContent = data.topics.length;
      const ttbody = document.getElementById('topic-tbody');
      if (!data.topics.length) {
        ttbody.innerHTML = '<tr class="empty-row"><td colspan="3">No topics yet</td></tr>';
      } else {
        ttbody.innerHTML = data.topics.map(t => `
          <tr>
            <td><span class="topic-pill" style="font-size:12px">${t.name}</span></td>
            <td><span class="num-sub">${t.subscribers}</span></td>
            <td><span class="num-msg">${t.messages}</span></td>
          </tr>`).join('');
      }

      // ─ Role breakdown ─
      const pubs  = data.clients.filter(c => c.role === 'publisher').length;
      const subs  = data.clients.filter(c => c.role === 'subscriber').length;
      const unk   = data.clients.filter(c => c.role === 'unknown').length;
      const total = data.clients.length || 1;
      document.getElementById('role-breakdown').innerHTML =
        renderBar('Publishers',  pubs, total, 'var(--amber)') +
        renderBar('Subscribers', subs, total, 'var(--green)') +
        (unk ? renderBar('Unknown', unk, total, 'var(--dim)') : '');

      // ─ Update charts ─
      updateCharts(data);

    } catch (e) {
      console.warn('Stats fetch failed:', e);
    }
  }

  fetchStats();
  setInterval(fetchStats, 2000);
</script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  HTTP request handler
# ═══════════════════════════════════════════════════════════════════════════════

class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass   # suppress per-request console noise

    def do_GET(self):
        if self.path == "/api/stats":
            body = json.dumps(get_stats()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        elif self.path in ("/", "/index.html"):
            body = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        else:
            self.send_response(404)
            self.end_headers()


def start_dashboard():
    server = HTTPServer(("0.0.0.0", DASHBOARD_PORT), DashboardHandler)
    print(f"Dashboard available at  http://<host-ip>:{DASHBOARD_PORT}")
    server.serve_forever()


# ═══════════════════════════════════════════════════════════════════════════════
#  Console performance monitor
# ═══════════════════════════════════════════════════════════════════════════════

def performance_monitor():
    while True:
        time.sleep(15)
        elapsed = max(time.time() - start_time, 1)
        with lock:
            n_clients = len(client_info)
            n_topics  = len(topics)
        print(f"\n── STATS ── clients={n_clients}  topics={n_topics}"
              f"  msgs={messages_processed}"
              f"  throughput={messages_processed/elapsed:.2f} msg/s ──\n")


# ═══════════════════════════════════════════════════════════════════════════════
#  TLS broker server
# ═══════════════════════════════════════════════════════════════════════════════

def start_server():
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(certfile="server.crt", keyfile="server.key")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, PORT))
    sock.listen(50)

    print(f"Secure Broker  listening on  {HOST}:{PORT}  (TLS)")

    # Background threads
    threading.Thread(target=performance_monitor, daemon=True).start()
    threading.Thread(target=start_dashboard,     daemon=True).start()

    while True:
        conn, addr = sock.accept()
        try:
            tls_conn = context.wrap_socket(conn, server_side=True)
        except ssl.SSLError as e:
            print(f"[SSL ERROR] {addr}: {e}")
            conn.close()
            continue

        t = threading.Thread(target=handle_client, args=(tls_conn, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    start_server()
