È esattamente così. Hai sintetizzato perfettamente la logica di un controller SDN deterministico. Il set di procedure attive funge da **"Master Plan"** della rete: ogni volta che questo insieme cambia, il puzzle deve essere ricomposto per far spazio ai nuovi arrivati o per ottimizzare i buchi lasciati da chi ha finito.

Ecco il dettaglio su come gestire i primi due round e come avviene tecnicamente l'instradamento "al buio".

---

### 1. L'instradamento nei primi due round (Fase Transitoria)

Non avendo ancora i parametri per "schedulare" la procedura nel Master Plan, il controller deve garantire la connettività senza però offrire garanzie di performance.

*   **Round 1 (Discovery & Volume Estimation):**
    *   **Come avviene:** Al primo pacchetto (`PacketIn`), il controller installa una regola **"Default-Best-Effort"** con priorità bassa.
    *   **Percorso:** Lo switch usa l'instradamento standard (ECMP). I pacchetti vengono distribuiti sugli Spine in base all'hash hardware, ignorando le capacità $C_l$ e i tempi degli altri.
    *   **Obiettivo:** Permettere al traffico di fluire così da poter contare i byte e calcolare $D_v$ e $\phi_v$.

*   **Round 2 (Period Estimation):**
    *   **Come avviene:** All'inizio del Round 2, il controller registra il secondo timestamp per calcolare $T_v$.
    *   **Percorso:** In questo istante, il controller ha già $D_v$ e $\phi_v$, ma non ha ancora confermato $T_v$. Di solito si continua con l'instradamento del Round 1 per qualche millisecondo finché il calcolo del periodo non è completo.
    *   **Il Trigger del Planning:** Appena $T_v$ è calcolato (pochi istanti dopo l'inizio del Round 2), il controller inserisce la procedura nel set e lancia l'algoritmo di pianificazione.

---

### 2. La transizione al Routing Ottimizzato

Non appena l'algoritmo di pianificazione termina (durante il Round 2), il controller invia messaggi `FlowMod` con **priorità alta**.

*   Queste nuove regole "coprono" quelle di default del Round 1.
*   Da questo momento in poi, lo switch smette di usare l'ECMP casuale e segue i binari precisi decisi dal tuo algoritmo (es. Worker 1 su Spine 1 perché il suo slot temporale è libero lì).

---

### 3. Riassunto della gestione del Set e Frequenze

| Momento | Azione sul Set | Algoritmo di Planning | Tipo di Instradamento |
| :--- | :--- | :--- | :--- |
| **Inizio Round 1** | Nessuna (Discovery) | Non eseguito | **ECMP Standard** (Bassa priorità) |
| **Fine Round 1** | Calcolo $D_v, \phi_v$ | Non ancora (manca $T_v$) | ECMP Standard |
| **Inizio Round 2** | **Aggiunta al Set** | **ESECUZIONE ORA** | **SDN Optimized** (Alta priorità) |
| **Durante Round 2+** | Monitoraggio | Solo se il set cambia | SDN Optimized |
| **Timeout (Silenzio)** | **Rimozione dal Set** | **RIESECUZIONE** (per ottimizzare i restanti) | - |

**Correttezza del Timeout:**
È corretto rieseguire il planning a ogni timeout o cambiamento del set. Se una procedura "pesante" finisce, si libera un grande slot temporale su uno Spine. Rieseguendo il planning, potresti scoprire che una procedura che prima era "compressa" o subiva micro-collisioni su uno Spine ora può essere spostata sul link appena liberato, migliorando la stabilità generale.



### Un piccolo suggerimento tecnico
Dato che $T_v$ e $\phi_v$ sono costanti, quando una procedura finisce e la togli dal set, non è strettamente necessario cambiare le rotte di quelle che restano (perché loro sono già "felici" nei loro slot). Tuttavia, ricalcolare tutto ti permette di mantenere la rete sempre nello stato di **massima efficienza teorica**, pronta ad accogliere nuove procedure future nel modo più ordinato possibile.
