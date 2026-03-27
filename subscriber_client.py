"""
subscriber_client.py  ─  Secure PubSub Subscriber

Usage:
    python subscriber_client.py --host <broker-ip> [--port 5000] [--cert server.crt]

Examples:
    python subscriber_client.py --host 192.168.1.10
    python subscriber_client.py --host 192.168.1.10 --port 5000 --cert ../broker/server.crt

After connecting you will be prompted to enter one or more topics to subscribe to.
Press Ctrl+C to unsubscribe and exit cleanly.
"""

import socket
import ssl
import threading
import argparse
import sys
import time


# ─── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_PORT = 5000
DEFAULT_CERT = "server.crt"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Secure PubSub Subscriber Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--host", required=True,
        help="Broker server IP address or hostname"
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help=f"Broker port (default: {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--cert", default=DEFAULT_CERT,
        help=f"Path to server certificate for TLS verification (default: {DEFAULT_CERT})"
    )
    parser.add_argument(
        "--topics", nargs="*", default=None,
        help="Topics to subscribe to immediately (space-separated)"
    )
    return parser.parse_args()


# ─── Shared state ─────────────────────────────────────────────────────────────
active_topics: list[str] = []
stop_event = threading.Event()


def receive_messages(client):
    """Background thread: print every incoming MESSAGE from the broker."""
    buffer = ""
    while not stop_event.is_set():
        try:
            chunk = client.recv(4096)
            if not chunk:
                break
            buffer += chunk.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                _handle_line(line)
        except (OSError, ssl.SSLError):
            break

    if not stop_event.is_set():
        print("\n[WARN] Connection to broker lost.")
        stop_event.set()


def _handle_line(line: str):
    """Dispatch a single broker → client line."""
    parts = line.split(":", 2)
    if not parts:
        return
    cmd = parts[0]

    if cmd == "MESSAGE" and len(parts) == 3:
        _, topic, payload = parts
        ts = time.strftime("%H:%M:%S")
        print(f"\n  [{ts}]  {topic}  →  {payload}")

    elif cmd in ("SUBSCRIBED", "UNSUBSCRIBED", "IDENTIFIED"):
        # Already handled in main thread; suppress duplicate prints
        pass

    elif cmd == "ERROR":
        print(f"\n  [BROKER ERROR] {':'.join(parts[1:])}")

    # else: ignore other protocol messages silently


def connect(host, port, cert_path):
    """Establish a TLS connection to the broker and identify as subscriber."""
    context = ssl.create_default_context(cafile=cert_path)
    context.check_hostname = False

    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.settimeout(10)

    client = context.wrap_socket(raw, server_hostname=host)
    client.connect((host, port))
    client.settimeout(None)

    # Identify role
    client.send(b"IDENTIFY:subscriber\n")
    resp = client.recv(1024).decode().strip()
    if not resp.startswith("IDENTIFIED"):
        print(f"[WARN] Unexpected identify response: {resp}")

    print(f"[OK] Connected to broker at {host}:{port}")
    return client


def subscribe(client, topic: str) -> bool:
    """Send SUBSCRIBE and confirm acknowledgement."""
    client.send(f"SUBSCRIBE:{topic}\n".encode())
    resp = client.recv(1024).decode().strip()
    ok   = resp.startswith(f"SUBSCRIBED:{topic}")
    if ok:
        active_topics.append(topic)
        print(f"  ✓ Subscribed to '{topic}'")
    else:
        print(f"  ! Unexpected response: {resp}")
    return ok


def unsubscribe_all(client):
    """Best-effort unsubscribe from all active topics before closing."""
    for topic in active_topics:
        try:
            client.send(f"UNSUBSCRIBE:{topic}\n".encode())
        except Exception:
            pass


def prompt_topics(client):
    """Interactive loop to subscribe to one or more topics."""
    print("\nEnter topic names to subscribe (one per line).")
    print("Press Enter on an empty line when done, or Ctrl+C to exit.\n")

    while True:
        try:
            topic = input("  Subscribe to: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise

        if not topic:
            if active_topics:
                break
            print("  [HINT] Enter at least one topic.")
            continue

        subscribe(client, topic)


def main():
    args = parse_args()

    try:
        client = connect(args.host, args.port, args.cert)
    except FileNotFoundError:
        print(f"[ERROR] Certificate not found: {args.cert}")
        print("        Point --cert at the broker's server.crt file.")
        sys.exit(1)
    except (ConnectionRefusedError, OSError) as e:
        print(f"[ERROR] Cannot reach broker at {args.host}:{args.port} → {e}")
        sys.exit(1)
    except ssl.SSLError as e:
        print(f"[ERROR] TLS handshake failed → {e}")
        sys.exit(1)

    # Subscribe to topics (CLI-supplied or interactive)
    try:
        if args.topics:
            for t in args.topics:
                subscribe(client, t.strip())
        else:
            prompt_topics(client)
    except KeyboardInterrupt:
        print("\n[EXIT] Cancelled before subscribing.")
        client.close()
        return

    if not active_topics:
        print("[EXIT] No topics subscribed. Exiting.")
        client.close()
        return

    print(f"\n[LISTENING] Waiting for messages on: {', '.join(active_topics)}")
    print("            Press Ctrl+C to unsubscribe and exit.\n")

    # Start background receiver
    rx = threading.Thread(target=receive_messages, args=(client,), daemon=True)
    rx.start()

    try:
        stop_event.wait()   # block until connection dies or Ctrl+C
    except KeyboardInterrupt:
        print("\n[EXIT] Keyboard interrupt — unsubscribing…")
    finally:
        stop_event.set()
        unsubscribe_all(client)
        client.close()
        print("[CLOSED] Subscriber disconnected.")


if __name__ == "__main__":
    main()
