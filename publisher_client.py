"""
publisher_client.py  ─  Secure PubSub Publisher

Usage:
    python publisher_client.py --host <broker-ip> [--port 5000] [--cert server.crt]

Examples:
    python publisher_client.py --host 192.168.1.10
    python publisher_client.py --host 192.168.1.10 --port 5000 --cert ../broker/server.crt
"""

import socket
import ssl
import argparse
import sys


# ─── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_PORT = 5000
DEFAULT_CERT = "server.crt"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Secure PubSub Publisher Client",
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
    return parser.parse_args()


def connect(host, port, cert_path):
    """Establish a TLS connection to the broker and identify as publisher."""
    context = ssl.create_default_context(cafile=cert_path)
    # Self-signed certs don't embed a hostname that matches the server IP,
    # so hostname verification is disabled while certificate verification
    # (chain + signature) remains fully enforced via the cafile above.
    context.check_hostname = False

    raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw.settimeout(10)

    client = context.wrap_socket(raw, server_hostname=host)
    client.connect((host, port))
    client.settimeout(None)     # blocking mode after connect

    # Tell the broker our role
    client.send(b"IDENTIFY:publisher\n")
    resp = client.recv(1024).decode().strip()
    if not resp.startswith("IDENTIFIED"):
        print(f"[WARN] Unexpected identify response: {resp}")

    print(f"[OK] Connected to broker at {host}:{port}")
    print("     Type  'quit'  to exit.\n")
    return client


def publish(client, topic, message):
    """Send a PUBLISH command and return the broker's response."""
    client.send(f"PUBLISH:{topic}:{message}\n".encode())
    return client.recv(1024).decode().strip()


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

    try:
        while True:
            print("─" * 42)
            topic = input("  Topic   : ").strip()
            if topic.lower() in ("quit", "exit", "q"):
                break
            if not topic:
                print("  [SKIP] Topic cannot be empty.")
                continue

            message = input("  Message : ").strip()
            if not message:
                print("  [SKIP] Message cannot be empty.")
                continue

            # Warn if message contains a colon that might confuse older brokers
            response = publish(client, topic, message)
            status   = "✓" if response == "OK" else f"! {response}"
            print(f"  Server  : {status}")

    except KeyboardInterrupt:
        print("\n[EXIT] Keyboard interrupt.")
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        print(f"\n[ERROR] Lost connection to broker: {e}")
    finally:
        client.close()
        print("[CLOSED] Publisher disconnected.")


if __name__ == "__main__":
    main()
