/*++

    ekframe.h  -  EKframePnP 2.1

    Driver WDM Plug and Play per la scheda Kodak/Gretag IMAQ
    PCI\VEN_11B2&DEV_0008&SUBSYS_908010B5 - Windows 7 x86.

    Sostituisce EKframe.sys (Eastman Kodak, 2000) mantenendo identica
    l'interfaccia verso le applicazioni: stesso \\.\EKFrameDev, stessi codici
    IOCTL, stesse strutture, stessa semantica di ReadFile/WriteFile.

    Composizione della scheda, ricostruita dal binario originale:

      - bridge PCI PLX PCI 9080 (registri operativi nel BAR da 256 byte);
      - finestra locale da 2 MB, vista dal bus locale a partire da 0xC0000000:
          0x004004..0x004014   registri della FPGA Kodak
          0x008000             controller ARCNET SMC COM20020 (registri a passo 4)
          0x100000             descrittori DMA del canale 0 (256 KB)
          0x140000             descrittori DMA del canale 1 (256 KB)
        il FIFO dei pixel e' all'indirizzo locale 0xC0000000;
      - ARCNET e' il canale comandi verso il controllore della testa di
        scansione (nodo 0xAA); il PC e' il nodo 0x55.

--*/

#ifndef _EKFRAME_H_
#define _EKFRAME_H_

#include <ntddk.h>

#ifndef __drv_dispatchType
#define __drv_dispatchType(x)
#endif

#define EK_DRIVER_VERSION_STRING  "2.1"

//
// ---------------------------------------------------------------------------
//  Nomi - identici all'originale
// ---------------------------------------------------------------------------
//
#define EK_NT_DEVICE_NAME       L"\\Device\\EKframe0"
#define EK_DOS_DEVICE_NAME      L"\\DosDevices\\EKFrameDev"
#define FILE_DEVICE_EKFRAME     0x9c40U     /* unsigned: lo shift di CTL_CODE supera il bit di segno */

//
// ---------------------------------------------------------------------------
//  IOCTL - valori letti dal binario originale e confermati in
//  DMLCmdTransfer.exe. I C_ASSERT in fondo al file garantiscono che non
//  cambino per errore.
// ---------------------------------------------------------------------------
//
#define IOCTL_EK_GET_REGISTER   CTL_CODE(FILE_DEVICE_EKFRAME, 0x900, METHOD_BUFFERED,  FILE_READ_ACCESS)
#define IOCTL_EK_SET_REGISTER   CTL_CODE(FILE_DEVICE_EKFRAME, 0x901, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_GET_PCI_INFO   CTL_CODE(FILE_DEVICE_EKFRAME, 0x902, METHOD_BUFFERED,  FILE_READ_ACCESS)
#define IOCTL_EK_OPEN           CTL_CODE(FILE_DEVICE_EKFRAME, 0x903, METHOD_BUFFERED,  FILE_READ_ACCESS | FILE_WRITE_ACCESS)
#define IOCTL_EK_REGISTER_EVENT CTL_CODE(FILE_DEVICE_EKFRAME, 0x904, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_RELEASE_EVENT  CTL_CODE(FILE_DEVICE_EKFRAME, 0x905, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_SIGNAL_EVENT   CTL_CODE(FILE_DEVICE_EKFRAME, 0x906, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_DMA_ARM_0      CTL_CODE(FILE_DEVICE_EKFRAME, 0x908, METHOD_IN_DIRECT, FILE_WRITE_ACCESS)
#define IOCTL_EK_DMA_ARM_1      CTL_CODE(FILE_DEVICE_EKFRAME, 0x909, METHOD_IN_DIRECT, FILE_WRITE_ACCESS)
#define IOCTL_EK_DMA_RELEASE_0  CTL_CODE(FILE_DEVICE_EKFRAME, 0x90C, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_DMA_RELEASE_1  CTL_CODE(FILE_DEVICE_EKFRAME, 0x90D, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_DMA_START      CTL_CODE(FILE_DEVICE_EKFRAME, 0x910, METHOD_BUFFERED,  FILE_WRITE_ACCESS)
#define IOCTL_EK_DMA_STOP       CTL_CODE(FILE_DEVICE_EKFRAME, 0x911, METHOD_BUFFERED,  FILE_WRITE_ACCESS)

//
// ---------------------------------------------------------------------------
//  Strutture scambiate con l'utente
// ---------------------------------------------------------------------------
//
#define EK_SPACE_PLX            0       // registri operativi PLX 9080
#define EK_SPACE_LOCAL          1       // finestra locale da 2 MB

typedef struct _EK_REGISTER_ACCESS {    // GET_REGISTER / SET_REGISTER
    ULONG   Space;
    ULONG   Offset;
    ULONG   Value;
} EK_REGISTER_ACCESS, *PEK_REGISTER_ACCESS;

typedef struct _EK_PCI_INFO {           // GET_PCI_INFO
    USHORT  DeviceId;                   // cfg 0x02  (precede VendorId, come l'originale)
    USHORT  VendorId;                   // cfg 0x00
    UCHAR   RevisionId;                 // cfg 0x08
    UCHAR   ProgIf;                     // cfg 0x09
    UCHAR   BaseClass;                  // cfg 0x0B
    UCHAR   SubClass;                   // cfg 0x0A
    UCHAR   InterruptLine;              // cfg 0x3C
    UCHAR   InterruptPin;               // cfg 0x3D
    UCHAR   MinimumGrant;               // cfg 0x3E
    UCHAR   MaximumLatency;             // cfg 0x3F
    UCHAR   CacheLineSize;              // cfg 0x0C
    UCHAR   LatencyTimer;               // cfg 0x0D
    UCHAR   Reserved0[2];
    ULONG   RomBaseAddress;             // cfg 0x30
    ULONG   BaseAddresses[6];           // cfg 0x10..0x27
} EK_PCI_INFO, *PEK_PCI_INFO;

typedef struct _EK_OPEN_PARAMS {        // OPEN (inizializzazione scheda + ARCNET)
    UCHAR   Reinit;                     // +0  ignorato nella sostanza, come l'originale
    UCHAR   Reserved[3];
    ULONG   NodeId;                     // +4  nodo ARCNET del PC        (DMLCmdTransfer: 0x55)
    ULONG   DestId;                     // +8  nodo ARCNET dello scanner (DMLCmdTransfer: 0xAA)
    ULONG   Status;                     // +C  uscita: bit0 = inizializzazione ARCNET fallita
} EK_OPEN_PARAMS, *PEK_OPEN_PARAMS;

typedef struct _EK_EVENT_PARAMS {       // REGISTER_EVENT / RELEASE_EVENT / SIGNAL_EVENT
    ULONG   Index;                      // 0..4
    HANDLE  Event;                      // solo REGISTER_EVENT
} EK_EVENT_PARAMS, *PEK_EVENT_PARAMS;

typedef struct _EK_DMA_PARAMS {         // DMA_START / DMA_STOP
    ULONG   Channel;                    // 0 o 1
    ULONG   Value;                      // START: byte da trasferire; STOP: uscita (0)
} EK_DMA_PARAMS, *PEK_DMA_PARAMS;

//
// IOCTL diagnostico, NON presente nell'originale (funzione 0xA00, fuori dallo
// spazio usato dalle applicazioni Kodak). Lo usa ektest.exe.
//
#define IOCTL_EK_DIAG           CTL_CODE(FILE_DEVICE_EKFRAME, 0xA00, METHOD_BUFFERED,  FILE_READ_ACCESS)
#define EK_DIAG_VERSION         0x00020001

typedef struct _EK_DIAG {
    ULONG   Version;
    ULONG   PlxPhysical;
    ULONG   PlxLength;
    ULONG   LocalPhysical;
    ULONG   LocalLength;
    ULONG   Vector;
    ULONG   Irql;
    ULONG   InterruptConnected;
    ULONG   MapRegisters[2];
    ULONG   IntMask;
    LONG    ActiveChannel;
    ULONG   NetReady;
    ULONG   NodeId;
    ULONG   DestId;
    ULONG   LastArcStatus;
    ULONG   ArcInitResult;
    ULONG   RxHead;
    ULONG   RxTail;
    ULONG   OpenCount;
    ULONG   IsrCount;
    ULONG   IsrClaimed;
    ULONG   DmaDoneCount;
    ULONG   RxCount;
    ULONG   RxDropped;
    ULONG   TxCount;
    ULONG   TxFailed;
    ULONG   ChannelArmed[2];
    ULONG   ChannelBytes[2];
    ULONG   ChannelDescriptors[2];
    ULONG   PlxIntcsr;
    ULONG   PlxDmacsr;
    ULONG   LocalIntStatus;
    ULONG   LocalIntEnable;
    ULONG   LocalMode;
} EK_DIAG, *PEK_DIAG;

//
// Significato dei cinque eventi, ricostruito dalle DPC dell'originale.
//
#define EK_EVENT_NET_READY      0       // ARCNET: rete configurata, scanner raggiungibile
#define EK_EVENT_NET_LOST       1       // ARCNET: riconfigurazione, collegamento perso
#define EK_EVENT_MESSAGE        2       // ARCNET: messaggio ricevuto, da leggere con ReadFile
#define EK_EVENT_DMA_DONE       3       // DMA: trasferimento completato
#define EK_EVENT_AUX            4       // previsto dall'originale, mai segnalato dalla ISR
#define EK_MAX_EVENTS           5

//
// ---------------------------------------------------------------------------
//  Registri PLX PCI 9080
// ---------------------------------------------------------------------------
//
#define PLX_LBRD0               0x18
#define PLX_INTCSR              0x68
#define PLX_DMAMODE0            0x80
#define PLX_DMADPR0             0x90
#define PLX_DMACSR0             0xA8

#define PLX_LBRD0_VALUE         0x40000043UL
#define PLX_DMAMODE0_VALUE      0x00001F43UL
#define PLX_INTCSR_ENABLE_BITS  0x00040800UL    // bit 11 int. locale, bit 18 int. DMA canale 0
#define PLX_INTCSR_DMA0_ACTIVE  0x00200000UL    // bit 21

#define PLX_DMACSR_ENABLE       0x01
#define PLX_DMACSR_START        0x02
#define PLX_DMACSR_ABORT        0x04
#define PLX_DMACSR_CLEAR_INT    0x08

//
// ---------------------------------------------------------------------------
//  Finestra locale
// ---------------------------------------------------------------------------
//
#define EKL_FIFO_RESET          0x4004      // impulso: 8, attesa 1 us, 0
#define EKL_INT_ENABLE          0x4008      // maschera interrupt della FPGA
#define EKL_INT_STATUS          0x400C      // stato interrupt (filtrato con la maschera)
#define EKL_MODE                0x4014      // bit0 messaggi in attesa, bit1 trasmissione in corso
#define EKL_ARCNET              0x8000      // COM20020, registri a passo 4 byte
#define EKL_DESC_BASE           0x100000    // descrittori del canale 0
#define EKL_DESC_STRIDE         0x40000     // 256 KB per canale
#define EKL_MIN_WINDOW          0x180000    // la finestra deve contenere entrambe le aree

#define EK_LOCAL_BUS_BASE       0xC0000000UL    // indirizzo locale dell'offset 0 della finestra
#define EK_FIFO_LOCAL_ADDRESS   0xC0000000UL

#define EK_INT_DMA              0x02
#define EK_INT_ARCNET           0x04
#define EK_INT_FIFO             0x08
#define EK_INT_DEFAULT_MASK     (EK_INT_DMA | EK_INT_ARCNET)

#define EK_MODE_RX_PENDING      0x01
#define EK_MODE_TX_PENDING      0x02

//
// ---------------------------------------------------------------------------
//  DMA
// ---------------------------------------------------------------------------
//
#define EK_DMA_CHANNELS         2
#define EK_DMA_MAX_TRANSFER     0x3105560UL     // ~49 MB, limite dell'originale
#define EK_DMA_MAX_SEGMENT      0x400000UL      // 4 MB per descrittore
#define EK_DMA_MAX_DESCRIPTORS  0x4000          // 16384 = 256 KB / 16

#define PLX_DESC_NEXT_DIR_TO_PCI    0x08        // direzione locale -> PCI
#define PLX_DESC_NEXT_LAST          0x0E        // fine catena + int. a fine conteggio + direzione

//
// ---------------------------------------------------------------------------
//  ARCNET (SMC COM20020)
// ---------------------------------------------------------------------------
//
#define EK_RX_RING_SIZE         64
#define EK_RX_MESSAGE_MAX       0x200
#define EK_TX_MESSAGE_MAX       0x1FB           // 507 byte, come l'originale

typedef struct _EK_RX_ENTRY {
    ULONG   Source;                             // nodo mittente (0 = vuoto)
    ULONG   Length;
    UCHAR   Data[EK_RX_MESSAGE_MAX];
} EK_RX_ENTRY, *PEK_RX_ENTRY;

//
// ---------------------------------------------------------------------------
//  Stato per canale DMA
// ---------------------------------------------------------------------------
//
struct _DEVICE_EXTENSION;

typedef struct _EK_CHANNEL {
    struct _DEVICE_EXTENSION *DevExt;
    ULONG                   Index;
    PDMA_ADAPTER            Adapter;
    ULONG                   MapRegisters;

    KSPIN_LOCK              Lock;
    PIRP                    Irp;                // IRP di aggancio, pendente
    PSCATTER_GATHER_LIST    SgList;
    BOOLEAN                 Armed;              // catena di descrittori costruita
    BOOLEAN                 CancelRequested;    // annullamento arrivato prima della catena
    BOOLEAN                 SgError;

    ULONG                   ByteCount;
    PUCHAR                  DescVa;             // descrittori nella finestra mappata
    ULONG                   DescLocal;          // stessi descrittori visti dal bus locale
    ULONG                   DescCount;
    PULONG                  DescSizes;          // copia lato host delle dimensioni

    LONG                    TruncDesc;          // -1 = catena intera
    ULONG                   TruncLength;
} EK_CHANNEL, *PEK_CHANNEL;

//
// ---------------------------------------------------------------------------
//  Device extension
// ---------------------------------------------------------------------------
//
typedef struct _DEVICE_EXTENSION {

    PDEVICE_OBJECT      Self;
    PDEVICE_OBJECT      LowerDeviceObject;
    PDEVICE_OBJECT      Pdo;
    BOOLEAN             SymbolicLinkCreated;
    IO_REMOVE_LOCK      RemoveLock;

    BOOLEAN             Started;
    LONG                OpenCount;

    // risorse
    PUCHAR              PlxRegBase;
    ULONG               PlxRegLength;
    PUCHAR              LocalRegBase;
    ULONG               LocalRegLength;
    PUCHAR              ArcBase;

    PKINTERRUPT         InterruptObject;
    ULONG               Vector;
    KIRQL               Irql;
    KAFFINITY           Affinity;
    KINTERRUPT_MODE     InterruptMode;
    BOOLEAN             ShareVector;

    UCHAR               PciConfig[256];
    BOOLEAN             PciConfigValid;

    // stato hardware (protetto dall'interrupt)
    ULONG               IntMask;
    LONG                ActiveChannel;          // -1 = nessun DMA in corso
    BOOLEAN             PlxInitialized;
    BOOLEAN             NetReady;
    BOOLEAN             TxPending;
    ULONG               LastArcStatus;

    // ARCNET
    ULONG               NodeId;
    ULONG               DestId;
    PEK_RX_ENTRY        RxRing;
    volatile ULONG      RxHead;                 // scritto dalla ISR
    volatile ULONG      RxTail;                 // scritto da ReadFile
    KSPIN_LOCK          RxLock;

    // eventi utente
    KSPIN_LOCK          EventLock;
    PKEVENT             Event[EK_MAX_EVENTS];

    // DPC
    KDPC                DpcNetReady;
    KDPC                DpcNetLost;
    KDPC                DpcMessage;
    KDPC                DpcTxDone;
    KDPC                DpcDma;

    // coda seriale delle richieste (equivale a StartIo dell'originale)
    KSPIN_LOCK          QueueLock;
    LIST_ENTRY          Queue;
    PIRP                CurrentIrp;
    PIRP                WriteIrp;               // WriteFile in attesa di conferma ARCNET
    ULONG               WriteLength;

    // DMA
    EK_CHANNEL          Channel[EK_DMA_CHANNELS];

    // diagnostica (IOCTL_EK_DIAG)
    PHYSICAL_ADDRESS    PlxPhys;
    PHYSICAL_ADDRESS    LocalPhys;
    volatile ULONG      IsrCount;
    volatile ULONG      IsrClaimed;
    volatile ULONG      DmaDoneCount;
    volatile ULONG      RxCount;
    volatile ULONG      RxDropped;
    volatile ULONG      TxCount;
    volatile ULONG      TxFailed;
    ULONG               ArcInitResult;          // 0 mai, 1 riuscita, 2 fallita

} DEVICE_EXTENSION, *PDEVICE_EXTENSION;

#define EK_POOL_TAG     0x6D617246UL    /* 'marF' -> "Fram" nei dump del pool */

//
// ---------------------------------------------------------------------------
//  Verifiche a tempo di compilazione
// ---------------------------------------------------------------------------
//
C_ASSERT(IOCTL_EK_GET_REGISTER   == 0x9C406400);
C_ASSERT(IOCTL_EK_SET_REGISTER   == 0x9C40A404);
C_ASSERT(IOCTL_EK_GET_PCI_INFO   == 0x9C406408);
C_ASSERT(IOCTL_EK_OPEN           == 0x9C40E40C);
C_ASSERT(IOCTL_EK_REGISTER_EVENT == 0x9C40A410);
C_ASSERT(IOCTL_EK_RELEASE_EVENT  == 0x9C40A414);
C_ASSERT(IOCTL_EK_SIGNAL_EVENT   == 0x9C40A418);
C_ASSERT(IOCTL_EK_DMA_ARM_0      == 0x9C40A421);
C_ASSERT(IOCTL_EK_DMA_ARM_1      == 0x9C40A425);
C_ASSERT(IOCTL_EK_DMA_RELEASE_0  == 0x9C40A430);
C_ASSERT(IOCTL_EK_DMA_RELEASE_1  == 0x9C40A434);
C_ASSERT(IOCTL_EK_DMA_START      == 0x9C40A440);
C_ASSERT(IOCTL_EK_DMA_STOP       == 0x9C40A444);
C_ASSERT(sizeof(EK_REGISTER_ACCESS) == 12);
C_ASSERT(sizeof(EK_PCI_INFO)        == 0x2C);
C_ASSERT(sizeof(EK_OPEN_PARAMS)     == 0x10);
C_ASSERT(sizeof(EK_EVENT_PARAMS)    == 8);
C_ASSERT(sizeof(EK_DMA_PARAMS)      == 8);

//
// ---------------------------------------------------------------------------
//  Prototipi
// ---------------------------------------------------------------------------
//

// driver.c
DRIVER_INITIALIZE DriverEntry;
NTSTATUS NTAPI EkAddDevice(PDRIVER_OBJECT DriverObject, PDEVICE_OBJECT Pdo);
VOID     NTAPI EkUnload(PDRIVER_OBJECT DriverObject);
NTSTATUS NTAPI EkCreate(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkClose(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkCleanup(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkRead(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkWrite(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkDeviceControl(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkPower(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkSystemControl(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS NTAPI EkPnp(PDEVICE_OBJECT DeviceObject, PIRP Irp);

VOID     EkCompleteIrp(PDEVICE_EXTENSION DevExt, PIRP Irp, NTSTATUS Status, CCHAR Boost);
VOID     EkCompleteWrite(PDEVICE_EXTENSION DevExt, BOOLEAN Success);
VOID NTAPI EkWriteCancel(PDEVICE_OBJECT DeviceObject, PIRP Irp);
NTSTATUS EkQueueIrp(PDEVICE_EXTENSION DevExt, PIRP Irp);
VOID     EkStartNextIrp(PDEVICE_EXTENSION DevExt);
VOID     EkFlushRequests(PDEVICE_EXTENSION DevExt, PFILE_OBJECT FileObject, NTSTATUS Status);
VOID     EkShutdownHardware(PDEVICE_EXTENSION DevExt);

// pnp.c
NTSTATUS EkStartDevice(PDEVICE_EXTENSION DevExt, PCM_RESOURCE_LIST Translated);
VOID     EkStopDevice(PDEVICE_EXTENSION DevExt);

// ioctl.c
NTSTATUS EkProcessQueuedIrp(PDEVICE_EXTENSION DevExt, PIRP Irp);
NTSTATUS EkRegisterEvent(PDEVICE_EXTENSION DevExt, PIRP Irp);
NTSTATUS EkReleaseEvent(PDEVICE_EXTENSION DevExt, PIRP Irp);
VOID     EkSignalEvent(PDEVICE_EXTENSION DevExt, ULONG Index);
VOID     EkReleaseAllEvents(PDEVICE_EXTENSION DevExt);

// hw.c
VOID     EkPlxWrite(PDEVICE_EXTENSION DevExt, ULONG Offset, ULONG Value);
ULONG    EkPlxRead(PDEVICE_EXTENSION DevExt, ULONG Offset);
VOID     EkLocalWrite(PDEVICE_EXTENSION DevExt, ULONG Offset, ULONG Value);
ULONG    EkLocalRead(PDEVICE_EXTENSION DevExt, ULONG Offset);
BOOLEAN  EkOffsetValid(ULONG Offset, ULONG Length);
VOID     EkFifoReset(PDEVICE_EXTENSION DevExt);
VOID     EkSetModeBit(PDEVICE_EXTENSION DevExt, ULONG Bit, BOOLEAN On);
VOID     EkSetIntMask(PDEVICE_EXTENSION DevExt, ULONG Mask);
VOID     EkPlxEnableInterrupts(PDEVICE_EXTENSION DevExt, BOOLEAN Enable);
VOID     EkPlxInitLocalBus(PDEVICE_EXTENSION DevExt);
VOID     EkDmaDisable(PDEVICE_EXTENSION DevExt);
VOID     EkDmaAbort(PDEVICE_EXTENSION DevExt);
VOID     EkDmaClearInterrupt(PDEVICE_EXTENSION DevExt);
BOOLEAN  EkSync(PDEVICE_EXTENSION DevExt, PKSYNCHRONIZE_ROUTINE Routine, PVOID Context);
KSERVICE_ROUTINE EkIsr;
KDEFERRED_ROUTINE EkDpcNetReady;
KDEFERRED_ROUTINE EkDpcNetLost;
KDEFERRED_ROUTINE EkDpcMessage;
KDEFERRED_ROUTINE EkDpcTxDone;
KDEFERRED_ROUTINE EkDpcDma;

// arcnet.c
BOOLEAN  EkArcInit(PUCHAR Arc, UCHAR NodeId);
VOID     EkArcShutdown(PUCHAR Arc);
BOOLEAN  EkArcSend(PUCHAR Arc, UCHAR Source, UCHAR Dest, PUCHAR Data, ULONG Length);
BOOLEAN  EkArcReceive(PUCHAR Arc, PULONG Source, PUCHAR Data, PULONG Length, ULONG VerifyRetries);
ULONG    EkArcStatus(PUCHAR Arc);
UCHAR    EkArcReadNextId(PUCHAR Arc);

// dma.c
NTSTATUS EkDmaArm(PDEVICE_EXTENSION DevExt, PIRP Irp, ULONG Channel);
NTSTATUS EkDmaStart(PDEVICE_EXTENSION DevExt, ULONG Channel, ULONG Length);
NTSTATUS EkDmaStop(PDEVICE_EXTENSION DevExt, ULONG Channel);
NTSTATUS EkDmaRelease(PDEVICE_EXTENSION DevExt, ULONG Channel);
VOID     EkDmaFlush(PDEVICE_EXTENSION DevExt, PFILE_OBJECT FileObject, NTSTATUS Status);

#endif // _EKFRAME_H_
