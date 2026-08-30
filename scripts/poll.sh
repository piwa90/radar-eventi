#!/bin/bash
set -e

OFFSET_FILE="pending/tg_offset.txt"
PENDING_FILE="pending.csv"

mkdir -p pending
mkdir -p pending/images

OFFSET=$(cat "$OFFSET_FILE" 2>/dev/null || echo "")
if [ -z "$OFFSET" ]; then
  OFFSET=0
fi

echo "Offset attuale: $OFFSET"

RESPONSE=$(curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getUpdates?offset=${OFFSET}&timeout=0")

OK=$(echo "$RESPONSE" | jq -r '.ok')
if [ "$OK" != "true" ]; then
  echo "Errore da Telegram: $RESPONSE"
  exit 0
fi

COUNT=$(echo "$RESPONSE" | jq '.result | length')
echo "Aggiornamenti ricevuti: $COUNT"

if [ "$COUNT" -eq 0 ]; then
  echo "Nessun nuovo messaggio."
  exit 0
fi

NEW_OFFSET=$(echo "$RESPONSE" | jq '[.result[].update_id] | max + 1')

if [ ! -f "$PENDING_FILE" ]; then
  echo "timestamp,text" > "$PENDING_FILE"
fi

# --- Messaggi di testo (non /start) ---
NEW_TEXT_LINES=$(echo "$RESPONSE" | jq -r '
  .result[]
  | select(.message.text != null)
  | select(.message.text | startswith("/start") | not)
  | [(now | strftime("%Y-%m-%dT%H:%M:%SZ")), .message.text]
  | @csv
')
if [ -n "$NEW_TEXT_LINES" ]; then
  echo "$NEW_TEXT_LINES" >> "$PENDING_FILE"
  echo "Aggiunti messaggi di testo"
fi

# --- Foto: scarico il file e lo salvo nel repo ---
PHOTO_UPDATE_IDS=$(echo "$RESPONSE" | jq -r '.result[] | select(.message.photo != null) | .update_id')

for UID_PHOTO in $PHOTO_UPDATE_IDS; do
  echo "Elaboro foto update_id=$UID_PHOTO"

  FILE_ID=$(echo "$RESPONSE" | jq -r --arg uid "$UID_PHOTO" '.result[] | select(.update_id == ($uid|tonumber)) | .message.photo | sort_by(.file_size) | last | .file_id')
  CAPTION=$(echo "$RESPONSE" | jq -r --arg uid "$UID_PHOTO" '.result[] | select(.update_id == ($uid|tonumber)) | .message.caption // ""')

  FILE_INFO=$(curl -s "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getFile?file_id=${FILE_ID}")
  FILE_PATH=$(echo "$FILE_INFO" | jq -r '.result.file_path')

  if [ "$FILE_PATH" != "null" ] && [ -n "$FILE_PATH" ]; then
    EXT="${FILE_PATH##*.}"
    LOCAL_PATH="pending/images/${UID_PHOTO}.${EXT}"
    curl -s "https://api.telegram.org/file/bot${TELEGRAM_TOKEN}/${FILE_PATH}" -o "$LOCAL_PATH"
    echo "Salvata foto in $LOCAL_PATH"

    CAPTION_ESCAPED=$(echo "$CAPTION" | sed 's/"/""/g')
    TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    echo "${TS},\"[FOTO] ${LOCAL_PATH} | didascalia: ${CAPTION_ESCAPED}\"" >> "$PENDING_FILE"
  else
    echo "Impossibile scaricare la foto per update_id=$UID_PHOTO"
  fi
done

echo "$NEW_OFFSET" > "$OFFSET_FILE"
echo "Nuovo offset salvato: $NEW_OFFSET"
