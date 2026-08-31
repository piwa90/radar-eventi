#!/usr/bin/env python3
"""
Legge pending.csv, manda ogni voce a Gemini per l'estrazione strutturata,
smista il risultato nel file giusto in base a un prefisso (#artista = artisti visual,
default = eventi), e aggiunge le righe risultanti marcate come Guessing.

IMPORTANTE: una riga viene rimossa dalla coda SOLO se Gemini ha risposto
correttamente (anche con lista vuota = nulla di riconoscibile). Se la chiamata
API fallisce (errore di rete, timeout, chiave, quota) la riga RESTA in coda
per essere ritentata al prossimo giro, cosi' nessun messaggio va mai perso
per un problema temporaneo.
"""
import os, csv, json, base64, urllib.request, urllib.error, sys, re

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
MODEL = 'gemini-3.6-flash'
API_URL = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent'

PENDING = 'pending.csv'

SECTION_TAGS = {
    '#artista': 'artisti-visual.csv',
    '#art': 'artisti-visual.csv',
    '#digital': 'artisti-visual.csv',
    '#visual': 'artisti-visual.csv',
}
DEFAULT_TARGET = 'data.csv'

PROMPT_EVENTI = """Sei un assistente che cataloga locali, club, festival, collettivi, spazi culturali E artisti musicali in tour, legati a musica elettronica, arte e digital art.

Analizza il contenuto fornito ed estrai TUTTE le entita rilevanti.

DISTINZIONE FONDAMENTALE - ARTISTA vs VENUE:
Un artista (persona o collettivo che si esibisce/crea) e una venue (locale, festival, spazio fisico che ospita) sono DUE COSE DIVERSE. Se il contenuto menziona ENTRAMBI insieme (es. "tal DJ suona da tal club", una locandina con artista + venue), estrai SEMPRE due righe separate, mai una sola:
- una riga per l'artista, con type "Tour artista"
- una riga per la venue/il locale/il festival, con il suo type proprio (Musica/Arte/Digital art/Misto)
Non mescolare mai le informazioni delle due entita in una singola riga.

REGOLE FERREE:
- Estrai SOLO handle Instagram che vedi scritti esplicitamente nel testo o nell'immagine. NON inventare mai un handle basandoti sul nome.
- Se non vedi un handle scritto, lascia il campo link vuoto.
- Se non sei sicuro della citta, lascia vuoto invece di indovinare.
- Se il testo non contiene nessuna entita catalogabile (es. un saluto, un test, una frase generica), rispondi con array vuoto [].

Rispondi SOLO con un array JSON, senza testo prima o dopo, senza markdown. Formato:
[{"name":"...","city":"...","country":"...","type":"Musica|Arte|Digital art|Misto|Tour artista","handle":"...","note":"breve descrizione"}]

Se non trovi nulla di rilevante, rispondi: []"""

PROMPT_ARTISTI = """Sei un assistente che cataloga artisti/creatori digital art, new media, generative art e audiovisivi (persone o collettivi che producono opere), E le venue d'arte (gallerie, spazi indipendenti, project space, musei) quando compaiono nello stesso contenuto.

Analizza il contenuto fornito ed estrai TUTTE le entita rilevanti.

DISTINZIONE FONDAMENTALE - ARTISTA vs VENUE:
Un artista (persona o collettivo che crea l'opera) e una venue (galleria, museo, spazio che la espone/ospita) sono DUE COSE DIVERSE. Se il contenuto menziona entrambi insieme (es. "installazione di tal artista alla galleria X"), estrai SEMPRE due righe separate, mai una sola:
- una riga per l'artista, con kind "artista"
- una riga per la venue/galleria/spazio, con kind "venue"
Se vedi solo l'artista senza nessuna venue associata, estrai solo l'artista. Se vedi solo una venue senza nessun artista associato, estrai solo la venue.

REGOLE FERREE:
- Estrai SOLO handle Instagram o link a portfolio/sito che vedi scritti esplicitamente nel testo o nell'immagine. NON inventare mai un handle basandoti sul nome.
- Se non vedi un handle o link scritto, lascia il campo link vuoto.
- Se non sei sicuro della citta o base dell'artista/venue, lascia vuoto invece di indovinare.
- Se il testo non contiene nessuna entita riconoscibile (es. un saluto, un test, una frase generica), rispondi con array vuoto [].

Rispondi SOLO con un array JSON, senza testo prima o dopo, senza markdown. Formato:
[{"name":"...","city":"...","country":"...","kind":"artista|venue","handle":"...","note":"breve descrizione"}]

Se non trovi nulla di rilevante, rispondi: []"""

PROMPTS = {
    'data.csv': PROMPT_EVENTI,
    'artisti-visual.csv': PROMPT_ARTISTI,
}


def log_debug(msg):
    os.makedirs('pending', exist_ok=True)
    with open('pending/debug.log', 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def call_gemini(parts):
    """Ritorna (testo_risposta, successo). successo=False solo per errori di rete/API,
    non per risposte valide che contengono un array vuoto."""
    body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.1}}
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
        msg = f"Errore Gemini HTTP {e.code}: {e.read().decode()[:500]}"
        print(msg)
        log_debug(msg)
        return None, False
    except Exception as e:
        msg = f"Errore Gemini: {type(e).__name__}: {e}"
        print(msg)
        log_debug(msg)
        return None, False


def parse_json_response(text):
    """Ritorna (lista_items, parsing_riuscito). parsing_riuscito=False se il JSON
    e' malformato (da NON considerare come successo, va ritentato)."""
    if not text:
        return [], False
    cleaned = re.sub(r'```(?:json)?|```', '', text).strip()
    try:
        return json.loads(cleaned), True
    except Exception as e:
        print(f"JSON non valido: {e} | testo: {cleaned[:200]}")
        return [], False


def determine_target(text):
    stripped = text.strip()
    lower = stripped.lower()
    for tag, target in SECTION_TAGS.items():
        if lower.startswith(tag):
            cleaned = stripped[len(tag):].strip()
            return target, cleaned
    return DEFAULT_TARGET, stripped


def load_existing_links(path):
    links = set()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                if r.get('link'):
                    links.add(r['link'].strip().lower())
    return links


def append_rows(path, rows):
    write_header = not os.path.exists(path)
    with open(path, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(['name', 'city', 'type', 'link', 'note', 'conf'])
        for r in rows:
            w.writerow(r)


def main():
    log_debug(f"--- run: chiave presente={bool(GEMINI_KEY)}, lunghezza={len(GEMINI_KEY)}, modello={MODEL}")
    if not GEMINI_KEY:
        print("GEMINI_API_KEY mancante, esco. Nessuna riga verra' rimossa dalla coda.")
        log_debug("CHIAVE MANCANTE")
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
    extracted_by_target = {}   # target_file -> lista di item estratti
    failed_rows = []           # righe da RIMETTERE in coda (errore temporaneo)
    succeeded_count = 0

    for row in rows:
        text = row['text']
        parts = []
        target = DEFAULT_TARGET

        m = re.match(r'\[FOTO\]\s+(\S+)\s*\|\s*didascalia:\s*(.*)', text)
        if m:
            img_path, caption = m.group(1), m.group(2)
            target, cleaned_caption = determine_target(caption)
            if not os.path.exists(img_path):
                print(f"Immagine non trovata: {img_path} -- tengo la riga in coda per sicurezza")
                failed_rows.append(row)
                continue
            with open(img_path, 'rb') as imf:
                b64 = base64.b64encode(imf.read()).decode('utf-8')
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
            parts.append({"text": PROMPTS[target] + f"\n\nDidascalia allegata: {cleaned_caption}"})
        else:
            target, cleaned_text = determine_target(text)
            parts.append({"text": PROMPTS[target] + f"\n\nContenuto da analizzare:\n{cleaned_text}"})

        result_text, api_ok = call_gemini(parts)
        log_debug(f"target={target} | api_ok={api_ok} | risposta grezza: {str(result_text)[:400]}")

        if not api_ok:
            print(f"  errore API, tengo in coda per riprovare: {text[:60]}")
            failed_rows.append(row)
            continue

        items, parse_ok = parse_json_response(result_text)
        if not parse_ok:
            print(f"  risposta non valida, tengo in coda per riprovare: {text[:60]}")
            failed_rows.append(row)
            continue

        # Successo: la chiamata ha funzionato, indipendentemente da quanti item ha trovato
        succeeded_count += 1
        print(f"  -> estratte {len(items)} entita ({target}) da: {text[:60]}")
        extracted_by_target.setdefault(target, []).extend(items)

    # Il bot Artisti restituisce sia artisti sia venue (campo "kind"): le venue vanno
    # smistate verso Eventi (tipo "Arte"), solo gli artisti restano in artisti-visual.csv
    if 'artisti-visual.csv' in extracted_by_target:
        artisti_items = extracted_by_target.pop('artisti-visual.csv')
        only_artists = []
        for item in artisti_items:
            kind = (item.get('kind') or 'artista').strip().lower()
            if kind == 'venue':
                item['type'] = 'Arte'
                note = (item.get('note') or '').strip()
                item['note'] = (note + " [venue d'arte, trovata tramite bot Artisti Visual]").strip()
                extracted_by_target.setdefault('data.csv', []).append(item)
            else:
                item['type'] = 'Digital art'
                only_artists.append(item)
        extracted_by_target['artisti-visual.csv'] = only_artists

    # Scrivo le entita' estratte nei rispettivi file
    for target, extracted in extracted_by_target.items():
        existing_links = load_existing_links(target)
        new_rows = []
        default_type = 'Digital art' if target == 'artisti-visual.csv' else 'Musica'

        for item in extracted:
            name = (item.get('name') or '').strip()
            if not name:
                continue
            handle = (item.get('handle') or '').strip().lstrip('@')
            link = f"https://www.instagram.com/{handle}" if handle else ''
            if link and link.lower() in existing_links:
                print(f"  gia presente in {target}, salto: {name}")
                continue
            if link:
                existing_links.add(link.lower())
            note = (item.get('note') or '').strip()
            note = (note + ' [auto-estratto da Gemini, da verificare]').strip()
            new_rows.append([
                name,
                (item.get('city') or '').strip(),
                (item.get('type') or default_type).strip(),
                link,
                note,
                'Guessing'
            ])

        if new_rows:
            append_rows(target, new_rows)
            print(f"Aggiunte {len(new_rows)} righe a {target}")
        else:
            print(f"Nulla di nuovo da aggiungere a {target} (chiamate riuscite ma senza entita' riconosciute)")

    # Riscrivo la coda: solo le righe fallite restano, tutte le altre (elaborate con successo) sono rimosse
    with open(PENDING, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'text'])
        for r in failed_rows:
            w.writerow([r.get('timestamp', ''), r.get('text', '')])

    print(f"Elaborazione completata: {succeeded_count} righe elaborate con successo, {len(failed_rows)} rimesse in coda per un nuovo tentativo.")


if __name__ == '__main__':
    main()
