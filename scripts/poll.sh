#!/bin/bash
set -e

PENDING_FILE="pending.csv"
mkdir -p pending
mkdir -p pending/images

if [ ! -f "$PENDING_FILE" ]; then
  echo "timestamp,text" > "$PENDING_FILE"
fi

poll_bot() {
  local BOT_TOKEN="$1"
  local OFFSET_FILE="$2"
  local AUTO_PREFIX="$3"
  local BOT_LABEL="$4"

  if [ -z "$BOT_TOKEN" ]; then
    echo "[$BOT_LABEL] token assente, salto."
    return 0
  fi

  local OFFSET
  OFFSET=$(cat "$OFFSET_FILE" 2>/dev/null || echo "")
  if [ -z "$OFFSET" ]; then
    OFFSET=0
  fi
  echo "[$BOT_LABEL] offset attuale: $OFFSET"

  local RESPONSE
  RESPONSE=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?offset=${OFFSET}&timeout=0")

  local OK
  OK=$(echo "$RESPONSE" | jq -r '.ok')
  if [ "$OK" != "true" ]; then
    echo "[$BOT_LABEL] errore da Telegram: $RESPONSE"
    return 0
  fi

  local COUNT
  COUNT=$(echo "$RESPONSE" | jq '.result | length')
  echo "[$BOT_LABEL] aggiornamenti ricevuti: $COUNT"

  if [ "$COUNT" -eq 0 ]; then
    echo "[$BOT_LABEL] nessun nuovo messaggio."
    return 0
  fi

  local NEW_OFFSET
  NEW_OFFSET=$(echo "$RESPONSE" | jq '[.result[].update_id] | max + 1')

  local NEW_TEXT_LINES
  NEW_TEXT_LINES=$(echo "$RESPONSE" | jq -r --arg prefix "$AUTO_PREFIX" '
    .result[]
    | select(.message.text != null)
    | select(.message.text | startswith("/start") | not)
    | [(now | strftime("%Y-%m-%dT%H:%M:%SZ")), ($prefix + .message.text)]
    | @csv
  ')
  if [ -n "$NEW_TEXT_LINES" ]; then
    echo "$NEW_TEXT_LINES" >> "$PENDING_FILE"
    echo "[$BOT_LABEL] aggiunti messaggi di testo"
  fi

  local PHOTO_UPDATE_IDS
  PHOTO_UPDATE_IDS=$(echo "$RESPONSE" | jq -r '.result[] | select(.message.photo != null) | .update_id')

  for UID_PHOTO in $PHOTO_UPDATE_IDS; do
    echo "[$BOT_LABEL] elaboro foto update_id=$UID_PHOTO"

    local FILE_ID CAPTION FILE_INFO FILE_PATH
    FILE_ID=$(echo "$RESPONSE" | jq -r --arg uid "$UID_PHOTO" '.result[] | select(.update_id == ($uid|tonumber)) | .message.photo | sort_by(.file_size) | last | .file_id')
    CAPTION=$(echo "$RESPONSE" | jq -r --arg uid "$UID_PHOTO" '.result[] | select(.update_id == ($uid|tonumber)) | .message.caption // ""')

    FILE_INFO=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getFile?file_id=${FILE_ID}")
    FILE_PATH=$(echo "$FILE_INFO" | jq -r '.result.file_path')

    if [ "$FILE_PATH" != "null" ] && [ -n "$FILE_PATH" ]; then
      local EXT LOCAL_PATH CAPTION_ESCAPED TS
      EXT="${FILE_PATH##*.}"
      LOCAL_PATH="pending/images/${BOT_LABEL}_${UID_PHOTO}.${EXT}"
      curl -s "https://api.telegram.org/file/bot${BOT_TOKEN}/${FILE_PATH}" -o "$LOCAL_PATH"
      echo "[$BOT_LABEL] salvata foto in $LOCAL_PATH"

      CAPTION_ESCAPED=$(echo "${AUTO_PREFIX}${CAPTION}" | sed 's/"/""/g')
      TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
      echo "${TS},\"[FOTO] ${LOCAL_PATH} | didascalia: ${CAPTION_ESCAPED}\"" >> "$PENDING_FILE"
    else
      echo "[$BOT_LABEL] impossibile scaricare la foto per update_id=$UID_PHOTO"
    fi
  done

  echo "$NEW_OFFSET" > "$OFFSET_FILE"
  echo "[$BOT_LABEL] nuovo offset salvato: $NEW_OFFSET"
}

poll_bot "${TELEGRAM_TOKEN}" "pending/tg_offset.txt" "" "eventi"
poll_bot "${TELEGRAM_TOKEN_ARTISTI}" "pending/tg_offset_artisti.txt" "#artista " "artisti"
