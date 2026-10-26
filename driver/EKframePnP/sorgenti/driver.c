/*++

    driver.c  -  EKframePnP 2.1

    Punti di ingresso, coda seriale delle richieste, chiusura.

--*/

#include "ekframe.h"

VOID NTAPI EkWriteCancel(PDEVICE_OBJECT DeviceObject, PIRP Irp);
VOID EkCompleteWrite(PDEVICE_EXTENSION DevExt, BOOLEAN Success);


NTSTATUS NTAPI
DriverEntry(PDRIVER_OBJECT DriverObject, PUNICODE_STRING RegistryPath)
{
    UNREFERENCED_PARAMETER(RegistryPath);

    DbgPrint("EKframePnP " EK_DRIVER_VERSION_STRING ": caricato\n");

    DriverObject->DriverExtension->AddDevice = EkAddDevice;
    DriverObject->DriverUnload               = EkUnload;

    DriverObject->MajorFunction[IRP_MJ_CREATE]         = EkCreate;
    DriverObject->MajorFunction[IRP_MJ_CLOSE]          = EkClose;
    DriverObject->MajorFunction[IRP_MJ_CLEANUP]        = EkCleanup;
    DriverObject->MajorFunction[IRP_MJ_READ]           = EkRead;
    DriverObject->MajorFunction[IRP_MJ_WRITE]          = EkWrite;
    DriverObject->MajorFunction[IRP_MJ_DEVICE_CONTROL] = EkDeviceControl;
    DriverObject->MajorFunction[IRP_MJ_PNP]            = EkPnp;
    DriverObject->MajorFunction[IRP_MJ_POWER]          = EkPower;
    DriverObject->MajorFunction[IRP_MJ_SYSTEM_CONTROL] = EkSystemControl;

    return STATUS_SUCCESS;
}


NTSTATUS NTAPI
EkAddDevice(PDRIVER_OBJECT DriverObject, PDEVICE_OBJECT Pdo)
{
    NTSTATUS        status;
    PDEVICE_OBJECT  fdo = NULL;
    PDEVICE_EXTENSION d;
    UNICODE_STRING  ntName;
    UNICODE_STRING  dosName;
    ULONG           i;

    RtlInitUnicodeString(&ntName,  EK_NT_DEVICE_NAME);
    RtlInitUnicodeString(&dosName, EK_DOS_DEVICE_NAME);

    //
    // Nome fisso, come l'originale: una sola scheda per sistema.
    //
    status = IoCreateDevice(DriverObject, sizeof(DEVICE_EXTENSION), &ntName,
                            FILE_DEVICE_EKFRAME, FILE_DEVICE_SECURE_OPEN, FALSE, &fdo);
    if (!NT_SUCCESS(status)) {
        DbgPrint("EKframePnP: IoCreateDevice fallita 0x%08X\n", status);
        return status;
    }

    d = (PDEVICE_EXTENSION)fdo->DeviceExtension;
    RtlZeroMemory(d, sizeof(DEVICE_EXTENSION));

    d->Self          = fdo;
    d->Pdo           = Pdo;
    d->ActiveChannel = -1;

    IoInitializeRemoveLock(&d->RemoveLock, EK_POOL_TAG, 0, 0);
    KeInitializeSpinLock(&d->RxLock);
    KeInitializeSpinLock(&d->EventLock);
    KeInitializeSpinLock(&d->QueueLock);
    InitializeListHead(&d->Queue);

    KeInitializeDpc(&d->DpcNetReady, EkDpcNetReady, d);
    KeInitializeDpc(&d->DpcNetLost,  EkDpcNetLost,  d);
    KeInitializeDpc(&d->DpcMessage,  EkDpcMessage,  d);
    KeInitializeDpc(&d->DpcTxDone,   EkDpcTxDone,   d);
    KeInitializeDpc(&d->DpcDma,      EkDpcDma,      d);

    for (i = 0; i < EK_DMA_CHANNELS; i++) {
        d->Channel[i].DevExt    = d;
        d->Channel[i].Index     = i;
        d->Channel[i].TruncDesc = -1;
        KeInitializeSpinLock(&d->Channel[i].Lock);
    }

    //
    // ReadFile/WriteFile usano buffer di sistema, come l'originale
    // (DO_BUFFERED_IO, impostato a 0x11091).
    //
    fdo->Flags |= DO_BUFFERED_IO;

    status = IoCreateSymbolicLink(&dosName, &ntName);
    if (!NT_SUCCESS(status)) {
        DbgPrint("EKframePnP: link simbolico non creato 0x%08X\n", status);
        IoDeleteDevice(fdo);
        return status;
    }
    d->SymbolicLinkCreated = TRUE;

    d->LowerDeviceObject = IoAttachDeviceToDeviceStack(fdo, Pdo);
    if (d->LowerDeviceObject == NULL) {
        IoDeleteSymbolicLink(&dosName);
        IoDeleteDevice(fdo);
        return STATUS_NO_SUCH_DEVICE;
    }

    fdo->Flags |= (d->LowerDeviceObject->Flags & (DO_POWER_PAGABLE | DO_POWER_INRUSH));
    fdo->Flags &= ~DO_DEVICE_INITIALIZING;

    return STATUS_SUCCESS;
}


VOID NTAPI
EkUnload(PDRIVER_OBJECT DriverObject)
{
    UNREFERENCED_PARAMETER(DriverObject);
    DbgPrint("EKframePnP: scaricato\n");
}


//
// ---------------------------------------------------------------------------
//  Completamento: ogni IRP che ha preso il remove lock passa da qui
// ---------------------------------------------------------------------------
//
VOID
EkCompleteIrp(PDEVICE_EXTENSION d, PIRP Irp, NTSTATUS Status, CCHAR Boost)
{
    Irp->IoStatus.Status = Status;
    IoCompleteRequest(Irp, Boost);
    IoReleaseRemoveLock(&d->RemoveLock, Irp);
}


static NTSTATUS
EkFinish(PDEVICE_EXTENSION d, PIRP Irp, NTSTATUS Status)
{
    if (Status != STATUS_PENDING) {
        EkCompleteIrp(d, Irp, Status, IO_NO_INCREMENT);
    }
    return Status;
}


//
// ---------------------------------------------------------------------------
//  Coda seriale
// ---------------------------------------------------------------------------
//
static VOID NTAPI
EkQueueCancel(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    KIRQL old;

    IoReleaseCancelSpinLock(Irp->CancelIrql);

    KeAcquireSpinLock(&d->QueueLock, &old);
    RemoveEntryList(&Irp->Tail.Overlay.ListEntry);      // sicuro anche se gia' tolto
    KeReleaseSpinLock(&d->QueueLock, old);

    Irp->IoStatus.Information = 0;
    EkCompleteIrp(d, Irp, STATUS_CANCELLED, IO_NO_INCREMENT);
}


static PIRP
EkDequeueNext(PDEVICE_EXTENSION d)
{
    KIRQL old;
    PIRP  next = NULL;

    KeAcquireSpinLock(&d->QueueLock, &old);

    while (!IsListEmpty(&d->Queue)) {
        PLIST_ENTRY e   = RemoveHeadList(&d->Queue);
        PIRP        irp = CONTAINING_RECORD(e, IRP, Tail.Overlay.ListEntry);

        if (IoSetCancelRoutine(irp, NULL) != NULL) {
            next = irp;
            break;
        }
        InitializeListHead(e);                          // lo completa la routine di annullamento
    }

    d->CurrentIrp = next;
    KeReleaseSpinLock(&d->QueueLock, old);
    return next;
}


static VOID
EkRunQueue(PDEVICE_EXTENSION d, PIRP Irp)
{
    KIRQL    old;
    NTSTATUS status;

    KeRaiseIrql(DISPATCH_LEVEL, &old);

    while (Irp != NULL) {

        if (Irp->Cancel) {
            Irp->IoStatus.Information = 0;
            status = STATUS_CANCELLED;
        } else if (!d->Started) {
            Irp->IoStatus.Information = 0;
            status = STATUS_DEVICE_NOT_READY;
        } else {
            status = EkProcessQueuedIrp(d, Irp);
        }

        if (status == STATUS_PENDING) {
            break;              // WriteFile in attesa: resta l'IRP corrente
        }

        EkCompleteIrp(d, Irp, status, IO_NO_INCREMENT);
        Irp = EkDequeueNext(d);
    }

    KeLowerIrql(old);
}


NTSTATUS
EkQueueIrp(PDEVICE_EXTENSION d, PIRP Irp)
{
    KIRQL old;

    IoMarkIrpPending(Irp);

    KeAcquireSpinLock(&d->QueueLock, &old);

    if (d->CurrentIrp == NULL) {
        d->CurrentIrp = Irp;
        KeReleaseSpinLock(&d->QueueLock, old);
        EkRunQueue(d, Irp);
        return STATUS_PENDING;
    }

    IoSetCancelRoutine(Irp, EkQueueCancel);
    if (Irp->Cancel && IoSetCancelRoutine(Irp, NULL) != NULL) {
        KeReleaseSpinLock(&d->QueueLock, old);
        Irp->IoStatus.Information = 0;
        EkCompleteIrp(d, Irp, STATUS_CANCELLED, IO_NO_INCREMENT);
        return STATUS_PENDING;
    }
    InsertTailList(&d->Queue, &Irp->Tail.Overlay.ListEntry);
    KeReleaseSpinLock(&d->QueueLock, old);

    return STATUS_PENDING;
}


VOID
EkStartNextIrp(PDEVICE_EXTENSION d)
{
    PIRP next = EkDequeueNext(d);
    if (next != NULL) {
        EkRunQueue(d, next);
    }
}


//
// Conferma di trasmissione ARCNET (DPC, 0x10CE6). L'originale completava con
// STATUS_IO_DEVICE_ERROR quando lo scanner non aveva confermato.
//
VOID
EkCompleteWrite(PDEVICE_EXTENSION d, BOOLEAN Success)
{
    KIRQL old;
    PIRP  irp;

    KeAcquireSpinLock(&d->QueueLock, &old);
    irp = d->WriteIrp;
    if (irp == NULL || IoSetCancelRoutine(irp, NULL) == NULL) {
        KeReleaseSpinLock(&d->QueueLock, old);
        return;
    }
    d->WriteIrp = NULL;
    KeReleaseSpinLock(&d->QueueLock, old);

    if (Success) {
        irp->IoStatus.Information = d->WriteLength;
        EkCompleteIrp(d, irp, STATUS_SUCCESS, 2);
    } else {
        d->TxFailed++;
        irp->IoStatus.Information = 0;
        EkCompleteIrp(d, irp, STATUS_IO_DEVICE_ERROR, 2);
    }

    EkStartNextIrp(d);
}


static BOOLEAN NTAPI
EkClearTxSync(PVOID Context)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)Context;
    d->TxPending = FALSE;
    EkSetModeBit(d, EK_MODE_TX_PENDING, FALSE);
    return TRUE;
}


VOID NTAPI
EkWriteCancel(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    KIRQL   old;
    BOOLEAN own = FALSE;

    IoReleaseCancelSpinLock(Irp->CancelIrql);

    KeAcquireSpinLock(&d->QueueLock, &old);
    if (d->WriteIrp == Irp) {
        d->WriteIrp = NULL;
        own = TRUE;
    }
    KeReleaseSpinLock(&d->QueueLock, old);

    if (own) {
        EkSync(d, EkClearTxSync, d);
        Irp->IoStatus.Information = 0;
        EkCompleteIrp(d, Irp, STATUS_CANCELLED, IO_NO_INCREMENT);
        EkStartNextIrp(d);      // l'originale qui non riavviava la coda e si bloccava
    }
}


VOID
EkFlushRequests(PDEVICE_EXTENSION d, PFILE_OBJECT FileObject, NTSTATUS Status)
{
    LIST_ENTRY  done;
    PLIST_ENTRY e;
    KIRQL       old;
    PIRP        write = NULL;

    InitializeListHead(&done);

    KeAcquireSpinLock(&d->QueueLock, &old);

    e = d->Queue.Flink;
    while (e != &d->Queue) {
        PIRP irp = CONTAINING_RECORD(e, IRP, Tail.Overlay.ListEntry);
        PLIST_ENTRY nextEntry = e->Flink;

        if (FileObject == NULL ||
            IoGetCurrentIrpStackLocation(irp)->FileObject == FileObject) {
            if (IoSetCancelRoutine(irp, NULL) != NULL) {
                RemoveEntryList(e);
                InsertTailList(&done, e);
            }
        }
        e = nextEntry;
    }

    if (d->WriteIrp != NULL &&
        (FileObject == NULL ||
         IoGetCurrentIrpStackLocation(d->WriteIrp)->FileObject == FileObject) &&
        IoSetCancelRoutine(d->WriteIrp, NULL) != NULL) {
        write = d->WriteIrp;
        d->WriteIrp = NULL;
    }

    KeReleaseSpinLock(&d->QueueLock, old);

    while (!IsListEmpty(&done)) {
        PIRP irp = CONTAINING_RECORD(RemoveHeadList(&done), IRP, Tail.Overlay.ListEntry);
        irp->IoStatus.Information = 0;
        EkCompleteIrp(d, irp, Status, IO_NO_INCREMENT);
    }

    if (write != NULL) {
        EkSync(d, EkClearTxSync, d);
        write->IoStatus.Information = 0;
        EkCompleteIrp(d, write, Status, IO_NO_INCREMENT);
        EkStartNextIrp(d);
    }

    EkDmaFlush(d, FileObject, Status);
}


//
// ---------------------------------------------------------------------------
//  Spegnimento logico della scheda: quello che l'originale faceva alla
//  chiusura dell'handle (0x10C26)
// ---------------------------------------------------------------------------
//
static BOOLEAN NTAPI
EkShutdownSync(PVOID Context)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)Context;

    if (d->ActiveChannel != -1) {
        EkDmaDisable(d);
        EkDmaAbort(d);
    }
    d->ActiveChannel = -1;
    EkSetIntMask(d, 0);
    d->NetReady  = FALSE;
    d->TxPending = FALSE;
    return TRUE;
}


VOID
EkShutdownHardware(PDEVICE_EXTENSION d)
{
    if (!d->Started) {
        return;
    }

    EkSync(d, EkShutdownSync, d);
    EkPlxEnableInterrupts(d, FALSE);
    EkArcShutdown(d->ArcBase);

    d->RxHead = 0;
    d->RxTail = 0;

    EkReleaseAllEvents(d);
}


//
// ---------------------------------------------------------------------------
//  Create / Close / Cleanup
// ---------------------------------------------------------------------------
//
NTSTATUS NTAPI
EkCreate(PDEVICE_OBJECT DeviceObject, PIRP Irp)          // 0x109B4
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    NTSTATUS status = IoAcquireRemoveLock(&d->RemoveLock, Irp);

    if (!NT_SUCCESS(status)) {
        Irp->IoStatus.Status = status;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return status;
    }

    InterlockedIncrement(&d->OpenCount);
    Irp->IoStatus.Information = 0;
    EkCompleteIrp(d, Irp, STATUS_SUCCESS, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}


NTSTATUS NTAPI
EkClose(PDEVICE_OBJECT DeviceObject, PIRP Irp)           // 0x109DC
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;

    //
    // La CLOSE arriva anche durante la rimozione: non si rifiuta mai.
    //
    if (InterlockedDecrement(&d->OpenCount) == 0) {
        EkShutdownHardware(d);
    }

    Irp->IoStatus.Status = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);
    return STATUS_SUCCESS;
}


NTSTATUS NTAPI
EkCleanup(PDEVICE_OBJECT DeviceObject, PIRP Irp)         // 0x10AA0
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    PFILE_OBJECT fo = IoGetCurrentIrpStackLocation(Irp)->FileObject;

    //
    // Come l'originale: si annullano le richieste di questo handle e poi si
    // spegne la parte logica della scheda (interrupt, ARCNET, eventi).
    // In piu' il DMA viene fermato PRIMA di sbloccare i buffer.
    //
    EkFlushRequests(d, fo, STATUS_CANCELLED);

    Irp->IoStatus.Status = STATUS_SUCCESS;
    Irp->IoStatus.Information = 0;
    IoCompleteRequest(Irp, IO_NO_INCREMENT);

    EkShutdownHardware(d);
    return STATUS_SUCCESS;
}


//
// ---------------------------------------------------------------------------
//  ReadFile: messaggio ARCNET ricevuto                       (0x11834)
// ---------------------------------------------------------------------------
//
static BOOLEAN NTAPI
EkClearRxPendingSync(PVOID Context)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)Context;
    if (d->RxHead == d->RxTail) {
        EkSetModeBit(d, EK_MODE_RX_PENDING, FALSE);
    }
    return TRUE;
}


NTSTATUS NTAPI
EkRead(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    ULONG    capacity = IoGetCurrentIrpStackLocation(Irp)->Parameters.Read.Length;
    NTSTATUS status = IoAcquireRemoveLock(&d->RemoveLock, Irp);
    KIRQL    old;
    ULONG    tail;
    BOOLEAN  empty = FALSE;

    if (!NT_SUCCESS(status)) {
        Irp->IoStatus.Status = status;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return status;
    }

    Irp->IoStatus.Information = 0;

    if (!d->Started || d->RxRing == NULL) {
        return EkFinish(d, Irp, STATUS_DEVICE_NOT_READY);
    }

    KeAcquireSpinLock(&d->RxLock, &old);

    tail = d->RxTail;
    if (tail == d->RxHead) {
        status = STATUS_DEVICE_DATA_ERROR;              // anello vuoto, come l'originale
    } else {
        PEK_RX_ENTRY e = &d->RxRing[tail];
        ULONG n = e->Length;
        ULONG copy = (n <= capacity) ? n : capacity;

        //
        // L'originale copiava senza guardare la dimensione del buffer di
        // lettura (sovrascrittura di memoria del kernel con buffer piccoli).
        // Qui si copia quello che ci sta e si segnala che il messaggio era
        // piu' lungo.
        //
        if (copy != 0) {
            RtlCopyMemory(Irp->AssociatedIrp.SystemBuffer, e->Data, copy);
        }
        Irp->IoStatus.Information = copy;
        status = (copy < n) ? STATUS_BUFFER_OVERFLOW : STATUS_SUCCESS;

        d->RxTail = (tail + 1) % EK_RX_RING_SIZE;
        empty = (d->RxTail == d->RxHead);
    }

    KeReleaseSpinLock(&d->RxLock, old);

    if (empty) {
        EkSync(d, EkClearRxPendingSync, d);
    }

    Irp->IoStatus.Status = status;
    IoCompleteRequest(Irp, 2);
    IoReleaseRemoveLock(&d->RemoveLock, Irp);
    return status;
}


NTSTATUS NTAPI
EkWrite(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    NTSTATUS status = IoAcquireRemoveLock(&d->RemoveLock, Irp);

    if (!NT_SUCCESS(status)) {
        Irp->IoStatus.Status = status;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return status;
    }

    Irp->IoStatus.Information = 0;
    if (!d->Started) {
        return EkFinish(d, Irp, STATUS_DEVICE_NOT_READY);
    }
    return EkQueueIrp(d, Irp);
}


//
// ---------------------------------------------------------------------------
//  IOCTL
// ---------------------------------------------------------------------------
//
static NTSTATUS
EkDiag(PDEVICE_EXTENSION d, PIRP Irp)
{
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(Irp);
    PEK_DIAG g;
    ULONG i;

    if (sp->Parameters.DeviceIoControl.OutputBufferLength < sizeof(EK_DIAG) ||
        Irp->AssociatedIrp.SystemBuffer == NULL) {
        return STATUS_BUFFER_TOO_SMALL;
    }
    g = (PEK_DIAG)Irp->AssociatedIrp.SystemBuffer;
    RtlZeroMemory(g, sizeof(EK_DIAG));

    g->Version            = EK_DIAG_VERSION;
    g->PlxPhysical        = d->PlxPhys.LowPart;
    g->PlxLength          = d->PlxRegLength;
    g->LocalPhysical      = d->LocalPhys.LowPart;
    g->LocalLength        = d->LocalRegLength;
    g->Vector             = d->Vector;
    g->Irql               = d->Irql;
    g->InterruptConnected = (d->InterruptObject != NULL);
    g->IntMask            = d->IntMask;
    g->ActiveChannel      = d->ActiveChannel;
    g->NetReady           = d->NetReady;
    g->NodeId             = d->NodeId;
    g->DestId             = d->DestId;
    g->LastArcStatus      = d->LastArcStatus;
    g->ArcInitResult      = d->ArcInitResult;
    g->RxHead             = d->RxHead;
    g->RxTail             = d->RxTail;
    g->OpenCount          = (ULONG)d->OpenCount;
    g->IsrCount           = d->IsrCount;
    g->IsrClaimed         = d->IsrClaimed;
    g->DmaDoneCount       = d->DmaDoneCount;
    g->RxCount            = d->RxCount;
    g->RxDropped          = d->RxDropped;
    g->TxCount            = d->TxCount;
    g->TxFailed           = d->TxFailed;
    for (i = 0; i < EK_DMA_CHANNELS; i++) {
        g->MapRegisters[i]       = d->Channel[i].MapRegisters;
        g->ChannelArmed[i]       = (d->Channel[i].Irp != NULL);
        g->ChannelBytes[i]       = d->Channel[i].ByteCount;
        g->ChannelDescriptors[i] = d->Channel[i].DescCount;
    }
    if (d->Started) {
        g->PlxIntcsr      = EkPlxRead(d, PLX_INTCSR);
        g->PlxDmacsr      = EkPlxRead(d, PLX_DMACSR0);
        g->LocalIntStatus = EkLocalRead(d, EKL_INT_STATUS);
        g->LocalIntEnable = EkLocalRead(d, EKL_INT_ENABLE);
        g->LocalMode      = EkLocalRead(d, EKL_MODE);
    }

    Irp->IoStatus.Information = sizeof(EK_DIAG);
    return STATUS_SUCCESS;
}


NTSTATUS NTAPI
EkDeviceControl(PDEVICE_OBJECT DeviceObject, PIRP Irp)   // 0x11AA2
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    ULONG    code = IoGetCurrentIrpStackLocation(Irp)->Parameters.DeviceIoControl.IoControlCode;
    NTSTATUS status = IoAcquireRemoveLock(&d->RemoveLock, Irp);

    if (!NT_SUCCESS(status)) {
        Irp->IoStatus.Status = status;
        Irp->IoStatus.Information = 0;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return status;
    }

    Irp->IoStatus.Information = 0;

    if (code == IOCTL_EK_DIAG) {
        return EkFinish(d, Irp, EkDiag(d, Irp));
    }

    if (!d->Started) {
        return EkFinish(d, Irp, STATUS_DEVICE_NOT_READY);
    }

    switch (code) {

    case IOCTL_EK_REGISTER_EVENT:
        return EkFinish(d, Irp, EkRegisterEvent(d, Irp));

    case IOCTL_EK_RELEASE_EVENT:
        return EkFinish(d, Irp, EkReleaseEvent(d, Irp));

    case IOCTL_EK_DMA_ARM_0:
        return EkFinish(d, Irp, EkDmaArm(d, Irp, 0));

    case IOCTL_EK_DMA_ARM_1:
        return EkFinish(d, Irp, EkDmaArm(d, Irp, 1));

    default:
        return EkQueueIrp(d, Irp);
    }
}


//
// ---------------------------------------------------------------------------
//  Power / WMI
// ---------------------------------------------------------------------------
//
NTSTATUS NTAPI
EkPower(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION  d  = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(Irp);
    NTSTATUS status;

    //
    // Il driver non sa riportare la scheda in stato dopo una sospensione.
    // Mentre un'applicazione ha il dispositivo aperto si rifiuta la
    // sospensione (S1-S4); lo spegnimento (S5) non viene mai bloccato.
    //
    if (sp->MinorFunction == IRP_MN_QUERY_POWER &&
        sp->Parameters.Power.Type == SystemPowerState &&
        sp->Parameters.Power.State.SystemState >= PowerSystemSleeping1 &&
        sp->Parameters.Power.State.SystemState <= PowerSystemHibernate &&
        d->OpenCount > 0) {

        DbgPrint("EKframePnP: sospensione rifiutata, dispositivo in uso\n");
        PoStartNextPowerIrp(Irp);
        Irp->IoStatus.Status = STATUS_DEVICE_BUSY;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return STATUS_DEVICE_BUSY;
    }

    if (sp->MinorFunction == IRP_MN_SET_POWER &&
        sp->Parameters.Power.Type == SystemPowerState &&
        sp->Parameters.Power.State.SystemState > PowerSystemWorking) {
        d->PlxInitialized = FALSE;      // al risveglio il bridge va riprogrammato
    }

    PoStartNextPowerIrp(Irp);
    IoSkipCurrentIrpStackLocation(Irp);
    status = PoCallDriver(d->LowerDeviceObject, Irp);
    return status;
}


NTSTATUS NTAPI
EkSystemControl(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    IoSkipCurrentIrpStackLocation(Irp);
    return IoCallDriver(d->LowerDeviceObject, Irp);
}
