#!/bin/bash
set -e

OFFSET_FILE="pending/tg_offset.txt"
PENDING_FILE="pending.csv"

# Leggo l'offset attuale (se non esiste, parto da 0)
if [ -f "$OFFSET_FILE" ]; then
  OFFSET=$(cat "$OFFSET_FILE")
else
  mkdir -p pending
  OFFSET=0
fi

echo "Offset attuale: $OFFSET"

# Chiamo Telegram
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

# Calcolo il nuovo offset (max update_id + 1)
NEW_OFFSET=$(echo "$RESPONSE" | jq '[.result[].update_id] | max + 1')

# Estraggo i messaggi di testo che non sono /start, con timestamp
NEW_LINES=$(echo "$RESPONSE" | jq -r '
  .result[]
  | select(.message.text != null)
  | select(.message.text | startswith("/start") | not)
  | [(now | strftime("%Y-%m-%dT%H:%M:%SZ")), .message.text]
  | @csv
')

if [ -z "$NEW_LINES" ]; then
  echo "Solo /start o comandi, nessun contenuto utile."
else
  if [ ! -f "$PENDING_FILE" ]; then
    echo "timestamp,text" > "$PENDING_FILE"
  fi
  echo "$NEW_LINES" >> "$PENDING_FILE"
  echo "Aggiunte nuove righe a $PENDING_FILE"
fi

echo "$NEW_OFFSET" > "$OFFSET_FILE"
echo "Nuovo offset salvato: $NEW_OFFSET"
