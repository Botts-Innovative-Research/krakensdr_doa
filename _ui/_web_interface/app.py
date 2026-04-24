# KrakenSDR Signal Processor
#
# Copyright (C) 2018-2021  Carl Laufer, Tamás Pető
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
#
# - coding: utf-8 -*-

# isort: off
from maindash import app, spectrum_fig, waterfall_fig, web_interface

# isort: on

import threading

import kraken_ws_server
from utils import fetch_dsp_data, fetch_gps_data
from views import main

app.layout = main.layout

# It is workaround for splitting callbacks in separate files (run callbacks after layout)
from callbacks import display_page, main, update_daq_params  # noqa: F401

# Register the WebInterface as the handler for inbound WS commands so that
# external clients can update settings via ws://host:8082/ws/kraken.
kraken_ws_server.register_command_handler(web_interface.handle_ws_command)

# Start the standalone WebSocket server immediately — pure stdlib, no Quart or
# hypercorn required, completely independent of the dash_devices server.
kraken_ws_server.start_server(host="0.0.0.0", port=8082)


def _start_data_pumps():
    """Start the data-pump timers after the Dash server has had time to init.

    fetch_gps_data calls app.push_mods() immediately, which needs the Dash
    server running.  A short delay avoids calling it before run_server() has
    finished its own setup.
    """
    fetch_dsp_data(app, web_interface, spectrum_fig, waterfall_fig)
    fetch_gps_data(app, web_interface)


# 3-second delay gives app.run_server() time to start before push_mods fires.
threading.Timer(3.0, _start_data_pumps).start()

if __name__ == "__main__":
    # Debug mode does not work when the data interface is set to shared-memory
    # "shmem"!
    app.run_server(debug=False, host="0.0.0.0", port=8080)