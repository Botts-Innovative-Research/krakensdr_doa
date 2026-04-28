# KrakenSDR Signal Processor — headless entry point
#
# Starts the signal-processing pipeline and WebSocket server (port 8082)
# without the Dash/Quart web GUI.  All data (DoA, spectrum, settings) is
# streamed via the WebSocket, and parameters can be updated by sending
# command messages to the same socket — no browser, no 8081 file server
# required.
#
# Usage:
#   python3 headless.py
#
# WebSocket endpoint:  ws://<host>:8082/ws/kraken
#
# Outbound message types (server → client):
#   { "type": "doa",      "timestamp": <ms>, ... }
#   { "type": "spectrum", "timestamp": <ms>, ... }
#   { "type": "settings", "timestamp": <ms>, ... }
#
# Inbound command (client → server):
#   {
#     "type": "command",
#     "action": "update_settings",
#     "data": { <partial or full settings dict> }
#   }
#
# - coding: utf-8 -*-

import threading

# variables.py inserts the receiver / signal-processor / ui paths into
# sys.path, so it must be imported before any of those modules.
import variables  # noqa: F401 (side-effect import)

import kraken_ws_server
from kraken_web_interface import WebInterface
from utils import fetch_dsp_data, fetch_gps_data

# ── bootstrap ─────────────────────────────────────────────────────────────────

# Create the core pipeline: receiver + signal processor + settings watcher.
# The signal processor thread starts inside WebInterface.__init__().
web_interface = WebInterface()

# Wire inbound WS commands to the web interface.
kraken_ws_server.register_command_handler(web_interface.handle_ws_command)

# Start the WebSocket server (port 8082) in a background daemon thread.
kraken_ws_server.start_server(host="0.0.0.0", port=8082)


def _start_data_pumps():
    """Start the DSP and GPS data-pump timers.

    Passing app=None skips all Dash push_mods calls — the pumps still drain
    the signal-processor queues and broadcast data via WebSocket.
    """
    fetch_dsp_data(None, web_interface, None, None)
    fetch_gps_data(None, web_interface)


# Small delay so the signal processor has time to produce its first frame
# before the pumps start polling.
threading.Timer(3.0, _start_data_pumps).start()

# ── run forever ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)
    logging.getLogger(__name__).info(
        "KrakenSDR headless mode running — WebSocket on ws://0.0.0.0:8082/ws/kraken"
    )
    # Block the main thread; all real work happens in daemon threads.
    threading.Event().wait()
