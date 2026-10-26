/*++

    ioctl.c  -  EKframePnP 2.1

    Gestori delle richieste. Come nell'originale:

      - serviti subito, nel contesto del chiamante:
            REGISTER_EVENT, RELEASE_EVENT, DMA_ARM_0/1, ReadFile
      - serializzati in coda (l'originale usava IoStartPacket/StartIo),
        eseguiti a DISPATCH_LEVEL:
            WriteFile e tutti gli altri IOCTL

    Dimensioni dei buffer, codici di errore e valori di Information sono
    quelli dell'originale, perche' le applicazioni li controllano.

--*/

#include "ekframe.h"

VOID NTAPI EkWriteCancel(PDEVICE_OBJECT DeviceObject, PIRP Irp);


//
// L'originale accettava la richiesta se ALMENO uno dei due buffer aveva la
// dimensione giusta. Con METHOD_BUFFERED il buffer di sistema e' grande
// max(in, out): la parte non coperta dall'ingresso viene azzerata, cosi' non
// si legge ne' si restituisce memoria del kernel non inizializzata.
//
static BOOLEAN
EkCheckBuffer(PIRP Irp, ULONG Size)
{
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(Irp);
    ULONG inLen  = sp->Parameters.DeviceIoControl.InputBufferLength;
    ULONG outLen = sp->Parameters.DeviceIoControl.OutputBufferLength;

    if (inLen != Size && outLen != Size) {
        return FALSE;
    }
    if (Irp->AssociatedIrp.SystemBuffer == NULL) {
        return FALSE;
    }
    if (inLen < Size) {
        RtlZeroMemory((PUCHAR)Irp->AssociatedIrp.SystemBuffer + inLen, Size - inLen);
    }
    return TRUE;
}


//
// ---------------------------------------------------------------------------
//  Eventi
// ---------------------------------------------------------------------------
//
VOID
EkSignalEvent(PDEVICE_EXTENSION d, ULONG Index)
{
    KIRQL old;

    KeAcquireSpinLock(&d->EventLock, &old);
    if (d->Event[Index] != NULL) {
        KeSetEvent(d->Event[Index], 0, FALSE);
    }
    KeReleaseSpinLock(&d->EventLock, old);
}


VOID
EkReleaseAllEvents(PDEVICE_EXTENSION d)
{
    PKEVENT ev[EK_MAX_EVENTS];
    KIRQL   old;
    ULONG   i;

    KeAcquireSpinLock(&d->EventLock, &old);
    for (i = 0; i < EK_MAX_EVENTS; i++) {
        ev[i] = d->Event[i];
        d->Event[i] = NULL;
    }
    KeReleaseSpinLock(&d->EventLock, old);

    for (i = 0; i < EK_MAX_EVENTS; i++) {
        if (ev[i] != NULL) {
            ObDereferenceObject(ev[i]);
        }
    }
}


NTSTATUS
EkRegisterEvent(PDEVICE_EXTENSION d, PIRP Irp)          // 0x11EF0
{
    PEK_EVENT_PARAMS p;
    PVOID   object;
    KIRQL   old;
    NTSTATUS status;

    if (!EkCheckBuffer(Irp, sizeof(EK_EVENT_PARAMS))) {
        return STATUS_INVALID_PARAMETER;
    }
    p = (PEK_EVENT_PARAMS)Irp->AssociatedIrp.SystemBuffer;
    if (p->Index >= EK_MAX_EVENTS) {
        return STATUS_INVALID_PARAMETER;
    }
    if (d->Event[p->Index] != NULL) {
        return STATUS_DEVICE_BUSY;
    }

    //
    // L'originale referenziava l'handle senza tipo e in modalita' kernel,
    // cioe' senza alcun controllo: qui si pretende un oggetto evento e si
    // verificano i diritti del chiamante.
    //
    status = ObReferenceObjectByHandle(p->Event, EVENT_MODIFY_STATE, *ExEventObjectType,
                                       Irp->RequestorMode, &object, NULL);
    if (!NT_SUCCESS(status)) {
        return status;
    }

    KeAcquireSpinLock(&d->EventLock, &old);
    if (d->Event[p->Index] != NULL) {
        KeReleaseSpinLock(&d->EventLock, old);
        ObDereferenceObject(object);
        return STATUS_DEVICE_BUSY;
    }
    d->Event[p->Index] = (PKEVENT)object;
    KeReleaseSpinLock(&d->EventLock, old);

    Irp->IoStatus.Information = sizeof(EK_EVENT_PARAMS);
    return STATUS_SUCCESS;
}


NTSTATUS
EkReleaseEvent(PDEVICE_EXTENSION d, PIRP Irp)           // 0x11F7A
{
    PEK_EVENT_PARAMS p;
    PKEVENT ev;
    KIRQL   old;

    if (!EkCheckBuffer(Irp, sizeof(EK_EVENT_PARAMS))) {
        return STATUS_INVALID_PARAMETER;
    }
    p = (PEK_EVENT_PARAMS)Irp->AssociatedIrp.SystemBuffer;
    if (p->Index >= EK_MAX_EVENTS) {
        return STATUS_INVALID_PARAMETER;
    }

    KeAcquireSpinLock(&d->EventLock, &old);
    ev = d->Event[p->Index];
    d->Event[p->Index] = NULL;
    KeReleaseSpinLock(&d->EventLock, old);

    if (ev == NULL) {
        return STATUS_ACCESS_VIOLATION;
    }
    ObDereferenceObject(ev);

    Irp->IoStatus.Information = sizeof(EK_EVENT_PARAMS);
    return STATUS_SUCCESS;
}


static NTSTATUS
EkSignalEventIoctl(PDEVICE_EXTENSION d, PIRP Irp)       // 0x11FD6
{
    PEK_EVENT_PARAMS p;
    NTSTATUS status = STATUS_SUCCESS;

    if (!EkCheckBuffer(Irp, sizeof(EK_EVENT_PARAMS))) {
        return STATUS_INVALID_PARAMETER;
    }
    p = (PEK_EVENT_PARAMS)Irp->AssociatedIrp.SystemBuffer;
    if (p->Index >= EK_MAX_EVENTS) {
        return STATUS_INVALID_PARAMETER;
    }

    KeAcquireSpinLockAtDpcLevel(&d->EventLock);
    if (d->Event[p->Index] == NULL) {
        status = STATUS_ACCESS_VIOLATION;
    } else {
        KeSetEvent(d->Event[p->Index], 0, FALSE);
    }
    KeReleaseSpinLockFromDpcLevel(&d->EventLock);

    if (NT_SUCCESS(status)) {
        Irp->IoStatus.Information = sizeof(EK_EVENT_PARAMS);
    }
    return status;
}


//
// ---------------------------------------------------------------------------
//  Registri e informazioni PCI
// ---------------------------------------------------------------------------
//
static NTSTATUS
EkRegisterAccess(PDEVICE_EXTENSION d, PIRP Irp, BOOLEAN Write)   // 0x11C5E / 0x11CD4
{
    PEK_REGISTER_ACCESS r;
    PUCHAR base;
    ULONG  length;

    if (!EkCheckBuffer(Irp, sizeof(EK_REGISTER_ACCESS))) {
        return STATUS_INVALID_PARAMETER;
    }
    r = (PEK_REGISTER_ACCESS)Irp->AssociatedIrp.SystemBuffer;

    switch (r->Space) {
    case EK_SPACE_PLX:
        base   = d->PlxRegBase;
        length = d->PlxRegLength;
        break;
    case EK_SPACE_LOCAL:
        base   = d->LocalRegBase;
        length = d->LocalRegLength;
        break;
    default:
        return STATUS_ACCESS_VIOLATION;
    }

    //
    // L'originale controllava solo Offset < Length: una lettura a 32 bit
    // vicino al limite usciva dalla finestra. Qui tutti e 4 i byte devono
    // stare dentro, e l'offset deve essere allineato.
    //
    if (base == NULL || !EkOffsetValid(r->Offset, length)) {
        return STATUS_ACCESS_VIOLATION;
    }

    if (Write) {
        WRITE_REGISTER_ULONG((PULONG)(base + r->Offset), r->Value);
    } else {
        r->Value = READ_REGISTER_ULONG((PULONG)(base + r->Offset));
    }

    Irp->IoStatus.Information = sizeof(EK_REGISTER_ACCESS);
    return STATUS_SUCCESS;
}


static NTSTATUS
EkGetPciInfo(PDEVICE_EXTENSION d, PIRP Irp)             // 0x11D4A
{
    PEK_PCI_INFO info;
    PUCHAR cfg = d->PciConfig;
    ULONG  i;

    if (!EkCheckBuffer(Irp, sizeof(EK_PCI_INFO))) {
        return STATUS_INVALID_PARAMETER;
    }
    info = (PEK_PCI_INFO)Irp->AssociatedIrp.SystemBuffer;
    RtlZeroMemory(info, sizeof(EK_PCI_INFO));

    info->DeviceId       = *(USHORT UNALIGNED *)(cfg + 0x02);
    info->VendorId       = *(USHORT UNALIGNED *)(cfg + 0x00);
    info->RevisionId     = cfg[0x08];
    info->ProgIf         = cfg[0x09];
    info->BaseClass      = cfg[0x0B];
    info->SubClass       = cfg[0x0A];
    info->InterruptLine  = cfg[0x3C];
    info->InterruptPin   = cfg[0x3D];
    info->MinimumGrant   = cfg[0x3E];
    info->MaximumLatency = cfg[0x3F];
    info->CacheLineSize  = cfg[0x0C];
    info->LatencyTimer   = cfg[0x0D];
    info->RomBaseAddress = *(ULONG UNALIGNED *)(cfg + 0x30);
    for (i = 0; i < 6; i++) {
        info->BaseAddresses[i] = *(ULONG UNALIGNED *)(cfg + 0x10 + i * 4);
    }

    Irp->IoStatus.Information = sizeof(EK_PCI_INFO);
    return STATUS_SUCCESS;
}


//
// ---------------------------------------------------------------------------
//  Apertura: inizializza bridge, interrupt e ARCNET          (0x11DE2)
// ---------------------------------------------------------------------------
//
static BOOLEAN NTAPI
EkOpenPrepareSync(PVOID Context)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)Context;

    EkSetModeBit(d, EK_MODE_TX_PENDING, FALSE);
    EkSetModeBit(d, EK_MODE_RX_PENDING, FALSE);
    EkFifoReset(d);

    if (!d->PlxInitialized) {
        EkPlxInitLocalBus(d);
        EkFifoReset(d);
        d->PlxInitialized = TRUE;
    }

    if (EkPlxRead(d, PLX_INTCSR) & PLX_INTCSR_DMA0_ACTIVE) {
        EkDmaDisable(d);
        EkDmaClearInterrupt(d);
    }

    EkFifoReset(d);
    d->NetReady  = FALSE;
    d->TxPending = FALSE;
    d->RxHead    = 0;
    d->RxTail    = 0;
    return TRUE;
}


static NTSTATUS
EkOpen(PDEVICE_EXTENSION d, PIRP Irp)
{
    PEK_OPEN_PARAMS p;

    if (!EkCheckBuffer(Irp, sizeof(EK_OPEN_PARAMS))) {
        return STATUS_INVALID_PARAMETER;
    }
    p = (PEK_OPEN_PARAMS)Irp->AssociatedIrp.SystemBuffer;
    p->Status = 0;

    EkSync(d, EkOpenPrepareSync, d);

    EkPlxEnableInterrupts(d, TRUE);
    EkSetIntMask(d, EK_INT_DEFAULT_MASK);

    d->NodeId = p->NodeId;
    d->DestId = p->DestId;

    //
    // Durante l'inizializzazione il COM20020 ha la maschera interrupt a zero
    // (e' la prima cosa che scrive), quindi la ISR non lo tocca finche'
    // l'ultima istruzione non riabilita gli interrupt.
    //
    if (EkArcInit(d->ArcBase, (UCHAR)p->NodeId)) {
        d->ArcInitResult = 1;
    } else {
        d->ArcInitResult = 2;
        p->Status |= 1;
    }

    Irp->IoStatus.Information = sizeof(EK_OPEN_PARAMS);
    return STATUS_SUCCESS;
}


//
// ---------------------------------------------------------------------------
//  WriteFile: un messaggio ARCNET verso lo scanner          (0x1191A)
// ---------------------------------------------------------------------------
//
typedef struct _EK_SEND_CONTEXT {
    PDEVICE_EXTENSION   DevExt;
    PUCHAR              Data;
    ULONG               Length;
} EK_SEND_CONTEXT;

static BOOLEAN NTAPI
EkSendSync(PVOID Context)                               // 0x119F8
{
    EK_SEND_CONTEXT *c = (EK_SEND_CONTEXT *)Context;
    PDEVICE_EXTENSION d = c->DevExt;

    if (!EkArcSend(d->ArcBase, (UCHAR)d->NodeId, (UCHAR)d->DestId, c->Data, c->Length)) {
        return FALSE;
    }
    d->TxPending = TRUE;
    EkSetModeBit(d, EK_MODE_TX_PENDING, TRUE);
    return TRUE;
}


static NTSTATUS
EkStartWrite(PDEVICE_EXTENSION d, PIRP Irp)
{
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(Irp);
    ULONG length = sp->Parameters.Write.Length;
    EK_SEND_CONTEXT ctx;

    Irp->IoStatus.Information = 0;

    if (!d->NetReady) {
        return STATUS_DEVICE_NOT_CONNECTED;             // come l'originale
    }
    if (length == 0 || Irp->AssociatedIrp.SystemBuffer == NULL) {
        return STATUS_INVALID_PARAMETER;
    }
    if (length > EK_TX_MESSAGE_MAX) {
        d->TxFailed++;
        return STATUS_DEVICE_DATA_ERROR;                // l'originale rifiutava cosi'
    }

    //
    // L'IRP diventa completabile (conferma o annullamento) appena la
    // trasmissione parte: va registrato prima, e dopo non va piu' toccato.
    //
    KeAcquireSpinLockAtDpcLevel(&d->QueueLock);
    d->WriteIrp    = Irp;
    d->WriteLength = length;
    IoSetCancelRoutine(Irp, EkWriteCancel);
    if (Irp->Cancel) {
        if (IoSetCancelRoutine(Irp, NULL) != NULL) {
            d->WriteIrp = NULL;
            KeReleaseSpinLockFromDpcLevel(&d->QueueLock);
            return STATUS_CANCELLED;
        }
        KeReleaseSpinLockFromDpcLevel(&d->QueueLock);
        return STATUS_PENDING;                          // lo completa la routine di annullamento
    }
    KeReleaseSpinLockFromDpcLevel(&d->QueueLock);

    ctx.DevExt = d;
    ctx.Data   = (PUCHAR)Irp->AssociatedIrp.SystemBuffer;
    ctx.Length = length;

    if (!EkSync(d, EkSendSync, &ctx)) {
        KeAcquireSpinLockAtDpcLevel(&d->QueueLock);
        if (d->WriteIrp == Irp && IoSetCancelRoutine(Irp, NULL) != NULL) {
            d->WriteIrp = NULL;
            KeReleaseSpinLockFromDpcLevel(&d->QueueLock);
            d->TxFailed++;
            return STATUS_DEVICE_DATA_ERROR;
        }
        KeReleaseSpinLockFromDpcLevel(&d->QueueLock);
        return STATUS_PENDING;
    }

    d->TxCount++;
    return STATUS_PENDING;
}


//
// ---------------------------------------------------------------------------
//  Esecuzione delle richieste in coda (equivale a StartIo + 0x11B52)
// ---------------------------------------------------------------------------
//
NTSTATUS
EkProcessQueuedIrp(PDEVICE_EXTENSION d, PIRP Irp)
{
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(Irp);
    PEK_DMA_PARAMS dp;

    Irp->IoStatus.Information = 0;

    if (sp->MajorFunction == IRP_MJ_WRITE) {
        return EkStartWrite(d, Irp);
    }

    if (sp->MajorFunction != IRP_MJ_DEVICE_CONTROL) {
        return STATUS_NOT_IMPLEMENTED;
    }

    switch (sp->Parameters.DeviceIoControl.IoControlCode) {

    case IOCTL_EK_GET_REGISTER:
        return EkRegisterAccess(d, Irp, FALSE);

    case IOCTL_EK_SET_REGISTER:
        return EkRegisterAccess(d, Irp, TRUE);

    case IOCTL_EK_GET_PCI_INFO:
        return EkGetPciInfo(d, Irp);

    case IOCTL_EK_OPEN:
        return EkOpen(d, Irp);

    case IOCTL_EK_SIGNAL_EVENT:
        return EkSignalEventIoctl(d, Irp);

    case IOCTL_EK_DMA_RELEASE_0:
        return EkDmaRelease(d, 0);

    case IOCTL_EK_DMA_RELEASE_1:
        return EkDmaRelease(d, 1);

    case IOCTL_EK_DMA_START:
        if (!EkCheckBuffer(Irp, sizeof(EK_DMA_PARAMS))) {
            return STATUS_INVALID_PARAMETER;
        }
        dp = (PEK_DMA_PARAMS)Irp->AssociatedIrp.SystemBuffer;
        {
            NTSTATUS status = EkDmaStart(d, dp->Channel, dp->Value);
            if (NT_SUCCESS(status)) {
                Irp->IoStatus.Information = sizeof(EK_DMA_PARAMS);
            }
            return status;
        }

    case IOCTL_EK_DMA_STOP:
        if (!EkCheckBuffer(Irp, sizeof(EK_DMA_PARAMS))) {
            return STATUS_INVALID_PARAMETER;
        }
        dp = (PEK_DMA_PARAMS)Irp->AssociatedIrp.SystemBuffer;
        {
            NTSTATUS status = EkDmaStop(d, dp->Channel);
            if (NT_SUCCESS(status)) {
                dp->Value = 0;
                Irp->IoStatus.Information = sizeof(EK_DMA_PARAMS);
            }
            return status;
        }

    default:
        return STATUS_NOT_IMPLEMENTED;                  // come l'originale
    }
}
