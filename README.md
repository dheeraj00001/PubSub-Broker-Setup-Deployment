# PubSub-Broker-Setup-Deployment
A TLS-encrypted publish–subscribe message broker that works across multiple physical devices on a network, with a built-in real-time web dashboard.

```
 Device A                    Broker (server)                   Device B
 ──────────                  ───────────────                   ──────────
 publisher_client.py  ──TLS──▶  broker_server.py  ──TLS──▶  subscriber_client.py
                                       │
                                       ▼
                               Dashboard :8080
                               (any browser)
```

## File Layout

```
broker/
├── broker_server.py      # Broker + web dashboard (runs on the server)
├── server.crt            # TLS certificate  (distribute to all clients)
├── server.key            # TLS private key  (keep on server only!)
└── protocol.py           # Protocol constants

clients/                  # Copy to each client device
├── publisher_client.py
├── subscriber_client.py
├── protocol.py
└── server.crt            # Copy from broker/
```

---

## 1 · Generating a Self-Signed TLS Certificate

Run once on the broker machine:

```bash
openssl req -x509 -newkey rsa:4096 -keyout server.key -out server.crt \
  -days 365 -nodes \
  -subj "/CN=pubsub-broker" \
  -addext "subjectAltName=IP:127.0.0.1,IP:<YOUR_SERVER_IP>"
```

Replace `<YOUR_SERVER_IP>` with the broker machine's LAN IP (e.g. `192.168.1.10`).

> **Important** — copy `server.crt` (not `server.key`) to every client device
> that needs to connect.

---

## 2 · Starting the Broker

```bash
# On the server machine, in the broker/ directory:
python broker_server.py
```

Output:
```
Secure Broker  listening on  0.0.0.0:5000  (TLS)
Dashboard available at  http://<host-ip>:8080
```

Open `http://<broker-ip>:8080` in any browser on the network to see the
real-time dashboard.

---

## 3 · Running a Publisher

```bash
# On any device that has server.crt:
python publisher_client.py --host <broker-ip>

# With explicit cert path:
python publisher_client.py --host 192.168.1.10 --cert /path/to/server.crt
```

At the prompt, enter a topic and message:
```
──────────────────────────────────────────
  Topic   : sensors/temperature
  Message : 23.5
  Server  : ✓
```

---

## 4 · Running a Subscriber

```bash
# Interactive topic entry:
python subscriber_client.py --host <broker-ip>

# Pre-specify topics via CLI (no interactive prompt):
python subscriber_client.py --host 192.168.1.10 --topics sensors/temperature alerts
```

Incoming messages print as:
```
  [14:32:01]  sensors/temperature  →  23.5
```

Press **Ctrl+C** to unsubscribe cleanly and exit.

---

## 5 · Dashboard

| URL                      | Content                                    |
|--------------------------|--------------------------------------------|
| `http://<host>:8080/`    | Live monitoring dashboard                  |
| `http://<host>:8080/api/stats` | Raw JSON stats (poll or integrate) |

Dashboard panels:

- **System Metrics** — connected clients, active topics, total messages, throughput
- **Connected Clients** — IP:port, role (publisher/subscriber), subscribed topics,
  messages published/received, connection duration
- **Topic Registry** — topic name, subscriber count, total messages
- **Activity Breakdown** — visual role split; connection endpoints

---

## 6 · Protocol Reference

All messages are **newline-terminated UTF-8** strings over a TLS TCP connection.

| Direction        | Command                        | Description                    |
|------------------|--------------------------------|--------------------------------|
| Client → Broker  | `IDENTIFY:<role>`              | Declare role on connect        |
| Client → Broker  | `SUBSCRIBE:<topic>`            | Subscribe to a topic           |
| Client → Broker  | `UNSUBSCRIBE:<topic>`          | Unsubscribe from a topic       |
| Client → Broker  | `PUBLISH:<topic>:<message>`    | Publish a message              |
| Broker → Client  | `IDENTIFIED:<role>`            | Confirm identification         |
| Broker → Client  | `SUBSCRIBED:<topic>`           | Confirm subscription           |
| Broker → Client  | `UNSUBSCRIBED:<topic>`         | Confirm unsubscription         |
| Broker → Client  | `MESSAGE:<topic>:<message>`    | Delivered message              |
| Broker → Client  | `OK`                           | Publish acknowledged           |
| Broker → Client  | `ERROR:<reason>`               | Error response                 |

---

## 7 · Ports Reference

| Port | Protocol | Purpose                              |
|------|----------|--------------------------------------|
| 5000 | TCP/TLS  | Broker — publishers & subscribers    |
| 8080 | HTTP     | Dashboard — browser access           |

Make sure your firewall allows inbound connections on both ports if clients
are on different network segments.

---

## 8 · Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Certificate not found` | Wrong `--cert` path | Point `--cert` at your `server.crt` |
| `Connection refused` | Broker not running / wrong IP | Check broker is started; verify `--host` |
| `SSL handshake failed` | Wrong cert or IP not in SAN | Regenerate cert with correct `--addext` IP |
| Dashboard blank | Browser can't reach port 8080 | Check firewall; use broker's LAN IP |
| Messages not delivered | Subscriber joined after publish | PubSub is live-only — no message persistence |
