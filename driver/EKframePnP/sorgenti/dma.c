/*++

    dma.c  -  EKframePnP 2.1

    Acquisizione immagine: due canali logici (0 e 1) che usano a turno il
    canale DMA 0 del PLX 9080. Protocollo ricostruito dall'originale e
    confermato in DMLCmdTransfer.exe:

      1. IOCTL_EK_DMA_ARM_0/1 (0x9C40A421 / 0x9C40A425)
         il buffer dell'utente arriva come MDL; il driver lo blocca, ne
         costruisce la catena scatter/gather nella memoria della scheda e
         lascia l'IRP pendente. Il buffer resta agganciato finche' non viene
         rilasciato: si possono fare piu' trasferimenti nello stesso buffer.

      2. IOCTL_EK_DMA_START {canale, byte} (0x9C40A440)
         tronca la catena alla lunghezza chiesta, reimposta il FIFO e avvia.
         Fine trasferimento -> interrupt -> evento 3.

      3. IOCTL_EK_DMA_STOP {canale} (0x9C40A444)
         ferma il trasferimento in corso (errore se non ce n'e' uno).

      4. IOCTL_EK_DMA_RELEASE_0/1 (0x9C40A430 / 0x9C40A434)
         sgancia il buffer e completa l'IRP del punto 1.

    Formato dei descrittori PLX 9080, scritti nella finestra locale (16 byte):
        +0x00  indirizzo fisico PCI
        +0x04  indirizzo locale: sempre 0xC0000000, il FIFO dei pixel
        +0x08  byte
        +0x0C  indirizzo locale del descrittore successivo | flag
               (bit0 = 0 descrittori nello spazio locale, bit1 fine catena,
                bit2 interrupt a fine conteggio, bit3 direzione locale->PCI)

    Differenza dall'originale: qui si usa GetScatterGatherList invece di
    IoAllocateAdapterChannel + IoMapTransfer, e il buffer viene sempre
    rilasciato correttamente (l'originale perdeva i map register quando
    l'IRP veniva annullato, e poteva lasciare il DMA attivo su pagine gia'
    sbloccate alla chiusura dell'handle).

--*/

#include "ekframe.h"

typedef struct _EK_START_CONTEXT {
    PDEVICE_EXTENSION   DevExt;
    PEK_CHANNEL         Channel;
} EK_START_CONTEXT, *PEK_START_CONTEXT;


static VOID
DescWrite(PUCHAR Desc, ULONG Offset, ULONG Value)
{
    WRITE_REGISTER_ULONG((PULONG)(Desc + Offset), Value);
}


static ULONG
DescRead(PUCHAR Desc, ULONG Offset)
{
    return READ_REGISTER_ULONG((PULONG)(Desc + Offset));
}


static BOOLEAN
EkBuildChain(PEK_CHANNEL Ch, PSCATTER_GATHER_LIST Sg)  // AdapterControl 0x1236E
{
    ULONG count = 0;
    ULONG i;

    for (i = 0; i < Sg->NumberOfElements; i++) {

        ULONG address = Sg->Elements[i].Address.LowPart;
        ULONG remain  = Sg->Elements[i].Length;

        if (Sg->Elements[i].Address.HighPart != 0) {
            return FALSE;                       // la scheda indirizza solo 32 bit
        }

        while (remain != 0) {

            ULONG n = (remain > EK_DMA_MAX_SEGMENT) ? EK_DMA_MAX_SEGMENT : remain;
            PUCHAR desc;

            if (count >= EK_DMA_MAX_DESCRIPTORS) {
                return FALSE;
            }

            desc = Ch->DescVa + count * 16;
            DescWrite(desc, 0x4, EK_FIFO_LOCAL_ADDRESS);
            DescWrite(desc, 0x0, address);
            DescWrite(desc, 0x8, n);
            DescWrite(desc, 0xC, (Ch->DescLocal + (count + 1) * 16) | PLX_DESC_NEXT_DIR_TO_PCI);

            Ch->DescSizes[count] = n;
            count++;
            address += n;
            remain  -= n;
        }
    }

    if (count == 0) {
        return FALSE;
    }

    DescWrite(Ch->DescVa + (count - 1) * 16, 0xC,
              (Ch->DescLocal + count * 16) | PLX_DESC_NEXT_LAST);

    Ch->DescCount   = count;
    Ch->TruncDesc   = -1;
    Ch->TruncLength = Ch->ByteCount;
    return TRUE;
}


static VOID NTAPI
EkSgCallback(PDEVICE_OBJECT DeviceObject, PIRP Reserved, PSCATTER_GATHER_LIST Sg, PVOID Context)
{
    PEK_CHANNEL ch = (PEK_CHANNEL)Context;
    PIRP        cancelIrp = NULL;

    UNREFERENCED_PARAMETER(DeviceObject);
    UNREFERENCED_PARAMETER(Reserved);

    KeAcquireSpinLockAtDpcLevel(&ch->Lock);

    ch->SgList = Sg;
    if (!EkBuildChain(ch, Sg)) {
        ch->SgError = TRUE;
    }
    ch->Armed = TRUE;

    if (ch->CancelRequested) {
        cancelIrp      = ch->Irp;
        ch->Irp        = NULL;
        ch->SgList     = NULL;
        ch->Armed      = FALSE;
    }

    KeReleaseSpinLockFromDpcLevel(&ch->Lock);

    if (cancelIrp != NULL) {
        ch->Adapter->DmaOperations->PutScatterGatherList(ch->Adapter, Sg, FALSE);
        cancelIrp->IoStatus.Information = 0;
        EkCompleteIrp(ch->DevExt, cancelIrp, STATUS_CANCELLED, IO_NO_INCREMENT);
    }
}


static BOOLEAN NTAPI
EkDmaStopSync(PVOID Context)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)Context;
    EkDmaDisable(d);
    EkDmaAbort(d);
    d->ActiveChannel = -1;
    return TRUE;
}


typedef struct _EK_STOP_IF_CONTEXT {
    PDEVICE_EXTENSION   DevExt;
    LONG                Channel;
} EK_STOP_IF_CONTEXT;

static BOOLEAN NTAPI
EkDmaStopIfActiveSync(PVOID Context)
{
    EK_STOP_IF_CONTEXT *c = (EK_STOP_IF_CONTEXT *)Context;
    if (c->DevExt->ActiveChannel == c->Channel) {
        EkDmaDisable(c->DevExt);
        EkDmaAbort(c->DevExt);
        c->DevExt->ActiveChannel = -1;
    }
    return TRUE;
}

static VOID
EkDmaStopIfActive(PDEVICE_EXTENSION d, ULONG Channel)
{
    EK_STOP_IF_CONTEXT c;
    c.DevExt  = d;
    c.Channel = (LONG)Channel;
    EkSync(d, EkDmaStopIfActiveSync, &c);
}


static VOID
EkReleaseChannelIrp(PEK_CHANNEL Ch, PIRP Irp, PSCATTER_GATHER_LIST Sg, NTSTATUS Status)
{
    KIRQL old;

    EkDmaStopIfActive(Ch->DevExt, Ch->Index);

    if (Sg != NULL) {
        KeRaiseIrql(DISPATCH_LEVEL, &old);
        Ch->Adapter->DmaOperations->PutScatterGatherList(Ch->Adapter, Sg, FALSE);
        KeLowerIrql(old);
    }

    Irp->IoStatus.Information = 0;
    EkCompleteIrp(Ch->DevExt, Irp, Status, IO_NO_INCREMENT);
}


static VOID NTAPI
EkDmaCancel(PDEVICE_OBJECT DeviceObject, PIRP Irp)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)DeviceObject->DeviceExtension;
    ULONG i;

    IoReleaseCancelSpinLock(Irp->CancelIrql);

    for (i = 0; i < EK_DMA_CHANNELS; i++) {

        PEK_CHANNEL ch = &d->Channel[i];
        PSCATTER_GATHER_LIST sg;
        KIRQL old;

        KeAcquireSpinLock(&ch->Lock, &old);

        if (ch->Irp != Irp) {
            KeReleaseSpinLock(&ch->Lock, old);
            continue;
        }

        if (!ch->Armed) {
            //
            // La catena non e' ancora stata costruita: completera' il
            // callback di GetScatterGatherList.
            //
            ch->CancelRequested = TRUE;
            KeReleaseSpinLock(&ch->Lock, old);
            return;
        }

        sg = ch->SgList;
        ch->SgList = NULL;
        ch->Irp    = NULL;
        ch->Armed  = FALSE;
        KeReleaseSpinLock(&ch->Lock, old);

        EkReleaseChannelIrp(ch, Irp, sg, STATUS_CANCELLED);
        return;
    }
}


NTSTATUS
EkDmaArm(PDEVICE_EXTENSION d, PIRP Irp, ULONG Channel)     // 0x12028
{
    PEK_CHANNEL ch = &d->Channel[Channel];
    PMDL        mdl = Irp->MdlAddress;
    PVOID       va;
    ULONG       length;
    ULONG       pages;
    KIRQL       old;
    NTSTATUS    status;
    PSCATTER_GATHER_LIST sg = NULL;

    Irp->IoStatus.Information = 0;

    if (mdl == NULL || ch->Adapter == NULL) {
        return STATUS_INVALID_PARAMETER;
    }

    va     = MmGetMdlVirtualAddress(mdl);
    length = MmGetMdlByteCount(mdl);

    if (length == 0 || length > EK_DMA_MAX_TRANSFER) {
        return STATUS_INVALID_PARAMETER;
    }

    pages = ADDRESS_AND_SIZE_TO_SPAN_PAGES(va, length);
    if (pages > ch->MapRegisters) {
        return STATUS_INSUFFICIENT_RESOURCES;
    }

    KeAcquireSpinLock(&ch->Lock, &old);
    if (ch->Irp != NULL) {
        KeReleaseSpinLock(&ch->Lock, old);
        return STATUS_DEVICE_BUSY;
    }
    ch->Irp             = Irp;
    ch->Armed           = FALSE;
    ch->CancelRequested = FALSE;
    ch->SgError         = FALSE;
    ch->SgList          = NULL;
    ch->ByteCount       = length;
    ch->DescCount       = 0;
    ch->TruncDesc       = -1;
    ch->TruncLength     = length;
    KeReleaseSpinLock(&ch->Lock, old);

    IoMarkIrpPending(Irp);

    KeRaiseIrql(DISPATCH_LEVEL, &old);
    status = ch->Adapter->DmaOperations->GetScatterGatherList(
                    ch->Adapter, d->Self, mdl, va, length,
                    EkSgCallback, ch, FALSE /* dalla scheda verso la memoria */);
    KeLowerIrql(old);

    if (!NT_SUCCESS(status)) {
        KeAcquireSpinLock(&ch->Lock, &old);
        ch->Irp = NULL;
        KeReleaseSpinLock(&ch->Lock, old);
        EkCompleteIrp(d, Irp, status, IO_NO_INCREMENT);
        return STATUS_PENDING;
    }

    KeAcquireSpinLock(&ch->Lock, &old);

    if (ch->Armed && ch->SgError) {
        //
        // Catena non costruibile (non dovrebbe mai succedere: entro il limite
        // di 49 MB i descrittori necessari sono al massimo circa 12.600 su
        // 16.384 disponibili, e Windows 7 a 32 bit non usa memoria sopra
        // i 4 GB).
        //
        sg = ch->SgList;
        ch->SgList = NULL;
        ch->Irp    = NULL;
        ch->Armed  = FALSE;
        KeReleaseSpinLock(&ch->Lock, old);
        EkReleaseChannelIrp(ch, Irp, sg, STATUS_INSUFFICIENT_RESOURCES);
        return STATUS_PENDING;
    }

    IoSetCancelRoutine(Irp, EkDmaCancel);

    if (Irp->Cancel && IoSetCancelRoutine(Irp, NULL) != NULL) {
        if (ch->Armed) {
            sg = ch->SgList;
            ch->SgList = NULL;
            ch->Irp    = NULL;
            ch->Armed  = FALSE;
            KeReleaseSpinLock(&ch->Lock, old);
            EkReleaseChannelIrp(ch, Irp, sg, STATUS_CANCELLED);
        } else {
            ch->CancelRequested = TRUE;
            KeReleaseSpinLock(&ch->Lock, old);
        }
        return STATUS_PENDING;
    }

    KeReleaseSpinLock(&ch->Lock, old);
    return STATUS_PENDING;
}


static VOID
EkTruncate(PEK_CHANNEL Ch, ULONG Length)               // 0x1246E
/*++
    Fa trasferire esattamente Length byte: ripristina il descrittore toccato
    al giro precedente e tronca la catena nel descrittore in cui si
    raggiunge la lunghezza. L'originale teneva una cache di 32 lunghezze e
    rileggeva le dimensioni dalla scheda; qui le dimensioni sono tenute in
    memoria host, quindi basta un passaggio.
--*/
{
    ULONG  i;
    ULONG  acc = 0;
    ULONG  v;
    PUCHAR desc;

    if (Length == Ch->TruncLength) {
        return;
    }

    if (Ch->TruncDesc >= 0) {
        desc = Ch->DescVa + (ULONG)Ch->TruncDesc * 16;
        DescWrite(desc, 0x8, Ch->DescSizes[Ch->TruncDesc]);
        if ((ULONG)Ch->TruncDesc != Ch->DescCount - 1) {
            //
            // Non era l'ultimo: torna a puntare al successivo. L'originale
            // applicava questa correzione anche all'ultimo descrittore,
            // togliendogli il fine catena; qui l'ultimo resta terminato.
            //
            v = DescRead(desc, 0xC);
            DescWrite(desc, 0xC, (v & ~0x06UL) | PLX_DESC_NEXT_DIR_TO_PCI);
        }
        Ch->TruncDesc = -1;
    }
    Ch->TruncLength = Ch->ByteCount;

    if (Length == Ch->ByteCount) {
        return;
    }

    for (i = 0; i < Ch->DescCount; i++) {
        if (acc + Ch->DescSizes[i] >= Length) {
            break;
        }
        acc += Ch->DescSizes[i];
    }
    if (i >= Ch->DescCount) {
        return;                                 // impossibile: Length < ByteCount
    }

    desc = Ch->DescVa + i * 16;
    DescWrite(desc, 0x8, Length - acc);
    v = DescRead(desc, 0xC);
    DescWrite(desc, 0xC, v | PLX_DESC_NEXT_LAST);

    Ch->TruncDesc   = (LONG)i;
    Ch->TruncLength = Length;
}


static BOOLEAN NTAPI
EkDmaStartSync(PVOID Context)                           // 0x12626
{
    PEK_START_CONTEXT c = (PEK_START_CONTEXT)Context;
    PDEVICE_EXTENSION d = c->DevExt;

    if (d->ActiveChannel != -1) {
        return FALSE;
    }

    EkFifoReset(d);
    EkPlxWrite(d, PLX_DMADPR0, c->Channel->DescLocal);      // (canale + 0x3004) << 18
    (VOID)EkPlxRead(d, PLX_DMACSR0);
    EkPlxWrite(d, PLX_DMACSR0, PLX_DMACSR_ENABLE);
    EkPlxWrite(d, PLX_DMACSR0,
               (EkPlxRead(d, PLX_DMACSR0) & PLX_DMACSR_ENABLE) | PLX_DMACSR_START);
    d->ActiveChannel = (LONG)c->Channel->Index;
    return TRUE;
}


NTSTATUS
EkDmaStart(PDEVICE_EXTENSION d, ULONG Channel, ULONG Length)   // 0x122A4
{
    PEK_CHANNEL      ch;
    EK_START_CONTEXT ctx;
    NTSTATUS         status;

    if (Channel >= EK_DMA_CHANNELS) {
        return STATUS_INVALID_PARAMETER;
    }
    ch = &d->Channel[Channel];

    KeAcquireSpinLockAtDpcLevel(&ch->Lock);

    if (ch->Irp == NULL || !ch->Armed || Length == 0 || Length > ch->ByteCount) {
        status = STATUS_INVALID_PARAMETER;
    } else if (ch->SgError) {
        status = STATUS_INSUFFICIENT_RESOURCES;
    } else if (d->ActiveChannel != -1) {
        status = STATUS_DEVICE_BUSY;
    } else {
        EkTruncate(ch, Length);
        ctx.DevExt  = d;
        ctx.Channel = ch;
        status = EkSync(d, EkDmaStartSync, &ctx) ? STATUS_SUCCESS : STATUS_DEVICE_BUSY;
    }

    KeReleaseSpinLockFromDpcLevel(&ch->Lock);
    return status;
}


NTSTATUS
EkDmaStop(PDEVICE_EXTENSION d, ULONG Channel)           // 0x1231C + 0x12668
{
    if (Channel >= EK_DMA_CHANNELS) {
        return STATUS_INVALID_PARAMETER;
    }
    if (d->ActiveChannel == -1) {
        return STATUS_ACCESS_VIOLATION;                 // come l'originale
    }
    EkSync(d, EkDmaStopSync, d);
    return STATUS_SUCCESS;
}


NTSTATUS
EkDmaRelease(PDEVICE_EXTENSION d, ULONG Channel)        // 0x121BE
{
    PEK_CHANNEL ch = &d->Channel[Channel];
    PIRP        irp;
    PSCATTER_GATHER_LIST sg;

    if (d->ActiveChannel == (LONG)Channel) {
        return STATUS_DEVICE_BUSY;
    }

    KeAcquireSpinLockAtDpcLevel(&ch->Lock);

    irp = ch->Irp;
    if (irp == NULL) {
        KeReleaseSpinLockFromDpcLevel(&ch->Lock);
        return STATUS_INVALID_PARAMETER;
    }
    if (!ch->Armed) {
        KeReleaseSpinLockFromDpcLevel(&ch->Lock);
        return STATUS_DEVICE_BUSY;
    }
    if (IoSetCancelRoutine(irp, NULL) == NULL) {
        KeReleaseSpinLockFromDpcLevel(&ch->Lock);
        return STATUS_INVALID_PARAMETER;                // annullamento gia' in corso
    }

    sg = ch->SgList;
    ch->SgList = NULL;
    ch->Irp    = NULL;
    ch->Armed  = FALSE;

    KeReleaseSpinLockFromDpcLevel(&ch->Lock);

    ch->Adapter->DmaOperations->PutScatterGatherList(ch->Adapter, sg, FALSE);
    irp->IoStatus.Information = 0;
    EkCompleteIrp(d, irp, STATUS_SUCCESS, IO_NO_INCREMENT);

    return STATUS_SUCCESS;
}


VOID
EkDmaFlush(PDEVICE_EXTENSION d, PFILE_OBJECT FileObject, NTSTATUS Status)
{
    ULONG i;

    for (i = 0; i < EK_DMA_CHANNELS; i++) {

        PEK_CHANNEL ch = &d->Channel[i];
        PIRP        irp = NULL;
        PSCATTER_GATHER_LIST sg = NULL;
        KIRQL       old;

        KeAcquireSpinLock(&ch->Lock, &old);

        if (ch->Irp != NULL &&
            (FileObject == NULL ||
             IoGetCurrentIrpStackLocation(ch->Irp)->FileObject == FileObject)) {

            if (!ch->Armed) {
                ch->CancelRequested = TRUE;     // completa il callback
            } else if (IoSetCancelRoutine(ch->Irp, NULL) != NULL) {
                irp = ch->Irp;
                sg  = ch->SgList;
                ch->Irp    = NULL;
                ch->SgList = NULL;
                ch->Armed  = FALSE;
            }
        }

        KeReleaseSpinLock(&ch->Lock, old);

        if (irp != NULL) {
            EkReleaseChannelIrp(ch, irp, sg, Status);
        }
    }
}
