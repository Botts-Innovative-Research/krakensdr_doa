#!/bin/bash
#
# Usage:
#   ./gui_run.sh            — start with full web GUI on port 8080
#   ./gui_run.sh --headless — start without GUI; control via WebSocket on 8082

IPADDR="0.0.0.0"
IPPORT="8081"

SHARED_FOLDER="_share"
SHARED_FOLDER_DOA_LOGS="${SHARED_FOLDER}/logs/krakensdr_doa"
SHARED_FOLDER_DAQ_LOGS="${SHARED_FOLDER}/logs/heimdall_daq_fw"

SETTINGS_PATH="${SHARED_FOLDER}/settings.json"

# Parse --headless flag
HEADLESS=false
for arg in "$@"; do
    if [ "$arg" = "--headless" ]; then
        HEADLESS=true
    fi
done

echo "Starting KrakenSDR Direction Finder"
if [ "$HEADLESS" = true ]; then
    echo "Mode: HEADLESS (no GUI — WebSocket only on $IPADDR:8082)"
else
    echo "Mode: GUI (web interface on $IPADDR:8080, WebSocket on $IPADDR:8082)"
fi
echo

# Create shared folders
mkdir -p "${SHARED_FOLDER}"
mkdir -p "${SHARED_FOLDER_DOA_LOGS}"
mkdir -p "${SHARED_FOLDER_DAQ_LOGS}"

# In virtual box there needs to be a delay between folder creation and the code
sync
sleep 0.1

# Start rsync to sync DAQ logs into shared folder
./util/sync_daq_logs.sh >/dev/null 2>/dev/null &

if [ "$HEADLESS" = true ]; then
    # Headless: no Dash GUI, no 8081 file server — WS on 8082 is the sole interface
    echo "WebSocket server will be available at ws://$IPADDR:8082/ws/kraken"
    python3 _ui/_web_interface/headless.py >"${SHARED_FOLDER_DOA_LOGS}/ui.log" 2>&1 &
else
    declare -A STATE_TO_MESSAGE=(["true"]="ENABLED" ["false"]="DISABLED")
    REMOTE_CONTROL="false"
    SERVER_BIN="sudo php -S ${IPADDR}:${IPPORT} -t"
    if [ -f "${SETTINGS_PATH}" ] && [ -x "$(command -v miniserve)" ] && [ -x "$(command -v jq)" ]; then
        REMOTE_CONTROL=$(jq .en_remote_control ${SETTINGS_PATH})
        if [ "$REMOTE_CONTROL" = "true" ]; then
            SERVER_BIN="miniserve -i ${IPADDR} -p ${IPPORT} -P -u -o"
        fi
    fi
    echo "Remote Control (8081) is ${STATE_TO_MESSAGE[$REMOTE_CONTROL]}"
    if [ "$REMOTE_CONTROL" = "false" ]; then
        echo "To enable Remote Control please install miniserve and jq."
        echo "Then change 'en_remote_control' in ${SETTINGS_PATH} to 'true'."
        echo "Finally, apply settings by restarting the software."
    fi
    echo

    echo "Web Interface Running at $IPADDR:8080"
    python3 _ui/_web_interface/app.py >"${SHARED_FOLDER_DOA_LOGS}/ui.log" 2>&1 &

    # Start webserver to share output and settings with clients
    echo "Data Out Server Running at $IPADDR:$IPPORT"
    $SERVER_BIN "${SHARED_FOLDER}" >/miniserve_error.log 2>&1 &
fi

# Start nodejs server for KrakenSDR Pro App
#node _nodejs/index.js 1>/dev/null 2>/dev/null &
node _nodejs/index.js >"${SHARED_FOLDER_DOA_LOGS}/node.log" 2>&1 &
