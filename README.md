# Secure PubSub Broker

A TLS-encrypted publish-subscribe message broker for real-time messaging across multiple devices on a network, with a built-in live web dashboard.

```
 Publisher Device               Broker (Server)               Subscriber Device
 ────────────────               ───────────────               ─────────────────
 publisher_client.py  ──TLS──▶  broker_server.py  ──TLS──▶  subscriber_client.py
                                       │
                                       ▼
                               Dashboard :8080
```

---

## File Structure

```
broker/
├── broker_server.py      # Broker + web dashboard — runs on the server machine
├── server.crt            # TLS certificate — copy this to every client device
├── server.key            # TLS private key — keep on server only, never share
└── protocol.py           # Wire protocol constants

clients/                  # Copy these to each client device
├── publisher_client.py
├── subscriber_client.py
├── protocol.py
└── server.crt            # Copied from broker/
```

---

## Requirements

- Python 3.10+
- `openssl` (for certificate generation — pre-installed on Linux/macOS; on Windows use Git Bash or WSL)
- All devices must be on the same LAN, or have appropriate port forwarding

---

## Setup & Running

### Step 1 — Find the Broker Machine's IP

```bash
# Linux / macOS
hostname -I

# Windows
ipconfig    # look for IPv4 Address
```

Note this IP — you will use it everywhere. Example: `10.1.4.59`

---

### Step 2 — Generate TLS Certificate (Broker Machine, Once)

Run this once in the `broker/` directory, replacing the IP with your broker's actual IP:

```bash
openssl req -x509 -newkey rsa:4096 -keyout server.key -out server.crt \
  -days 365 -nodes \
  -subj "/CN=pubsub-broker" \
  -addext "subjectAltName=IP:127.0.0.1,IP:10.1.4.59"
```

This generates `server.crt` and `server.key`. Copy only `server.crt` to every client device.

---

### Step 3 — Start the Broker

On the server machine, open ports 5000 and 8080 in your firewall, then run:

```bash
python3 broker_server.py
```

Expected output:
```
Secure Broker  listening on  0.0.0.0:5000  (TLS)
Dashboard available at  http://10.1.4.59:8080
```

---

### Step 4 — Run a Subscriber (Client Device)

```bash
# Interactive — prompts you to enter topics after connecting
python3 subscriber_client.py --host 10.1.4.59

# Pre-specify topics directly
python3 subscriber_client.py --host 10.1.4.59 --topics sensors/temperature alerts

# Custom port or cert path
python3 subscriber_client.py --host 10.1.4.59 --port 5000 --cert /path/to/server.crt
```

You will see incoming messages as:
```
  [14:32:01]  sensors/temperature  →  23.5
```

Press `Ctrl+C` to unsubscribe cleanly and exit.

---

### Step 5 — Run a Publisher (Client Device)

```bash
# Interactive prompt
python3 publisher_client.py --host 10.1.4.59

# Custom port or cert path
python3 publisher_client.py --host 10.1.4.59 --port 5000 --cert /path/to/server.crt
```

At the prompt, enter a topic and message:
```
──────────────────────────────────────────
  Topic   : sensors/temperature
  Message : 23.5
  Server  : ✓
```

Type `quit` to exit.

---

### Step 6 — View the Dashboard

Open in any browser on any device on the network:

```
http://10.1.4.59:8080
```

The dashboard refreshes every 2 seconds and shows:

- **Live Charts** — Throughput over time, Client count over time, Throughput vs Clients (scatter), and Messages per Topic
- **System Metrics** — Connected clients, active topics, total messages, throughput (msg/s)
- **Clients Table** — Each client's IP, role, subscribed topics, messages published/received, connection duration
- **Topic Registry** — All topics with subscriber count and total message count
- **Activity Breakdown** — Publisher vs subscriber split

Raw JSON stats are also available at `http://10.1.4.59:8080/api/stats`.

---

## Protocol Reference

All messages are newline-terminated UTF-8 strings over a TLS TCP connection.

| Direction       | Command                     | Description                 |
|-----------------|-----------------------------|-----------------------------|
| Client → Broker | `IDENTIFY:<role>`           | Declare role on connect     |
| Client → Broker | `SUBSCRIBE:<topic>`         | Subscribe to a topic        |
| Client → Broker | `UNSUBSCRIBE:<topic>`       | Unsubscribe from a topic    |
| Client → Broker | `PUBLISH:<topic>:<message>` | Publish a message           |
| Broker → Client | `IDENTIFIED:<role>`         | Confirm identification      |
| Broker → Client | `SUBSCRIBED:<topic>`        | Confirm subscription        |
| Broker → Client | `UNSUBSCRIBED:<topic>`      | Confirm unsubscription      |
| Broker → Client | `MESSAGE:<topic>:<message>` | Delivered message           |
| Broker → Client | `OK`                        | Publish acknowledged        |
| Broker → Client | `ERROR:<reason>`            | Error response              |

---

## Ports

| Port | Protocol | Purpose                           |
|------|----------|-----------------------------------|
| 5000 | TCP/TLS  | Broker — publishers & subscribers |
| 8080 | HTTP     | Dashboard — browser access        |

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Certificate not found` | Wrong `--cert` path | Point `--cert` at your `server.crt` |
| `Connection refused` | Broker not running or wrong IP | Check broker is started; verify `--host` |
| `SSL handshake failed` | Cert generated with a different IP | Re-run `openssl` with the correct IP in `--addext` |
| Dashboard blank | Firewall blocking port 8080 | Open port 8080 on the broker machine |
| Messages not arriving | Subscriber connected after publish | PubSub is live-only — subscribers must connect before a message is published |
