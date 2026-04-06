# Report di Sviluppo: Ricostruzione della Pipeline CSI passo per passo (Task 1 - 7)

Inizialmente ho esaminato i progetti RuView ed ESPectre, ma entrambi presentavano interfacce utente già molto complete, integrate e architetture complesse. Questo rendeva difficile isolare e comprendere in modo pulito il vero cuore dell'elaborazione del segnale CSI. Per questo motivo, ho deciso di "mettere da parte" le loro UI e ricostruire l'intero meccanismo dalle basi, verificando matematicamente ogni passaggio per averne la padronanza assoluta.

Ecco il percorso strutturato nei 7 task implementati nella cartella `demo/`:

## Task 1: Le Fondamenta - Parsing dei dati grezzi (`csi_parser.py`)
Il primissimo passo è stato capire cosa trasmettesse realmente l'hardware ESP32. Ho scritto un parser per decodificare il protocollo binario UDP grezzo (formato ADR-018). Questo convertiva i byte incomprensibili per estrarre gli header (MAC, RSSI, sequenza) e soprattutto per calcolare le ampiezze matematiche `√(I² + Q²)` a partire dalle singole subcarrier. *Perché per primo?* Perché senza la garanzia di estrarre i dati fisicamente corretti, ogni calcolo successivo sarebbe stato viziato.

## Task 2: Validazione del Flusso di Rete (`csi_probe.py`)
Ottenuti i dati leggibili, dovevo capire se la rete Wi-Fi li stesse inoltrando correttamente in tempo reale. Ho creato un probe diagnostico. *Perché?* Se il router droppa il 30% dei pacchetti (UDP non ha garanzie di consegna) o se i frame arrivano fuori sequenza, qualsiasi algoritmo di rilevamento produrrebbe "buchi" e falsi positivi. Il probe mi ha permesso di monitorare la stabilità e la latenza della rete prima di scriverci sopra logica complessa.

## Task 3: Visualizzazione ed Esplorazione Visiva (`csi_capture.py`)
Il passo successivo era "vedere" l'invisibile. Ho creato un piccolo script offline per catturare blocchi di 30 secondi di segnale passivo e graficarli con Python (Matplotlib). *Perché l'ho fatto qui?* L'unico modo per intuire se usare la varianza spaziale globale o la differenza temporale tra i pacchetti era camminare nella stanza mentre il PC registrava e analizzare successivamente in modo visivo l'impatto dei perturbamenti sulle onde radio.

## Task 4: Base Empirica - Raccolta di "Ground Truth" (`csi_experiment.py`)
Per automatizzare una decisione (rilevare se qualcuno è in stanza), non ci si può affidare a numeri "inventati". Ho costruito un tool per orchestrare un vero e proprio esperimento metodico: uno script che guida l'utente a restare fermo per "N" secondi (etichettando i dati estratti col parser come "idle") e poi a muoversi per altri "N" secondi (etichettando tutto come "movement"), buttando i risultati in un log CSV pulito.

## Task 5: Calcolo Matematico delle Soglie (`csi_analyze.py`)
Con il CSV dell'esperimento pronto, avevo bisogno della matematica per separare gli stati di quiete da quelli di moto. Ho sviluppato un analizzatore statistico che digerisce il CSV, applica 4 diverse metriche sperimentali, misura i cluster di densità dei dati idle vs movement e determina in automatico quale formula offra la separazione matematica migliore (es. Coefficiente di Variazione e Moving Variance). In uscita restituisce la "Soglia Magica" ideale da applicare come barriera di classificazione.

## Task 6: Streaming Live su Dashboard Semplice (`csi_bridge.py` & `csi_dashboard.html`)
Prima di chiudere il cerchio, volevo assicurarmi che il delay tra l'elaborazione dei pacchetti e un'eventuale interfaccia utente fosse sotto controllo. Ho creato un "bridge" che snellisce e aggrega le stringhe Python tramite WebSocket e spinge i dati a una pagina web vanilla/minimale dove la linea del segnale fluttua dal vivo in base a chi passa davanti ai dispositivi, verificandone finalmente l'esperienza fluida svincolata dalle logiche complesse del framework originale di RuView.

## Task 7: Il Motore Autonomo Definitivo (`csi_detector.py`)
Come fusione finale delle lezioni apprese in questi 6 step, ho forgiato il motore centrale. Vi ho riversato l'uso del parser (Task 1), la finestra mobile di varianza misurata (Task 5) e lo stream WebSocket in uscita (Task 6). *L'innovazione chiave?* Piuttosto che cablare nel codice i numeri magici calcolati alla riga precedente, ho fornito al motore 10 secondi di auto-calibrazione non appena viene sollevato, affinché scatti la fotografia dell'ambiente "tranquillo" e imposti le sue personali soglie in modo intelligente basandosi sullo scostamento standard. È stata inoltre introdotta un'isteresi di sicurezza (il target di stato non cambia finché decine di pacchetti confermano il disturbo logico nel locale) al fine di filtrare i micro-rumori spuri ambientali.

---
Questo percorso ha de-costruito e reso chiari in step autonomi i concetti che erano invece fortemente abbinati e quasi illeggibili nel sorgente completo base, mettendoci in rampa di lancio protetta per le imminenti migliorie architetturali avanzate come la Selezione di Frequenza o l'uso di filtri temporali di Hampel e Butterworth.
---


## SETUP
Un ESP32 flashato con firmware di RuView (approfondire)
Un router AP per wifi casalingo
Un computer che pinga costantemente l'ESP32 per generare un flusso di dati continuo e costante fra router e ESP32
Computer e ESP32 sono disposti il più lontano possibile per evitare che l'ESP32 ascolti anche i pacchetti inviati dal computer al router.
Idealmente triangolazione ai lati della stanza per coprire tutta la stanza
