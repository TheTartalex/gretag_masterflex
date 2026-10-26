/*++

    hw.c  -  EKframePnP 2.1

    Accesso ai registri, ISR e DPC.

    La ISR e' la traduzione della routine a 0x12750 dell'originale: lo stato
    della FPGA (locale + 0x400C) viene filtrato con la maschera abilitata
    (locale + 0x4008) e ogni bit ha il suo ramo:

        bit 1  DMA completato    -> evento 3
        bit 2  ARCNET            -> eventi 0 (rete pronta), 1 (rete persa),
                                    2 (messaggio), conferma di WriteFile
        bit 3  errore FIFO       -> DMA interrotto

    Rispetto all'originale gli aggiornamenti read-modify-write dei registri
    condivisi con la ISR (modo, reset FIFO) passano tutti da
    KeSynchronizeExecution, e l'anello dei messaggi ricevuti non viene mai
    sovrascritto quando e' pieno.

--*/

#include "ekframe.h"


BOOLEAN
EkOffsetValid(ULONG Offset, ULONG Length)
{
    if (Length < sizeof(ULONG)) {
        return FALSE;
    }
    return (Offset <= Length - sizeof(ULONG)) && ((Offset & 3) == 0);
}


VOID
EkPlxWrite(PDEVICE_EXTENSION DevExt, ULONG Offset, ULONG Value)
{
    if (DevExt->PlxRegBase != NULL && EkOffsetValid(Offset, DevExt->PlxRegLength)) {
        WRITE_REGISTER_ULONG((PULONG)(DevExt->PlxRegBase + Offset), Value);
    }
}


ULONG
EkPlxRead(PDEVICE_EXTENSION DevExt, ULONG Offset)
{
    if (DevExt->PlxRegBase != NULL && EkOffsetValid(Offset, DevExt->PlxRegLength)) {
        return READ_REGISTER_ULONG((PULONG)(DevExt->PlxRegBase + Offset));
    }
    return 0xFFFFFFFF;
}


VOID
EkLocalWrite(PDEVICE_EXTENSION DevExt, ULONG Offset, ULONG Value)
{
    if (DevExt->LocalRegBase != NULL && EkOffsetValid(Offset, DevExt->LocalRegLength)) {
        WRITE_REGISTER_ULONG((PULONG)(DevExt->LocalRegBase + Offset), Value);
    }
}


ULONG
EkLocalRead(PDEVICE_EXTENSION DevExt, ULONG Offset)
{
    if (DevExt->LocalRegBase != NULL && EkOffsetValid(Offset, DevExt->LocalRegLength)) {
        return READ_REGISTER_ULONG((PULONG)(DevExt->LocalRegBase + Offset));
    }
    return 0xFFFFFFFF;
}


VOID
EkFifoReset(PDEVICE_EXTENSION DevExt)                   // 0x125E8
{
    EkLocalWrite(DevExt, EKL_FIFO_RESET, 8);
    KeStallExecutionProcessor(1);
    EkLocalWrite(DevExt, EKL_FIFO_RESET, 0);
    (VOID)EkLocalRead(DevExt, 0);
}


VOID
EkSetModeBit(PDEVICE_EXTENSION DevExt, ULONG Bit, BOOLEAN On)   // 0x11A3E / 0x11A70
{
    ULONG v = EkLocalRead(DevExt, EKL_MODE);
    v = On ? (v | Bit) : (v & ~Bit);
    EkLocalWrite(DevExt, EKL_MODE, v);
}


VOID
EkSetIntMask(PDEVICE_EXTENSION DevExt, ULONG Mask)     // 0x1291E
{
    DevExt->IntMask = Mask;
    EkLocalWrite(DevExt, EKL_INT_ENABLE, Mask);
}


VOID
EkPlxEnableInterrupts(PDEVICE_EXTENSION DevExt, BOOLEAN Enable)     // 0x12958
{
    ULONG v = EkPlxRead(DevExt, PLX_INTCSR);
    v = Enable ? (v | PLX_INTCSR_ENABLE_BITS) : (v & ~PLX_INTCSR_ENABLE_BITS);
    EkPlxWrite(DevExt, PLX_INTCSR, v);
}


VOID
EkPlxInitLocalBus(PDEVICE_EXTENSION DevExt)             // 0x12942 + 0x12986
{
    EkPlxWrite(DevExt, PLX_LBRD0, PLX_LBRD0_VALUE);
    EkPlxWrite(DevExt, PLX_DMAMODE0, PLX_DMAMODE0_VALUE);
}


VOID
EkDmaDisable(PDEVICE_EXTENSION DevExt)                  // 0x129D6
{
    (VOID)EkPlxRead(DevExt, PLX_DMACSR0);
    EkPlxWrite(DevExt, PLX_DMACSR0, 0);
}


VOID
EkDmaAbort(PDEVICE_EXTENSION DevExt)                    // 0x12A1A
{
    ULONG v = EkPlxRead(DevExt, PLX_DMACSR0) & PLX_DMACSR_ENABLE;
    EkPlxWrite(DevExt, PLX_DMACSR0, v | PLX_DMACSR_ABORT);
}


VOID
EkDmaClearInterrupt(PDEVICE_EXTENSION DevExt)           // 0x12A3E
{
    ULONG v = EkPlxRead(DevExt, PLX_DMACSR0) & PLX_DMACSR_ENABLE;
    EkPlxWrite(DevExt, PLX_DMACSR0, v | PLX_DMACSR_CLEAR_INT);
}


BOOLEAN
EkSync(PDEVICE_EXTENSION DevExt, PKSYNCHRONIZE_ROUTINE Routine, PVOID Context)
{
    if (DevExt->InterruptObject != NULL) {
        return KeSynchronizeExecution(DevExt->InterruptObject, Routine, Context);
    }
    return Routine(Context);
}


static BOOLEAN NTAPI
EkFifoResetSync(PVOID Context)
{
    EkFifoReset((PDEVICE_EXTENSION)Context);
    return TRUE;
}


//
// ---------------------------------------------------------------------------
//  ISR
// ---------------------------------------------------------------------------
//
BOOLEAN NTAPI
EkIsr(PKINTERRUPT Interrupt, PVOID ServiceContext)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)ServiceContext;
    ULONG   status;
    BOOLEAN dmaDone = FALSE;

    UNREFERENCED_PARAMETER(Interrupt);

    d->IsrCount++;

    if (d->LocalRegBase == NULL || d->IntMask == 0) {
        return FALSE;
    }

    status = EkLocalRead(d, EKL_INT_STATUS);
    if (status == 0xFFFFFFFF) {
        return FALSE;                                   // scheda non raggiungibile
    }
    status &= d->IntMask;
    if (status == 0) {
        return FALSE;                                   // IRQ condiviso: non e' nostro
    }

    d->IsrClaimed++;

    //
    // DMA completato
    //
    if (status & EK_INT_DMA) {
        if (EkPlxRead(d, PLX_INTCSR) & PLX_INTCSR_DMA0_ACTIVE) {
            EkDmaDisable(d);
            EkDmaClearInterrupt(d);
            dmaDone = TRUE;
            if (d->ActiveChannel != -1) {
                d->DmaDoneCount++;
                KeInsertQueueDpc(&d->DpcDma, (PVOID)1, NULL);
                d->ActiveChannel = -1;
            }
        }
    }

    //
    // Errore del FIFO: l'originale reimposta il FIFO e, se il DMA non si e'
    // appena concluso, lo interrompe.
    //
    if (status & EK_INT_FIFO) {
        EkLocalWrite(d, EKL_FIFO_RESET, 8);
        EkLocalWrite(d, EKL_FIFO_RESET, 0);
        if (!dmaDone) {
            EkDmaDisable(d);
            EkDmaAbort(d);
            d->ActiveChannel = -1;
        }
    }

    //
    // ARCNET
    //
    if (status & EK_INT_ARCNET) {

        ULONG s = EkArcStatus(d->ArcBase);
        d->LastArcStatus = s;

        if ((s & 0x0400) && d->NetReady) {
            d->NetReady = FALSE;
            KeInsertQueueDpc(&d->DpcNetLost, NULL, NULL);
        }

        if (s & 0x0002) {
            d->NetReady = TRUE;
            (VOID)EkArcReadNextId(d->ArcBase);
            KeInsertQueueDpc(&d->DpcNetReady, NULL, NULL);
        }

        if (s & 0xA000) {

            ULONG head = d->RxHead;
            ULONG next = (head + 1) % EK_RX_RING_SIZE;
            PEK_RX_ENTRY e = &d->RxRing[head];

            EkSetModeBit(d, EK_MODE_RX_PENDING, TRUE);

            e->Length = EK_RX_MESSAGE_MAX;
            if (EkArcReceive(d->ArcBase, &e->Source, e->Data, &e->Length,
                             (d->ActiveChannel != -1) ? 3 : 0)) {

                //
                // Come l'originale, il messaggio entra nell'anello solo se
                // qualcuno ha registrato l'evento 2. In piu': se l'anello e'
                // pieno il messaggio viene scartato invece di sovrascrivere
                // quello che l'applicazione sta leggendo.
                //
                if (d->Event[EK_EVENT_MESSAGE] != NULL && next != d->RxTail) {
                    d->RxHead = next;
                    d->RxCount++;
                    KeInsertQueueDpc(&d->DpcMessage, NULL, NULL);
                } else {
                    d->RxDropped++;
                }
            }

            if (d->RxHead == d->RxTail) {
                EkSetModeBit(d, EK_MODE_RX_PENDING, FALSE);
            }
        }

        if (d->TxPending && (s & 0x0100)) {
            d->TxPending = FALSE;
            EkSetModeBit(d, EK_MODE_TX_PENDING, FALSE);
            KeInsertQueueDpc(&d->DpcTxDone, (PVOID)(ULONG_PTR)((s & 0x0200) ? 1 : 0), NULL);
        }
    }

    return TRUE;
}


//
// ---------------------------------------------------------------------------
//  DPC - una per evento, come l'originale (0x10C8A ... 0x10D66)
// ---------------------------------------------------------------------------
//
VOID NTAPI
EkDpcNetReady(PKDPC Dpc, PVOID Context, PVOID Arg1, PVOID Arg2)
{
    PDEVICE_EXTENSION d = (PDEVICE_EXTENSION)Context;
    UNREFERENCED_PARAMETER(Dpc);
    UNREFERENCED_PARAMETER(Arg1);
    UNREFERENCED_PARAMETER(Arg2);

    EkSignalEvent(d, EK_EVENT_NET_READY);
    EkSync(d, EkFifoResetSync, d);
}


VOID NTAPI
EkDpcNetLost(PKDPC Dpc, PVOID Context, PVOID Arg1, PVOID Arg2)
{
    UNREFERENCED_PARAMETER(Dpc);
    UNREFERENCED_PARAMETER(Arg1);
    UNREFERENCED_PARAMETER(Arg2);
    EkSignalEvent((PDEVICE_EXTENSION)Context, EK_EVENT_NET_LOST);
}


VOID NTAPI
EkDpcMessage(PKDPC Dpc, PVOID Context, PVOID Arg1, PVOID Arg2)
{
    UNREFERENCED_PARAMETER(Dpc);
    UNREFERENCED_PARAMETER(Arg1);
    UNREFERENCED_PARAMETER(Arg2);
    EkSignalEvent((PDEVICE_EXTENSION)Context, EK_EVENT_MESSAGE);
}


VOID EkCompleteWrite(PDEVICE_EXTENSION DevExt, BOOLEAN Success);

VOID NTAPI
EkDpcTxDone(PKDPC Dpc, PVOID Context, PVOID Arg1, PVOID Arg2)
{
    UNREFERENCED_PARAMETER(Dpc);
    UNREFERENCED_PARAMETER(Arg2);
    EkCompleteWrite((PDEVICE_EXTENSION)Context, (BOOLEAN)(Arg1 != NULL));
}


VOID NTAPI
EkDpcDma(PKDPC Dpc, PVOID Context, PVOID Arg1, PVOID Arg2)
{
    UNREFERENCED_PARAMETER(Dpc);
    UNREFERENCED_PARAMETER(Arg2);
    EkSignalEvent((PDEVICE_EXTENSION)Context,
                  (Arg1 != NULL) ? EK_EVENT_DMA_DONE : EK_EVENT_AUX);
}
