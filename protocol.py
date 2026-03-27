# ── protocol.py ───────────────────────────────────────────────────────────────
# Wire-protocol command constants shared across broker and clients.
# All messages are newline-terminated UTF-8 strings.
#
# Client → Broker:
#   IDENTIFY:<role>
#   SUBSCRIBE:<topic>
#   UNSUBSCRIBE:<topic>
#   PUBLISH:<topic>:<message>
#
# Broker → Client:
#   IDENTIFIED:<role>
#   SUBSCRIBED:<topic>
#   UNSUBSCRIBED:<topic>
#   MESSAGE:<topic>:<message>
#   OK
#   ERROR:<reason>
# ─────────────────────────────────────────────────────────────────────────────

# Client → Broker
IDENTIFY    = "IDENTIFY"
SUBSCRIBE   = "SUBSCRIBE"
UNSUBSCRIBE = "UNSUBSCRIBE"
PUBLISH     = "PUBLISH"

# Broker → Client
IDENTIFIED   = "IDENTIFIED"
SUBSCRIBED   = "SUBSCRIBED"
UNSUBSCRIBED = "UNSUBSCRIBED"
MESSAGE      = "MESSAGE"
OK           = "OK"
ERROR        = "ERROR"
