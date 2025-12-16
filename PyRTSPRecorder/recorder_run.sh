#!/bin/bash

CONFIG_FILE="config.yml"
PID_FILE="recorder.pid"

segment_duration=$(grep 'segment_duration:' "$CONFIG_FILE" | awk '{print $2}')
output_folder=$(grep 'output_folder:' "$CONFIG_FILE" | awk '{print $2}' | tr -d "\"'")

start_process() {
    nohup python3 recorder_main.py > /dev/null 2>&1 &
    echo $! > "$PID_FILE"
}

stop_process() {
    [ -f "$PID_FILE" ] && kill $(cat "$PID_FILE") 2>/dev/null && rm -f "$PID_FILE"
}


start_process

while true; do
    sleep 60

    if [ -f "$PID_FILE" ] && ! kill -0 $(cat "$PID_FILE") 2>/dev/null; then
        start_process
        continue
    fi

    if [ -d "$output_folder" ]; then
        latest=$(find "$output_folder" -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1)
        if [ -n "$latest" ]; then
            diff=$(echo "$(date +%s) - ${latest%.*}" | bc)
            threshold=$((segment_duration + 120))

            if [ "$diff" -gt "$threshold" ]; then
                stop_process
                sleep 2
                start_process
            fi
        fi
    fi
done
