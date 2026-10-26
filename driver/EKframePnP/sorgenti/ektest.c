/*
    ektest.exe - collaudo del driver EKframePnP sulla macchina vera.

    ektest            informazioni: risorse, PCI, registri, contatori
    ektest arcnet     apre la scheda come DMLCmdTransfer (nodo 0x55 -> 0xAA)
                      e aspetta che la rete ARCNET verso lo scanner sia pronta
    ektest dma [MB]   prova aggancio/avvio/arresto/rilascio di un buffer DMA
                      (senza scansione in corso il trasferimento non si
                      completa: si verifica che il driver regga il ciclo)
    ektest regs       stampa tutti i registri del PLX 9080

    Codice d'uscita 0 = tutto bene.
*/
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

static FILE *logf = NULL;

static void out(const char *fmt, ...)
{
    va_list a;
    va_start(a, fmt); vprintf(fmt, a); va_end(a);
    if (logf) { va_start(a, fmt); vfprintf(logf, fmt, a); va_end(a); fflush(logf); }
}

#define FDEV 0x9c40U
#define CTL(f,m,a) ((FDEV<<16)|((a)<<14)|((f)<<2)|(m))
#define IOCTL_GET_REGISTER   CTL(0x900,0,1)
#define IOCTL_GET_PCI_INFO   CTL(0x902,0,1)
#define IOCTL_OPEN           CTL(0x903,0,3)
#define IOCTL_REGISTER_EVENT CTL(0x904,0,2)
#define IOCTL_RELEASE_EVENT  CTL(0x905,0,2)
#define IOCTL_DMA_ARM_0      CTL(0x908,1,2)
#define IOCTL_DMA_RELEASE_0  CTL(0x90C,0,2)
#define IOCTL_DMA_START      CTL(0x910,0,2)
#define IOCTL_DMA_STOP       CTL(0x911,0,2)
#define IOCTL_DIAG           CTL(0xA00,0,1)

typedef struct { ULONG Space, Offset, Value; } REGACC;
typedef struct { USHORT DeviceId, VendorId; UCHAR Rev, ProgIf, Base, Sub, IntLine, IntPin, MinGnt, MaxLat,
                 Cache, Lat, R[2]; ULONG Rom; ULONG Bar[6]; } PCIINFO;
typedef struct { UCHAR Reinit, R[3]; ULONG NodeId, DestId, Status; } OPENP;
typedef struct { ULONG Index; HANDLE Event; } EVP;
typedef struct { ULONG Channel, Value; } DMAP;
typedef struct {
    ULONG Version, PlxPhysical, PlxLength, LocalPhysical, LocalLength, Vector, Irql, InterruptConnected;
    ULONG MapRegisters[2], IntMask; LONG ActiveChannel; ULONG NetReady, NodeId, DestId, LastArcStatus,
    ArcInitResult, RxHead, RxTail, OpenCount, IsrCount, IsrClaimed, DmaDoneCount, RxCount, RxDropped,
    TxCount, TxFailed, ChannelArmed[2], ChannelBytes[2], ChannelDescriptors[2], PlxIntcsr, PlxDmacsr,
    LocalIntStatus, LocalIntEnable, LocalMode;
} DIAG;

static HANDLE dev;
static int failures = 0;

static void ok(const char *what)   { out("  [ OK ] %s\n", what); }
static void ko(const char *what, DWORD e) { out("  [ERR ] %s  (errore Windows %lu)\n", what, e); failures++; }

static BOOL ioctl(DWORD code, void *in, DWORD inLen, void *out, DWORD outLen, DWORD *ret)
{
    OVERLAPPED ov; DWORD r = 0; BOOL b;
    memset(&ov, 0, sizeof ov);
    ov.hEvent = CreateEvent(NULL, TRUE, FALSE, NULL);
    b = DeviceIoControl(dev, code, in, inLen, out, outLen, &r, &ov);
    if (!b && GetLastError() == ERROR_IO_PENDING) b = GetOverlappedResult(dev, &ov, &r, TRUE);
    CloseHandle(ov.hEvent);
    if (ret) *ret = r;
    return b;
}

static int open_device(void)
{
    dev = CreateFileA("\\\\.\\EKFrameDev", GENERIC_READ | GENERIC_WRITE, 0, NULL,
                      OPEN_EXISTING, FILE_FLAG_OVERLAPPED, NULL);
    if (dev == INVALID_HANDLE_VALUE) {
        DWORD e = GetLastError();
        out("Impossibile aprire \\\\.\\EKFrameDev (errore %lu).\n", e);
        if (e == 2) out("Il driver non risponde: o non e' installato (lancia INSTALLA.bat come amministratore)\noppure la scheda non e' partita (Gestione dispositivi: cerca \"IAQ Adapter\", guarda il codice di errore).\n");
        if (e == 1) out("Il nome EKFrameDev esiste ma appartiene a un dispositivo senza gestori: e' il segno\n"
                        "del vecchio ekframe.sys caricato all'avvio e fallito su Windows 7.\n"
                        "Lancia rimuovi-driver-vecchio.bat come amministratore e RIAVVIA.\n");
        if (e == 5) out("Accesso negato: esegui come amministratore.\n");
        return 0;
    }
    return 1;
}

static int diag(DIAG *g)
{
    DWORD r;
    memset(g, 0, sizeof *g);
    return ioctl(IOCTL_DIAG, NULL, 0, g, sizeof *g, &r) && r == sizeof *g;
}

static ULONG reg(ULONG space, ULONG off)
{
    REGACC a = { space, off, 0 }; DWORD r;
    if (!ioctl(IOCTL_GET_REGISTER, &a, sizeof a, &a, sizeof a, &r)) return 0xFFFFFFFF;
    return a.Value;
}

static void print_diag(const DIAG *g)
{
    out("  Registri PLX     : %08lX  (%lu byte)\n", g->PlxPhysical, g->PlxLength);
    out("  Finestra locale  : %08lX  (%lu KB)\n", g->LocalPhysical, g->LocalLength / 1024);
    out("  Interrupt        : vettore %lu, livello %lu, %s\n", g->Vector, g->Irql,
           g->InterruptConnected ? "collegato" : "NON collegato");
    out("  Map register DMA : %lu / %lu  (servono %lu per una scansione 2100x1400x16bit)\n",
           g->MapRegisters[0], g->MapRegisters[1], (2100UL * 1400 * 2 + 4095) / 4096 + 1);
    out("  Maschera FPGA    : %lX   stato %08lX   modo %08lX\n", g->IntMask, g->LocalIntStatus, g->LocalMode);
    out("  PLX INTCSR       : %08lX   DMACSR %08lX\n", g->PlxIntcsr, g->PlxDmacsr);
    out("  ARCNET           : init %s, rete %s, nodo %02lX -> %02lX, stato %04lX\n",
           g->ArcInitResult == 1 ? "riuscita" : g->ArcInitResult == 2 ? "FALLITA" : "non fatta",
           g->NetReady ? "PRONTA" : "non pronta", g->NodeId, g->DestId, g->LastArcStatus);
    out("  Contatori        : interrupt %lu (nostri %lu), DMA %lu, rx %lu (scartati %lu), tx %lu (falliti %lu)\n",
           g->IsrCount, g->IsrClaimed, g->DmaDoneCount, g->RxCount, g->RxDropped, g->TxCount, g->TxFailed);
}

static int cmd_info(void)
{
    DIAG g; PCIINFO p; DWORD r; int i;

    out("\n== Driver ==\n");
    if (!diag(&g)) { ko("risposta diagnostica (driver diverso da EKframePnP 2.x?)", GetLastError()); return 1; }
    if (g.Version != 0x00020001) out("  versione diagnostica %08lX inattesa\n", g.Version);
    ok("EKframePnP risponde");
    print_diag(&g);

    out("\n== Verifiche ==\n");
    if (g.PlxLength == 0x100) ok("BAR registri PLX da 256 byte"); else ko("BAR registri PLX: dimensione inattesa", 0);
    if (g.LocalLength >= 0x180000) ok("finestra locale abbastanza grande per i descrittori DMA");
    else ko("finestra locale troppo piccola", 0);
    if (g.InterruptConnected) ok("interrupt collegato"); else ko("interrupt non collegato", 0);
    if (g.MapRegisters[0] >= 1437) ok("map register sufficienti per una scansione intera");
    else ko("map register insufficienti: la scansione a piena risoluzione verra' rifiutata", 0);

    memset(&p, 0, sizeof p);
    if (ioctl(IOCTL_GET_PCI_INFO, NULL, 0, &p, sizeof p, &r) && r == sizeof p) {
        out("  PCI: vendor %04X device %04X rev %02X, classe %02X/%02X, IRQ %u pin %u\n",
               p.VendorId, p.DeviceId, p.Rev, p.Base, p.Sub, p.IntLine, p.IntPin);
        for (i = 0; i < 6; i++) if (p.Bar[i]) out("       BAR%d = %08lX\n", i, p.Bar[i]);
        if (p.VendorId == 0x11B2 && p.DeviceId == 0x0008) ok("identita' PCI 11B2:0008 (formato identico all'originale)");
        else ko("identita' PCI inattesa", 0);
    } else ko("GET_PCI_INFO", GetLastError());

    out("\n== Lettura registri (IOCTL dell'originale) ==\n");
    out("  PLX LBRD0 %08lX  DMAMODE0 %08lX  INTCSR %08lX\n", reg(0, 0x18), reg(0, 0x80), reg(0, 0x68));
    out("  FPGA 0x4008 %08lX  0x400C %08lX  0x4014 %08lX\n", reg(1, 0x4008), reg(1, 0x400C), reg(1, 0x4014));
    if (reg(0, 0x68) == 0xFFFFFFFF) ko("i registri leggono FFFFFFFF: la scheda non risponde sul bus", 0);
    else ok("registri leggibili");
    return failures;
}

static HANDLE ev[5];

static void register_events(void)
{
    int i; DWORD r;
    for (i = 0; i < 5; i++) {
        EVP e; e.Index = i; ev[i] = CreateEvent(NULL, FALSE, FALSE, NULL); e.Event = ev[i];
        if (!ioctl(IOCTL_REGISTER_EVENT, &e, sizeof e, &e, sizeof e, &r)) ko("registrazione evento", GetLastError());
    }
}

static int do_open(void)
{
    OPENP o; DWORD r;
    memset(&o, 0, sizeof o); o.NodeId = 0x55; o.DestId = 0xAA;
    if (!ioctl(IOCTL_OPEN, &o, sizeof o, &o, sizeof o, &r) || r != sizeof o) { ko("apertura scheda (IOCTL 0x9C40E40C)", GetLastError()); return 0; }
    if (o.Status & 1) { ko("inizializzazione ARCNET (COM20020) fallita: chip non risponde o RAM guasta", 0); return 0; }
    ok("scheda aperta, controller ARCNET inizializzato (firma D1h e test RAM superati)");
    return 1;
}

static int cmd_arcnet(void)
{
    DIAG g; DWORD w;
    out("\n== ARCNET verso lo scanner ==\n");
    register_events();
    if (!do_open()) return 1;
    out("  attendo la rete ARCNET (evento 0) fino a 15 s: lo scanner deve essere acceso...\n");
    w = WaitForSingleObject(ev[0], 15000);
    diag(&g);
    if (w == WAIT_OBJECT_0 || g.NetReady) ok("rete ARCNET pronta: lo scanner e' raggiungibile");
    else ko("rete ARCNET non pronta: scanner spento, cavo, o nodo diverso", 0);
    print_diag(&g);
    out("\n  Prova successiva consigliata: DMLCmdTransfer 1 (usa ReadFile/WriteFile su questo driver).\n");
    return failures;
}

static int cmd_dma(int mb)
{
    DWORD size = (DWORD)mb * 1024 * 1024, r, w;
    BYTE *buf;
    OVERLAPPED ov; DMAP p; DIAG g;

    out("\n== Ciclo DMA, canale 0, %d MB ==\n", mb);
    register_events();
    if (!do_open()) return 1;

    buf = (BYTE *)VirtualAlloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!buf) { ko("VirtualAlloc", GetLastError()); return 1; }

    memset(&ov, 0, sizeof ov); ov.hEvent = CreateEvent(NULL, TRUE, FALSE, NULL);
    if (DeviceIoControl(dev, IOCTL_DMA_ARM_0, NULL, 0, buf, size, &r, &ov) || GetLastError() != ERROR_IO_PENDING) {
        ko("aggancio buffer: doveva restare pendente", GetLastError()); return 1;
    }
    diag(&g);
    out("  buffer agganciato: %lu descrittori nella memoria della scheda\n", g.ChannelDescriptors[0]);
    ok("aggancio (IOCTL 0x9C40A421)");

    p.Channel = 0; p.Value = size;
    if (ioctl(IOCTL_DMA_START, &p, sizeof p, &p, sizeof p, &r)) ok("avvio (IOCTL 0x9C40A440)");
    else ko("avvio", GetLastError());

    w = WaitForSingleObject(ev[3], 2000);
    if (w == WAIT_OBJECT_0) ok("trasferimento completato (evento 3)");
    else out("  nessun dato in 2 s: normale senza una scansione in corso\n");

    p.Channel = 0; p.Value = 0;
    if (ioctl(IOCTL_DMA_STOP, &p, sizeof p, &p, sizeof p, &r)) ok("arresto (IOCTL 0x9C40A444)");
    else if (w == WAIT_OBJECT_0 && GetLastError() == ERROR_NOACCESS) ok("arresto non necessario: era gia' concluso");
    else ko("arresto", GetLastError());

    if (ioctl(IOCTL_DMA_RELEASE_0, NULL, 0, NULL, 0, &r)) ok("rilascio (IOCTL 0x9C40A430)");
    else ko("rilascio", GetLastError());

    if (WaitForSingleObject(ov.hEvent, 3000) == WAIT_OBJECT_0) ok("l'IRP di aggancio si e' chiuso correttamente");
    else ko("l'IRP di aggancio non si e' chiuso", 0);

    diag(&g); print_diag(&g);
    VirtualFree(buf, 0, MEM_RELEASE);
    return failures;
}

static int cmd_regs(void)
{
    ULONG off;
    out("\nRegistri PLX 9080:\n");
    for (off = 0; off < 0x100; off += 4) {
        out("  %02lX: %08lX%s", off, reg(0, off), (off % 16 == 12) ? "\n" : "");
    }
    return 0;
}

static void open_log(void)
{
    char path[MAX_PATH], *slash;
    DWORD n = GetModuleFileNameA(NULL, path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) return;
    slash = strrchr(path, '\\');
    if (!slash) return;
    strcpy(slash + 1, "ektest-esito.txt");
    logf = fopen(path, "w");
    if (!logf) {
        GetTempPathA(MAX_PATH, path);
        strcat(path, "ektest-esito.txt");
        logf = fopen(path, "w");
    }
    if (logf) printf("(esito salvato anche in %s)\n", path);
}

static int run(const char *what, int arg)
{
    int rc;
    if (!open_device()) return 2;
    if (!strcmp(what, "arcnet"))     rc = cmd_arcnet();
    else if (!strcmp(what, "dma"))   rc = cmd_dma(arg);
    else if (!strcmp(what, "regs"))  rc = cmd_regs();
    else                             rc = cmd_info();
    CloseHandle(dev);
    return rc;
}

int main(int argc, char **argv)
{
    int rc;
    DWORD pids[2];
    BOOL doppioClic = (GetConsoleProcessList(pids, 2) == 1);   /* console tutta nostra */
    SYSTEMTIME t;

    open_log();
    GetLocalTime(&t);
    out("ektest - collaudo EKframePnP   %02d/%02d/%04d %02d:%02d\n",
        t.wDay, t.wMonth, t.wYear, t.wHour, t.wMinute);

    if (argc > 1) {
        rc = run(argv[1], argc > 2 ? atoi(argv[2]) : 6);
    } else {
        /* senza argomenti (doppio clic): tutti i controlli in fila */
        rc = run("info", 0);
        if (rc != 2) {
            out("\n(lo scanner deve essere acceso per il controllo ARCNET)\n");
            run("arcnet", 0);
            run("dma", 6);
        }
    }

    out("\n%s\n", failures ? "ESITO: ci sono errori, vedi sopra." :
                  rc == 2 ? "ESITO: il dispositivo non si apre, vedi sopra." :
                            "ESITO: tutto regolare.");
    if (logf) fclose(logf);

    if (doppioClic) {
        printf("\nPremi Invio per chiudere...");
        getchar();
    }
    return failures ? 1 : rc;
}
