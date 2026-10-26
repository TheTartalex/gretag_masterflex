/*++

    pnp.c  -  EKframePnP 2.1

    Plug and Play. E' il punto in cui questo driver differisce davvero
    dall'originale: EKframe.sys scandiva il bus PCI da se' (HalGetBusData) e
    si assegnava le risorse (HalAssignSlotResources); qui le risorse arrivano
    dal PnP manager in IRP_MN_START_DEVICE.

    Risorse attese, confermate sulla macchina reale:
        memoria 256 B  (0xDE000000)  -> registri PLX 9080
        memoria 2 MB   (0xDD800000)  -> finestra locale
        I/O     256 B  (0xA800)      -> ignorato, come l'originale
        IRQ 9, condiviso, a livello

--*/

#include "ekframe.h"
#include <initguid.h>
#include <wdmguid.h>


static NTSTATUS NTAPI
EkSignalCompletion(PDEVICE_OBJECT DeviceObject, PIRP Irp, PVOID Context)
{
    UNREFERENCED_PARAMETER(DeviceObject);
    UNREFERENCED_PARAMETER(Irp);
    KeSetEvent((PKEVENT)Context, IO_NO_INCREMENT, FALSE);
    return STATUS_MORE_PROCESSING_REQUIRED;
}


static NTSTATUS
EkForwardAndWait(PDEVICE_EXTENSION d, PIRP Irp)
{
    KEVENT   event;
    NTSTATUS status;

    KeInitializeEvent(&event, NotificationEvent, FALSE);
    IoCopyCurrentIrpStackLocationToNext(Irp);
    IoSetCompletionRoutine(Irp, EkSignalCompletion, &event, TRUE, TRUE, TRUE);

    status = IoCallDriver(d->LowerDeviceObject, Irp);
    if (status == STATUS_PENDING) {
        KeWaitForSingleObject(&event, Executive, KernelMode, FALSE, NULL);
    }
    return Irp->IoStatus.Status;
}


static VOID
EkReadPciConfig(PDEVICE_EXTENSION d)
{
    KEVENT              event;
    PIRP                irp;
    PIO_STACK_LOCATION  sp;
    BUS_INTERFACE_STANDARD bus;
    NTSTATUS            status;

    RtlZeroMemory(&bus, sizeof(bus));
    KeInitializeEvent(&event, NotificationEvent, FALSE);

    irp = IoAllocateIrp(d->LowerDeviceObject->StackSize, FALSE);
    if (irp == NULL) {
        return;
    }

    irp->IoStatus.Status = STATUS_NOT_SUPPORTED;
    sp = IoGetNextIrpStackLocation(irp);
    sp->MajorFunction = IRP_MJ_PNP;
    sp->MinorFunction = IRP_MN_QUERY_INTERFACE;
    sp->Parameters.QueryInterface.InterfaceType = &GUID_BUS_INTERFACE_STANDARD;
    sp->Parameters.QueryInterface.Size          = sizeof(BUS_INTERFACE_STANDARD);
    sp->Parameters.QueryInterface.Version       = 1;
    sp->Parameters.QueryInterface.Interface     = (PINTERFACE)&bus;
    sp->Parameters.QueryInterface.InterfaceSpecificData = NULL;
    IoSetCompletionRoutine(irp, EkSignalCompletion, &event, TRUE, TRUE, TRUE);

    status = IoCallDriver(d->LowerDeviceObject, irp);
    if (status == STATUS_PENDING) {
        KeWaitForSingleObject(&event, Executive, KernelMode, FALSE, NULL);
    }
    status = irp->IoStatus.Status;
    IoFreeIrp(irp);

    if (!NT_SUCCESS(status)) {
        DbgPrint("EKframePnP: interfaccia bus non disponibile 0x%08X\n", status);
        return;
    }

    if (bus.GetBusData != NULL) {
        d->PciConfigValid =
            (bus.GetBusData(bus.Context, PCI_WHICHSPACE_CONFIG, d->PciConfig, 0,
                            sizeof(d->PciConfig)) == sizeof(d->PciConfig));
    }
    if (bus.InterfaceDereference != NULL) {
        bus.InterfaceDereference(bus.Context);
    }
}


NTSTATUS
EkStartDevice(PDEVICE_EXTENSION d, PCM_RESOURCE_LIST Translated)
{
    PCM_PARTIAL_RESOURCE_LIST       list;
    PCM_PARTIAL_RESOURCE_DESCRIPTOR p;
    DEVICE_DESCRIPTION              dd;
    BOOLEAN  haveInterrupt = FALSE;
    NTSTATUS status;
    ULONG    i;

    if (Translated == NULL || Translated->Count == 0) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }
    list = &Translated->List[0].PartialResourceList;

    for (i = 0; i < list->Count; i++) {

        p = &list->PartialDescriptors[i];

        switch (p->Type) {

        case CmResourceTypeMemory:
            //
            // Stesso criterio dell'originale (0x1112A): oltre 0x100 byte e'
            // la finestra locale, altrimenti i registri del PLX.
            //
            if (p->u.Memory.Length > 0x100) {
                if (d->LocalRegBase == NULL) {
                    d->LocalPhys      = p->u.Memory.Start;
                    d->LocalRegLength = p->u.Memory.Length;
                    d->LocalRegBase   = (PUCHAR)MmMapIoSpace(p->u.Memory.Start,
                                                             p->u.Memory.Length, MmNonCached);
                }
            } else {
                if (d->PlxRegBase == NULL) {
                    d->PlxPhys      = p->u.Memory.Start;
                    d->PlxRegLength = p->u.Memory.Length;
                    d->PlxRegBase   = (PUCHAR)MmMapIoSpace(p->u.Memory.Start,
                                                           p->u.Memory.Length, MmNonCached);
                }
            }
            break;

        case CmResourceTypeInterrupt:
            d->Vector        = p->u.Interrupt.Vector;
            d->Irql          = (KIRQL)p->u.Interrupt.Level;
            d->Affinity      = p->u.Interrupt.Affinity;
            d->InterruptMode = (p->Flags & CM_RESOURCE_INTERRUPT_LATCHED) ? Latched : LevelSensitive;
            d->ShareVector   = (p->ShareDisposition == CmResourceShareShared);
            haveInterrupt    = TRUE;
            break;

        default:
            break;
        }
    }

    DbgPrint("EKframePnP: PLX %08X/%X  locale %08X/%X  IRQ vettore %u livello %u\n",
             d->PlxPhys.LowPart, d->PlxRegLength, d->LocalPhys.LowPart, d->LocalRegLength,
             d->Vector, d->Irql);

    if (d->PlxRegBase == NULL || d->LocalRegBase == NULL) {
        status = STATUS_DEVICE_CONFIGURATION_ERROR;
        goto fail;
    }
    if (d->LocalRegLength < EKL_MIN_WINDOW) {
        DbgPrint("EKframePnP: finestra locale troppo piccola per i descrittori\n");
        status = STATUS_DEVICE_CONFIGURATION_ERROR;
        goto fail;
    }
    if (!haveInterrupt) {
        status = STATUS_DEVICE_CONFIGURATION_ERROR;
        goto fail;
    }

    d->ArcBase = d->LocalRegBase + EKL_ARCNET;

    EkReadPciConfig(d);

    //
    // Anello dei messaggi ARCNET: 64 voci da 512 byte, come l'originale
    // (0x8000 byte con tag 'Ddk ').
    //
    d->RxRing = (PEK_RX_ENTRY)ExAllocatePoolWithTag(NonPagedPool,
                                                    sizeof(EK_RX_ENTRY) * EK_RX_RING_SIZE,
                                                    EK_POOL_TAG);
    if (d->RxRing == NULL) {
        status = STATUS_INSUFFICIENT_RESOURCES;
        goto fail;
    }
    RtlZeroMemory(d->RxRing, sizeof(EK_RX_ENTRY) * EK_RX_RING_SIZE);
    d->RxHead = 0;
    d->RxTail = 0;

    //
    // Un adattatore DMA per canale, con la stessa descrizione
    // dell'originale (0x1130B): bus master a 32 bit, scatter/gather,
    // lunghezza massima 0x3105560.
    //
    RtlZeroMemory(&dd, sizeof(dd));
    dd.Version           = DEVICE_DESCRIPTION_VERSION;
    dd.Master            = TRUE;
    dd.ScatterGather     = TRUE;
    dd.Dma32BitAddresses = TRUE;
    dd.InterfaceType     = PCIBus;
    dd.DmaWidth          = Width32Bits;
    dd.MaximumLength     = EK_DMA_MAX_TRANSFER;

    for (i = 0; i < EK_DMA_CHANNELS; i++) {

        PEK_CHANNEL ch = &d->Channel[i];

        ch->DescVa    = d->LocalRegBase + EKL_DESC_BASE + i * EKL_DESC_STRIDE;
        ch->DescLocal = EK_LOCAL_BUS_BASE + EKL_DESC_BASE + i * EKL_DESC_STRIDE;
        ch->TruncDesc = -1;

        ch->DescSizes = (PULONG)ExAllocatePoolWithTag(NonPagedPool,
                                                      EK_DMA_MAX_DESCRIPTORS * sizeof(ULONG),
                                                      EK_POOL_TAG);
        if (ch->DescSizes == NULL) {
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto fail;
        }

        ch->Adapter = IoGetDmaAdapter(d->Pdo, &dd, &ch->MapRegisters);
        if (ch->Adapter == NULL) {
            DbgPrint("EKframePnP: adattatore DMA %u non ottenuto\n", i);
            status = STATUS_INSUFFICIENT_RESOURCES;
            goto fail;
        }
        DbgPrint("EKframePnP: canale %u, map register %u\n", i, ch->MapRegisters);
    }

    //
    // Scheda in stato "chiuso" prima di agganciare l'interrupt: nessuna
    // sorgente abilitata, cosi' un IRQ condiviso non puo' andare in tempesta.
    //
    d->ActiveChannel  = -1;
    d->PlxInitialized = FALSE;
    EkSetIntMask(d, 0);
    EkPlxEnableInterrupts(d, FALSE);
    EkDmaDisable(d);

    status = IoConnectInterrupt(&d->InterruptObject, EkIsr, d, NULL,
                                d->Vector, d->Irql, d->Irql, d->InterruptMode,
                                d->ShareVector, d->Affinity, FALSE);
    if (!NT_SUCCESS(status)) {
        DbgPrint("EKframePnP: IoConnectInterrupt fallita 0x%08X\n", status);
        d->InterruptObject = NULL;
        goto fail;
    }

    d->Started = TRUE;
    DbgPrint("EKframePnP: scheda avviata\n");
    return STATUS_SUCCESS;

fail:
    EkStopDevice(d);
    return status;
}


VOID
EkStopDevice(PDEVICE_EXTENSION d)
{
    ULONG i;

    d->Started = FALSE;

    if (d->InterruptObject != NULL) {
        IoDisconnectInterrupt(d->InterruptObject);
        d->InterruptObject = NULL;
    }

    //
    // Una DPC accodata dall'ultimo interrupt potrebbe girare ancora: va
    // aspettata prima di smappare le finestre che usa.
    //
    KeFlushQueuedDpcs();

    for (i = 0; i < EK_DMA_CHANNELS; i++) {
        PEK_CHANNEL ch = &d->Channel[i];
        if (ch->Adapter != NULL) {
            ch->Adapter->DmaOperations->PutDmaAdapter(ch->Adapter);
            ch->Adapter = NULL;
        }
        if (ch->DescSizes != NULL) {
            ExFreePoolWithTag(ch->DescSizes, EK_POOL_TAG);
            ch->DescSizes = NULL;
        }
        ch->DescVa = NULL;
    }

    if (d->RxRing != NULL) {
        ExFreePoolWithTag(d->RxRing, EK_POOL_TAG);
        d->RxRing = NULL;
    }

    if (d->PlxRegBase != NULL) {
        MmUnmapIoSpace(d->PlxRegBase, d->PlxRegLength);
        d->PlxRegBase = NULL;
    }
    if (d->LocalRegBase != NULL) {
        MmUnmapIoSpace(d->LocalRegBase, d->LocalRegLength);
        d->LocalRegBase = NULL;
    }
    d->ArcBase = NULL;
}


static VOID
EkHaltDevice(PDEVICE_EXTENSION d, BOOLEAN HardwarePresent, NTSTATUS FlushStatus)
{
    //
    // Prima si fermano DMA e interrupt (se la scheda c'e' ancora), poi si
    // completano le richieste, poi si liberano le risorse.
    //
    if (d->Started && HardwarePresent) {
        EkShutdownHardware(d);
    }
    EkFlushRequests(d, NULL, FlushStatus);
    EkStopDevice(d);
}


NTSTATUS NTAPI
EkPnp(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION  d  = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    PIO_STACK_LOCATION sp = IoGetCurrentIrpStackLocation(Irp);
    NTSTATUS           status;
    UNICODE_STRING     dosName;

    status = IoAcquireRemoveLock(&d->RemoveLock, Irp);
    if (!NT_SUCCESS(status)) {
        Irp->IoStatus.Status = status;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        return status;
    }

    switch (sp->MinorFunction) {

    case IRP_MN_START_DEVICE:
        status = EkForwardAndWait(d, Irp);
        if (NT_SUCCESS(status)) {
            status = EkStartDevice(d, sp->Parameters.StartDevice.AllocatedResourcesTranslated);
        }
        Irp->IoStatus.Status = status;
        IoCompleteRequest(Irp, IO_NO_INCREMENT);
        IoReleaseRemoveLock(&d->RemoveLock, Irp);
        return status;

    case IRP_MN_QUERY_STOP_DEVICE:
    case IRP_MN_QUERY_REMOVE_DEVICE:
        if (d->OpenCount > 0) {
            //
            // Un'applicazione ha la scheda aperta: niente rimozione a caldo.
            //
            Irp->IoStatus.Status = STATUS_DEVICE_BUSY;
            IoCompleteRequest(Irp, IO_NO_INCREMENT);
            IoReleaseRemoveLock(&d->RemoveLock, Irp);
            return STATUS_DEVICE_BUSY;
        }
        Irp->IoStatus.Status = STATUS_SUCCESS;
        break;

    case IRP_MN_CANCEL_STOP_DEVICE:
    case IRP_MN_CANCEL_REMOVE_DEVICE:
        Irp->IoStatus.Status = STATUS_SUCCESS;
        break;

    case IRP_MN_STOP_DEVICE:
        EkHaltDevice(d, TRUE, STATUS_DEVICE_NOT_READY);
        Irp->IoStatus.Status = STATUS_SUCCESS;
        break;

    case IRP_MN_SURPRISE_REMOVAL:
        d->Started = FALSE;
        if (d->InterruptObject != NULL) {
            IoDisconnectInterrupt(d->InterruptObject);
            d->InterruptObject = NULL;
        }
        EkFlushRequests(d, NULL, STATUS_DELETE_PENDING);
        EkStopDevice(d);
        Irp->IoStatus.Status = STATUS_SUCCESS;
        break;

    case IRP_MN_REMOVE_DEVICE:
        EkHaltDevice(d, TRUE, STATUS_DELETE_PENDING);
        EkReleaseAllEvents(d);

        IoReleaseRemoveLockAndWait(&d->RemoveLock, Irp);

        if (d->SymbolicLinkCreated) {
            RtlInitUnicodeString(&dosName, EK_DOS_DEVICE_NAME);
            IoDeleteSymbolicLink(&dosName);
            d->SymbolicLinkCreated = FALSE;
        }

        Irp->IoStatus.Status = STATUS_SUCCESS;
        IoSkipCurrentIrpStackLocation(Irp);
        status = IoCallDriver(d->LowerDeviceObject, Irp);

        IoDetachDevice(d->LowerDeviceObject);
        IoDeleteDevice(DeviceObject);
        return status;

    default:
        break;
    }

    IoSkipCurrentIrpStackLocation(Irp);
    status = IoCallDriver(d->LowerDeviceObject, Irp);
    IoReleaseRemoveLock(&d->RemoveLock, Irp);
    return status;
}
