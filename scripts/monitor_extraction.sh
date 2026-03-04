#!/usr/bin/env bash
set -euo pipefail

OUTPUT_FILE="$1"
LOG_FILE="/c/Projects/MBKBGVCG/creator-graphrag/data/monitor.log"
ENV_FILE="/c/Projects/MBKBGVCG/creator-graphrag/.env"
SCRIPT_DIR="/c/Projects/MBKBGVCG/creator-graphrag"

echo "[$(date)] Monitor started, watching: $OUTPUT_FILE" | tee -a "$LOG_FILE"

while true; do
    sleep 300  # check every 5 minutes

    # Check if process is still running
    if ! pgrep -f "extract_knowledge_units.py" > /dev/null 2>&1; then
        LAST_LINES=$(tail -20 "$OUTPUT_FILE" 2>/dev/null || echo "")
        
        # Check for error
        if echo "$LAST_LINES" | grep -q "Error\|Traceback\|Exception\|error"; then
            echo "[$(date)] ERROR detected, auto-resuming..." | tee -a "$LOG_FILE"
            echo "$LAST_LINES" >> "$LOG_FILE"
            
            set -a && source "$ENV_FILE" && set +a
            cd "$SCRIPT_DIR"
            NEW_OUT="/c/Users/saura/AppData/Local/Temp/claude/C--Projects-MBKBGVCG/tasks/resume_$(date +%s).output"
            PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 python -u scripts/extract_knowledge_units.py \
                --resume --model "openai/gpt-4.1-mini" --concurrency 5 \
                > "$NEW_OUT" 2>&1 &
            echo "[$(date)] Resumed, new output: $NEW_OUT" | tee -a "$LOG_FILE"
            OUTPUT_FILE="$NEW_OUT"
        else
            echo "[$(date)] Process completed (no error detected)" | tee -a "$LOG_FILE"
            tail -10 "$OUTPUT_FILE" >> "$LOG_FILE"
            break
        fi
    else
        LAST=$(tail -3 "$OUTPUT_FILE" 2>/dev/null | tr '\n' ' ')
        echo "[$(date)] Running — $LAST" | tee -a "$LOG_FILE"
    fi
done
