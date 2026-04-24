# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KrakenSDR DoA DSP is a real-time direction of arrival (DoA) estimation system for the KrakenSDR and compatible RTL-SDR coherent receiver arrays. It processes IQ data from the Heimdall DAQ firmware to determine the bearing of RF signals.

## Development Commands

### Environment Setup
```bash
conda activate kraken  # Required before any Python work
```

### Running the Application
```bash
./util/kraken_doa_start.sh   # Full startup: DAQ + DSP (local hardware)
./gui_run.sh                  # DSP/UI only (DAQ runs remotely)
./kill.sh                     # Stop all services
```

### Code Quality (pre-commit hooks)
```bash
pre-commit install            # Set up hooks once
pre-commit run --all-files    # Run manually
```

Formatting tools and their config (in `pyproject.toml`):
- **Black**: line length 120
- **isort**: profile `black`
- **Flake8**: line length 120, ignores `DUO107,E402,E501,E203,W503`

CI runs isort+Black (formatting) and Flake8 (linting) on all push/PR.

## Architecture

### Three Main Subsystems

**1. Signal Processing (`_sdr/`)**
- `_receiver/kraken_sdr_receiver.py` — Acquires IQ data from Heimdall DAQ via Ethernet (`eth` mode, port 5000) or shared memory (`shmem` mode). Configures frequency, gain, AGC.
- `_signal_processing/kraken_sdr_signal_processor.py` — Core `threading.Thread` running DoA estimation (Bartlett, Capon, MEM, TNA, MUSIC, ROOT-MUSIC via **pyargus**). Uses **Numba JIT** for performance-critical paths — first run is slow due to compilation.
- `_signal_processing/signal_utils.py` — FM demodulation, FIR/Butterworth filters, IQ file I/O.

**2. Web UI (`_ui/_web_interface/`)**
- `app.py` — Entry point. Starts Quart/Dash server (port 8080), WebSocket server (port 8082), and periodic data pump timers.
- `kraken_web_interface.py` — Main coordinator: manages queues between receiver, signal processor, and UI; monitors `_share/settings.json` via timestamp polling.
- `callbacks/main.py` — All Dash callback logic (~51K, core of UI interactivity).
- `utils.py` — `fetch_dsp_data()` and `fetch_gps_data()` poll signal processor and push to Dash via `app.push_mods()`.
- `kraken_ws_server.py` — Custom RFC 6455 WebSocket server (asyncio/TCP, no external deps). Broadcasts DoA/spectrum JSON to `ws://<host>:8082/ws/kraken`.
- `views/` — Individual UI card components (DAQ config, DSP config, VFO, station config, etc.).

**3. Node.js Middleware (`_nodejs/`)**
- `index.js` — Express REST API (port 8042) + WebSocket client (port 8021). Proxies data to `wss://map.krakenrf.com:2096`, provides settings load/save API.

### Data Flow
```
Heimdall DAQ → ReceiverRTLSDR → SignalProcessor (Thread)
    → WebInterface (queues) → Dash UI (port 8080)
                            → WebSocket broadcast (port 8082)
    ← settings.json ← Node.js API (port 8042) ← Remote clients
                    ← miniserve/PHP (port 8081)
```

### Key Shared State
- `_share/settings.json` — Runtime configuration; watched by timestamp polling, written by UI callbacks and Node.js API.
- `_share/status.json` — Current system status pushed from signal processor.
- `_share/logs/` — Per-module log files.
- `_data_control/` — Shared memory control flags for DAQ↔DSP synchronization.

### Port Map
| Port | Service |
|------|---------|
| 5000 | Heimdall DAQ (Ethernet IQ stream) |
| 8080 | Web UI (Dash/Quart) |
| 8081 | File server (miniserve or PHP) |
| 8082 | WebSocket server (custom asyncio) |
| 8042 | Node.js REST API |
| 8021 | Node.js WebSocket endpoint |

## Important Development Notes

- **Numba JIT**: `kraken_sdr_signal_processor.py` uses `@numba.jit` decorators. Expect 1-2 min startup on first run; compiled cache speeds up subsequent runs.
- **Settings watching**: `_share/settings.json` changes are detected via `os.path.getmtime()` polling in `utils.py`, not inotify — changes apply on the next poll cycle.
- **Remote vs local DAQ**: Switch between `eth` and `shmem` receiver modes in settings. Shared memory mode requires Heimdall DAQ on the same machine.
- **Heimdall DAQ dependency**: The `../heimdall_daq_fw/` sibling directory is expected to exist for full local operation. DAQ `daq_chain_config.ini` is loaded from that path.
- **Conda environment**: The `kraken` conda environment must be active; the app is not designed for system Python.