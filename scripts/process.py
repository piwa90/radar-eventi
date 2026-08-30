#!/usr/bin/env python3
"""
Legge pending.csv, manda ogni voce a Gemini per l'estrazione strutturata,
e aggiunge le righe risultanti a data.csv marcate come Guessing.
"""
import os, csv, json, base64, urllib.request, urllib.error, sys, re

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
MODEL = 'gemini-2.0-flash'
API_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={GEMINI_KEY}'

PENDING = 'pending.csv'
DATA = 'data.csv'

PROMPT = """Sei un assistente che cataloga locali, club, festival, collettivi e spazi culturali legati a musica elettronica, arte e digital art.

Analizza il contenuto fornito ed estrai TUTTE le entità rilevanti (locali, festival, collettivi, artisti in tour).

REGOLE FERREE:
- Estrai SOLO handle Instagram che vedi scritti esplicitamente nel testo o nell'immagine. NON inventare mai un handle basandoti sul nome.
- Se non vedi un handle scritto, lascia il campo link vuoto.
- Se non sei sicuro della città, lascia vuoto invece di indovinare.

Rispondi SOLO con un array JSON, senza testo prima o dopo, senza markdown. Formato:
[{"name":"...","city":"...","country":"...","type":"Musica|Arte|Digital art|Misto|Tour artista","handle":"...","note":"breve descrizione"}]

Se non trovi nulla di rilevante, rispondi: []"""

def call_gemini(parts):
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.1}
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode('utf-8'),
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        return resp['candidates'][0]['content']['parts'][0]['text']
    except urllib.error.HTTPError as e:
        print(f"Errore Gemini HTTP {e.code}: {e.read().decode()[:300]}")
        return None
    except Exception as e:
        print(f"Errore Gemini: {e}")
        return None

def parse_json_response(text):
    if not text:
        return []
    cleaned = re.sub(r'```(?:json)?|```', '', text).strip()
    try:
        return json.loads(cleaned)
    except Exception as e:
        print(f"JSON non valido: {e} | testo: {cleaned[:200]}")
        return []

def main():
    if not GEMINI_KEY:
        print("GEMINI_API_KEY mancante, esco.")
        return
    if not os.path.exists(PENDING):
        print("Nessun pending.csv")
        return

    with open(PENDING, encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    rows = [r for r in rows if r.get('text')]
    if not rows:
        print("Coda vuota.")
        return

    print(f"Voci in coda: {len(rows)}")
    extracted = []

    for row in rows:
        text = row['text']
        parts = []
        m = re.match(r'\[FOTO\]\s+(\S+)\s*\|\s*didascalia:\s*(.*)', text)
        if m:
            img_path, caption = m.group(1), m.group(2)
            if os.path.exists(img_path):
                with open(img_path, 'rb') as imf:
                    b64 = base64.b64encode(imf.read()).decode('utf-8')
                parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
                parts.append({"text": PROMPT + f"\n\nDidascalia allegata: {caption}"})
            else:
                print(f"Immagine non trovata: {img_path}")
                continue
        else:
            parts.append({"text": PROMPT + f"\n\nContenuto da analizzare:\n{text}"})

        result = call_gemini(parts)
        items = parse_json_response(result)
        print(f"  -> estratte {len(items)} entita da: {text[:60]}")
        extracted.extend(items)

    if not extracted:
        print("Nessuna entita estratta.")
        return

    # Carico i link gia presenti per evitare duplicati
    existing_links = set()
    if os.path.exists(DATA):
        with open(DATA, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r.get('link'):
                    existing_links.add(r['link'].strip().lower())

    new_rows = []
    for item in extracted:
        name = (item.get('name') or '').strip()
        if not name:
            continue
        handle = (item.get('handle') or '').strip().lstrip('@')
        link = f"https://www.instagram.com/{handle}" if handle else ''
        if link and link.lower() in existing_links:
            print(f"  gia presente, salto: {name}")
            continue
        if link:
            existing_links.add(link.lower())
        note = (item.get('note') or '').strip()
        note = (note + ' [auto-estratto da Gemini, da verificare]').strip()
        new_rows.append([
            name,
            (item.get('city') or '').strip(),
            (item.get('type') or 'Musica').strip(),
            link,
            note,
            'Guessing'
        ])

    if not new_rows:
        print("Nulla di nuovo da aggiungere.")
        return

    write_header = not os.path.exists(DATA)
    with open(DATA, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(['name','city','type','link','note','conf'])
        for r in new_rows:
            w.writerow(r)
    print(f"Aggiunte {len(new_rows)} righe a {DATA}")

    # Svuoto la coda
    with open(PENDING, 'w', newline='', encoding='utf-8') as f:
        f.write('timestamp,text\n')
    print("Coda svuotata.")

if __name__ == '__main__':
    main()
