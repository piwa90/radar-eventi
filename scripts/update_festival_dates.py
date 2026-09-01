#!/usr/bin/env python3
"""
Cerca via Gemini (con Google Search grounding) la data della prossima edizione
di ogni festival in data.csv, e aggiorna il campo 'note' con l'informazione trovata.

Un profilo e' considerato festival se "festival" compare (case-insensitive) nel
nome o nella nota, stessa regola usata dall'app per la scheda Festival.

La data trovata viene scritta in coda alla nota esistente, nel formato:
  ... nota originale ... | Prossima edizione: <data> [Certain|Likely|Guessing]

Ad ogni esecuzione il vecchio tag "Prossima edizione: ..." viene rimosso prima
di scriverne uno nuovo, cosi' non si accumulano duplicati settimana dopo settimana.

Se una chiamata Gemini fallisce (rete/quota) o non produce JSON valido, quel
profilo viene semplicemente saltato in questo giro: la nota resta invariata e
verra' ritentato al prossimo run settimanale.
"""
import os, csv, json, re, sys, time, urllib.request, urllib.error

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
# NOTA: sul piano gratuito, il grounding con Google Search per Gemini 3.x ha
# quota giornaliera pari a ZERO (verificato dalla dashboard Rate Limits:
# "Fondatezza della Ricerca" -> Gemini 3 = 0/0). Gemini 2.5 invece ha 1500
# richieste/giorno gratuite per il grounding, quindi qui usiamo questo modello
# anche se il resto del progetto (Telegram) usa gemini-3.6-flash.
MODEL = 'gemini-2.5-flash'
API_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent'

DATA_FILE = 'data.csv'
FIELDNAMES = ['name', 'city', 'type', 'link', 'note', 'conf']
DATE_TAG_RE = re.compile(r'\s*\|\s*Prossima edizione:.*$', re.IGNORECASE)

PROMPT_TEMPLATE = """Cerca sul web quando si svolge la PROSSIMA edizione del festival "{name}" a {city}.

Rispondi SOLO con un oggetto JSON, senza testo prima o dopo, senza markdown, in questo formato:
{{"date": "es. 12-15 giugno 2027, oppure 'non trovato' se non trovi nulla di affidabile", "confidence": "Certain|Likely|Guessing", "note": "una frase breve su cosa hai trovato o perche' non l'hai trovato"}}

Regole:
- "Certain" solo se trovi una fonte ufficiale/recente con date esplicite per la prossima edizione.
- "Likely" se trovi solo il periodo/mese abituale (es. edizioni passate sempre a giugno) ma non le date esatte della prossima.
- "Guessing" se non trovi nulla di specifico e stai solo ipotizzando in base al pattern delle edizioni precedenti.
- Se non trovi assolutamente nulla, rispondi date: "non trovato" e confidence: "Guessing".
"""


def log(msg):
    print(msg, flush=True)


def call_gemini_with_search(name, city):
    body = {
        "contents": [{"parts": [{"text": PROMPT_TEMPLATE.format(name=name, city=city)}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1}
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_KEY}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        text = resp['candidates'][0]['content']['parts'][0]['text']
        return text, True
    except urllib.error.HTTPError as e:
        log(f"Errore Gemini HTTP {e.code} per {name}: {e.read().decode()[:300]}")
        return None, False
    except Exception as e:
        log(f"Errore Gemini per {name}: {type(e).__name__}: {e}")
        return None, False


def parse_response(text):
    if not text:
        return None
    cleaned = re.sub(r'```(?:json)?|```', '', text).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and 'date' in data:
            return data
    except Exception as e:
        log(f"JSON non valido: {e} | testo: {cleaned[:200]}")
    return None


def is_festival(row):
    name = (row.get('name') or '').lower()
    note = (row.get('note') or '').lower()
    return 'festival' in name or 'festival' in note


def update_note(old_note, date_str, confidence):
    base = DATE_TAG_RE.sub('', old_note or '').strip()
    date_str = (date_str or '').strip()
    if date_str and date_str.lower() not in ('non trovato', 'not found', ''):
        tag = f" | Prossima edizione: {date_str} [{confidence or 'Guessing'}]"
    else:
        tag = ""
    return (base + tag).strip()


def main():
    if not GEMINI_KEY:
        log("GEMINI_API_KEY mancante, esco.")
        sys.exit(1)

    if not os.path.exists(DATA_FILE):
        log(f"{DATA_FILE} non trovato, esco.")
        sys.exit(1)

    with open(DATA_FILE, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))

    festivals = [r for r in rows if is_festival(r)]
    log(f"Trovati {len(festivals)} profili festival su {len(rows)} totali.")

    changed = False
    for i, row in enumerate(festivals, 1):
        name, city = row['name'], row.get('city', '')
        log(f"[{i}/{len(festivals)}] Cerco date per: {name} ({city})...")
        text, ok = call_gemini_with_search(name, city)
        if not ok:
            time.sleep(13)
            continue
        parsed = parse_response(text)
        if not parsed:
            time.sleep(13)
            continue
        new_note = update_note(row.get('note', ''), parsed.get('date', ''), parsed.get('confidence', 'Guessing'))
        if new_note != (row.get('note') or ''):
            row['note'] = new_note
            changed = True
            log(f"  -> aggiornato: {new_note}")
        time.sleep(13)  # margine rispetto al limite di 5 RPM di gemini-2.5-flash sul piano gratuito

    if changed:
        with open(DATA_FILE, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            w.writerows(rows)
        log("data.csv aggiornato.")
    else:
        log("Nessuna modifica.")


if __name__ == '__main__':
    main()
