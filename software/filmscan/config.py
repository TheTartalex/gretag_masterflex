# -*- coding: utf-8 -*-
"""
Configurazione centrale: geometria dei formati pellicola, preset di scansione,
parametri di calibrazione.

Tutte le misure geometriche sono in MILLIMETRI. La conversione in pixel avviene
in un solo punto (geometry.mm_to_px) in modo che cambiare la risoluzione ottica
non richieda di toccare nient'altro.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
import json
import os


# --------------------------------------------------------------------------
# Geometria dei formati
# --------------------------------------------------------------------------

@dataclass
class FilmFormat:
    """Descrive la geometria di un formato pellicola.

    frame_w / frame_h : dimensione dell'area immagine
    pitch             : passo tra l'inizio di un fotogramma e il successivo
                        (frame_w + interframe gap) lungo l'asse di trascinamento
    strip_w           : larghezza totale della striscia, bordi compresi
    has_sprockets     : se True l'individuazione del frame puo' usare le perforazioni
    """
    name: str
    frame_w: float
    frame_h: float
    pitch: float
    strip_w: float
    has_sprockets: bool = True
    notes: str = ""

    # Tetto di sicurezza, non un obiettivo. Il numero di fotogrammi non si
    # chiede all'operatore: si conta scansionando, e ci si ferma quando la
    # pellicola finisce. Questo valore serve solo a non tirare all'infinito se
    # il rilevamento della coda dovesse sbagliare.
    max_fotogrammi: int = 40

    @property
    def aspect(self) -> float:
        return self.frame_w / self.frame_h


# Valori nominali. Vanno verificati sul carrello reale della macchina:
# il pitch soprattutto varia con la marca di pellicola e con l'usura del trascinamento.
FORMATS: Dict[str, FilmFormat] = {
    "135-full": FilmFormat("135 (24x36)", 36.0, 24.0, 38.0, 35.0, True,
                           "Standard 35mm. Pitch nominale 38mm (8 perforazioni).",
                           max_fotogrammi=40),
    # Il mezzo formato sdoppia ogni fotogramma: un rullino da 36 ne da' 72.
    "135-half": FilmFormat("135 mezzo formato (18x24)", 18.0, 24.0, 19.0, 35.0, True,
                           "Orientamento verticale rispetto alla striscia.",
                           max_fotogrammi=72),
    "135-pano": FilmFormat("135 panoramico (24x65)", 65.0, 24.0, 67.0, 35.0, True,
                           max_fotogrammi=40),
    "120-645": FilmFormat("120 / 6x4.5", 56.0, 41.5, 43.5, 61.0, False,
                          max_fotogrammi=16),
    "120-66": FilmFormat("120 / 6x6", 56.0, 56.0, 58.0, 61.0, False,
                         max_fotogrammi=12),
    "120-67": FilmFormat("120 / 6x7", 69.0, 56.0, 71.0, 61.0, False,
                         max_fotogrammi=10),
    # 40 e' anche il tetto dichiarato dall'SDK originale (MAX_APS_FRAME_NUMBER).
    "aps-h": FilmFormat("APS-H", 30.2, 16.7, 32.0, 24.0, True, max_fotogrammi=40),
    "aps-c": FilmFormat("APS-C", 25.1, 16.7, 32.0, 24.0, True, max_fotogrammi=40),
    "aps-p": FilmFormat("APS-P", 30.2, 9.5, 32.0, 24.0, True, max_fotogrammi=40),
}


# --------------------------------------------------------------------------
# Preset di scansione
# --------------------------------------------------------------------------

@dataclass
class ScanPreset:
    """Un preset di acquisizione.

    passes_per_channel : quante letture per canale (>1 = media, riduce il rumore)
    use_ir             : acquisisce anche il canale infrarosso
    bit_depth          : profondita' dell'ADC richiesta alla scheda
    dpi                : risoluzione ottica richiesta
    multi_exposure     : lista di moltiplicatori di esposizione; con piu' di un
                         valore si costruisce un HDR per estendere la gamma nelle ombre
                         dense (le alte densita' del negativo)
    """
    name: str
    dpi: int = 4000
    bit_depth: int = 16
    passes_per_channel: int = 1
    use_ir: bool = True
    multi_exposure: List[float] = field(default_factory=lambda: [1.0])
    autofocus: bool = True
    description: str = ""

    # Canali visibili da acquisire. Per il bianco e nero a base argento non
    # esiste informazione di colore da leggere: un solo passaggio basta, e
    # tenerne tre significa scansionare tre volte piu' lentamente per niente.
    color_channels: List[str] = field(default_factory=lambda: ["R", "G", "B"])

    # Esposizione relativa per canale. La maschera arancione del negativo
    # assorbe molto il blu, quindi a parita' di tempo il canale B arriva al
    # sensore piu' debole e viene su rumoroso. Con None l'esposizione viene
    # misurata da una pre-scansione (vedi acquire.measure_channel_exposure).
    channel_exposure: Optional[Dict[str, float]] = None
    auto_exposure: bool = True

    @property
    def channels(self) -> List[str]:
        ch = list(self.color_channels)
        if self.use_ir:
            ch.append("IR")
        return ch

    @property
    def monochrome(self) -> bool:
        return len(self.color_channels) == 1

    @property
    def total_passes(self) -> int:
        return (len(self.channels) * self.passes_per_channel
                * len(self.multi_exposure))


PRESETS: Dict[str, ScanPreset] = {
    "provino": ScanPreset(
        "Provino rapido", dpi=1000, bit_depth=8, passes_per_channel=1,
        use_ir=False, autofocus=False,
        description="Anteprima veloce dell'intero rullo per scegliere cosa scansionare."),
    "standard": ScanPreset(
        "Standard", dpi=2000, bit_depth=16, passes_per_channel=1, use_ir=True,
        description="Uso quotidiano. Un passaggio per canale piu' infrarosso."),
    "archivio": ScanPreset(
        "Archivio", dpi=4000, bit_depth=16, passes_per_channel=2, use_ir=True,
        multi_exposure=[1.0, 4.0],
        description="Massima qualita'. Media di due letture per canale e doppia "
                    "esposizione per recuperare le ombre dense. Lento."),
    "bn": ScanPreset(
        "Bianco e nero", dpi=4000, bit_depth=16, passes_per_channel=3, use_ir=False,
        color_channels=["G"],
        description="Un solo canale, mediato su tre letture. Le B/N a base argento "
                    "non portano informazione di colore, quindi tre passaggi "
                    "colore sarebbero lavoro triplicato per lo stesso risultato. "
                    "IR disattivato: l'emulsione all'argento e' opaca "
                    "all'infrarosso e la maschera coprirebbe tutta l'immagine. "
                    "Per le B/N cromogeniche (sviluppo C-41) usare 'standard'."),
    "diapositiva": ScanPreset(
        "Diapositiva", dpi=4000, bit_depth=16, passes_per_channel=1, use_ir=True,
        multi_exposure=[1.0, 6.0],
        description="Le diapositive raggiungono densita' molto piu' alte dei "
                    "negativi: nelle ombre profonde passa pochissima luce. La "
                    "doppia esposizione qui non e' un lusso, serve davvero per "
                    "non ritrovarsi le ombre annegate nel rumore."),
}


# --------------------------------------------------------------------------
# Configurazione applicativa
# --------------------------------------------------------------------------

@dataclass
class AppConfig:
    # --- hardware ---
    driver: str = "mock"                 # "mock" | "sdk"
    bridge_path: str = ""                # cartella di scambio con l'agente (driver "sdk")

    # --- macchina vera (driver "sdk") ---------------------------------------
    # Quale ingresso digitale (diag_input) e' quale sensore. Vuoto finche' non
    # si scopre sulla macchina: allora i sensori risultano non disponibili.
    sdk_ingressi: Dict[str, int] = field(default_factory=dict)
    # True solo dopo aver visto che ColorChannel 3 restituisce davvero l'IR.
    sdk_ir_verificato: bool = False
    # Regolazione fine massima per comando, in mm. Oltre si usa "avanza".
    max_regola_mm: float = 3.0

    # --- geometria ottica reale ---------------------------------------------
    # RETTIFICATA il 3/9/2026 leggendo le costanti dentro scn_dll.dll, l'SDK
    # originale. I valori precedenti (4000 dpi, 5600 pixel) erano una stima
    # ottimistica: la macchina vera e' piu' modesta di cosi', e vale la pena
    # saperlo adesso invece di scoprirlo al primo rullo.
    #
    #   PIXEL_SIZE = 6,8 um   (dimensione del pixel sul CCD)
    #   MAG_35     = 0,420026 (ingrandimento per il 135)
    #   MAG_APS    = 0,523181
    #
    # 6,8 um / 0,420026 = 16,19 um sul film -> circa 1570 dpi sul 135.
    # La tabella IMAGE_RESOLUTION dell'SDK, per il nostro tipo di scanner (3),
    # da' cinque risoluzioni: 2100x1400, 1052x700, 524x350, 264x175, 132x88.
    # Il fotogramma pieno e' quindi 2100x1400, non 5669x3779.
    #
    # Resta comunque un salto enorme rispetto ai 600x400 del software
    # originale: dodici volte i pixel. Ed e' un numero onesto.
    ccd_pixel_um: float = 6.8            # dimensione del pixel sul CCD
    magnification: Dict[str, float] = field(default_factory=lambda: {
        "135": 0.420026, "aps": 0.523181, "110": 0.523181, "120": 0.523181})
    optical_dpi: int = 1569              # risoluzione reale sul 135
    sensor_pixels: int = 2100            # larghezza del fotogramma a piena risoluzione
    sensor_lines: int = 1400             # altezza del fotogramma a piena risoluzione
    # Le cinque risoluzioni dell'SDK, indice 0 = piena. L'indice va passato
    # tale e quale all'SDK originale come ImageResolution.
    sdk_resolutions: List[tuple] = field(default_factory=lambda: [
        (2100, 1400), (1052, 700), (524, 350), (264, 175), (132, 88)])
    sdk_resolution: int = 0              # quale usare per le acquisizioni vere

    transport_step_mm: float = 0.01      # passo minimo del motore di trascinamento
    focus_step_mm: Optional[float] = None  # DA COMPILARE: passo dell'attuatore di fuoco

    # --- geometria del percorso pellicola ---
    # DA COMPILARE dal manuale. Distanza fra il sensore di presenza in ingresso
    # e la finestra di scansione. E' il dato che dice quanti fotogrammi restano
    # da leggere DOPO che il sensore d'ingresso si e' liberato: in quel momento
    # la coda della striscia e' al sensore, ma davanti alla finestra c'e'
    # ancora pellicola. Senza questo valore si perdono gli ultimi fotogrammi
    # di ogni striscia.
    sensore_ingresso_finestra_mm: Optional[float] = None
    # Distanza finestra -> sensore di uscita, per sapere quando l'espulsione
    # e' davvero completa.
    finestra_sensore_uscita_mm: Optional[float] = None

    # --- ciclo automatico ---
    auto_start: bool = True     # parte da solo quando il sensore d'ingresso si copre
    auto_eject: bool = True     # espelle a striscia completata
    # a striscia finita la pellicola e' gia' avanti: "retro" la fa uscire nel
    # verso in cui stava andando, "davanti" la riporta indietro tutta
    verso_espulsione_fine_rullo: str = "retro"
    # rullo tagliato in strisce: dopo ogni striscia il carrello si riarma e
    # aspetta la successiva per questo tempo (o fino a "Fine rullo")
    attesa_striscia_s: float = 600.0
    # Memoria per i fotogrammi letti che aspettano l'elaborazione (MB). Il
    # processo a 32 bit ne ha ~1800 in tutto; un grezzo RGB a piena
    # risoluzione ne occupa ~35: 700 MB sono una ventina di fotogrammi di
    # vantaggio della lettura sull'elaborazione.
    memoria_elaborazione_mb: int = 700
    # Il negativo lineare per altri programmi, accanto a ogni fotogramma:
    # "" = no, "tiff" = TIFF RGB 16 bit, "dng" = DNG lineare (Lightroom e
    # Negative Lab Pro lo trattano da RAW)
    negativo_lineare: str = ""
    eject_max_mm: float = 400.0  # corsa massima in espulsione, per non tirare a vuoto

    # --- archivio degli ordini gia' fatti ---
    # La cartella dove tenere il registro e le consegne. Vuota = accanto alle
    # scansioni. Su Windows conviene una cartella vera e propria, magari su un
    # disco diverso da quello di sistema.
    archivio_dir: str = ""
    registro_nome: str = "registro-ordini.xlsx"

    # Per quanti giorni tenere i file di un ordine. I giorni si contano
    # dall'ULTIMA volta che si e' lavorato a quell'ordine, non da quando e'
    # stato aperto: un ordine aperto a gennaio e scansionato ieri ha un giorno.
    conservazione_giorni: int = 30

    # Cosa fare degli ordini scaduti:
    #   "segnala" - non tocca niente, li elenca soltanto (predefinito)
    #   "grezzi"  - cancella i grezzi, lascia le foto esportate
    #   "tutto"   - cancella grezzi, foto e maschere
    # Resta su "segnala" finche' qualcuno non decide altro, e in nessun caso
    # sparisce la riga dell'ordine. I grezzi sono irripetibili: per rifarli
    # bisogna rimettere la pellicola nella macchina, ammesso che il cliente ce
    # l'abbia ancora, e nessun conto alla rovescia vale quanto le fotografie
    # di qualcuno.
    pulizia: str = "segnala"

    # Numero di telefono predefinito per gli avvisi (prefisso internazionale,
    # senza + e senza spazi: 39335...). Vuoto = si prende dalla scheda cliente.
    whatsapp_prefisso: str = "39"

    # --- percorsi ---
    raw_dir: str = "./scansioni/raw"
    out_dir: str = "./scansioni/export"
    calib_dir: str = "./calibrazione"

    # --- default ---
    default_format: str = "135-full"
    default_preset: str = "standard"

    # --- export ---
    export_format: str = "tiff16"        # tiff16 | tiff8 | jpeg | png
    jpeg_quality: int = 95
    keep_raw: bool = True                # conservare i grezzi per rielaborazioni future
    # --- impostazioni della macchina (scheda Macchina) ---
    # esposizione automatica: tempi calibrati dell'SDK (calibrate_all nella
    # calibrazione) piu' la misura per canale a inizio rullo; spenta: tempi
    # di fabbrica, x1 su tutti i canali
    esposizione_auto: bool = True
    # pressore: giu' prima di ogni lettura, su prima di muovere la pellicola
    pressore_auto: bool = True
    # avanzamento automatico: il carrello cerca il fotogramma successivo;
    # spento, ogni fotogramma si mette in finestra a mano col video
    avanzamento_automatico: bool = True
    # autofocus a inizio striscia e sul tasto Fuoco: "gi" (gi_focus, ricerca
    # completa), "rapido" (gi_focus modo 0), "nominale" (posizione
    # nominale della lente), "spento"
    autofocus: str = "gi"
    # --- LUT applicata in post-elaborazione a tutti gli ordini ---
    lut: str = ""                       # nome del .cube nella cartella lut, "" = nessuna
    lut_intensita: float = 1.0
    lut_dir: str = ""                   # vuoto = software/lut, accanto al programma
    # sotto questa escursione di densita' (D) un fotogramma e' vuoto: non
    # esposto o tutto opaco. Si salta, si annota e si conserva il grezzo.
    soglia_fotogramma_vuoto_d: float = 0.10
    # La misura dell'esposizione non vale se anche il canale piu' chiaro
    # passa meno di questa frazione della luce: la finestra e' sulla coda
    # annerita. Sulla riserva (27/10) la coda dava il rosso sotto il 7% e la
    # misura chiedeva x12; su un negativo vero la base arancione passa oltre
    # il 30% del rosso.
    soglia_misura_esposizione: float = 0.15

    # --- elaborazione ---
    auto_crop: bool = True
    auto_straighten: bool = True
    crop_margin_mm: float = 0.3          # quanto rosicchiare dentro il bordo trovato
    ir_clean_strength: float = 1.0       # 0 = disattivo, 1 = pieno
    export_ir_mask: bool = False         # esporta la maschera come file separato

    def path(self, which: str) -> str:
        p = {"raw": self.raw_dir, "out": self.out_dir, "calib": self.calib_dir,
             "archivio": self.archivio_dir or self.out_dir,
             "lut": self.lut_dir or os.path.join(
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "lut")}[which]
        os.makedirs(p, exist_ok=True)
        return p

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "AppConfig":
        if not os.path.exists(path):
            return cls()
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# --------------------------------------------------------------------------
# Conversioni
# --------------------------------------------------------------------------

def mm_to_px(mm: float, dpi: int) -> int:
    return int(round(mm / 25.4 * dpi))


def px_to_mm(px: float, dpi: int) -> float:
    return px * 25.4 / dpi


def steps_for_mm(mm: float, step_mm: float) -> int:
    """Numero di passi motore per coprire una distanza. Arrotonda per difetto:
    meglio fermarsi corti e correggere che sorpassare, dato il backlash."""
    return int(mm / step_mm)
