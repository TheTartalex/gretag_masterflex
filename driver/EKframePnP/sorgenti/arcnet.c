/*++

    arcnet.c  -  EKframePnP 2.1

    Controller ARCNET SMC COM20020, alla finestra locale + 0x8000, registri a
    passo 4 byte. E' il canale comandi verso il controllore della testa di
    scansione: lo usano ReadFile/WriteFile e l'IOCTL di apertura.

    Ogni routine e' la traduzione istruzione per istruzione di quella
    dell'originale indicata nel commento, comprese le attese di 2 us dopo
    ogni accesso a registro e i valori dei comandi. Il chip e' stato
    identificato dalla firma che scrive dopo il reset (0xD1 all'indirizzo 0,
    il proprio ID di nodo all'indirizzo 1) e dal formato dei pacchetti
    corti/lunghi ARCNET.

    Mappa registri COM20020:
        0  R: stato               W: maschera interrupt
        1  R: stato diagnostico   W: comando
        2  puntatore indirizzo, parte alta (bit7 lettura, bit6 autoincremento)
        3  puntatore indirizzo, parte bassa
        4  dato
        6  configurazione (bit7 reset, bit5 TXEN, bit0-1 selezione sottoregistro)
        7  sottoregistro (1 = ID nodo, 3 = ID nodo successivo)

--*/

#include "ekframe.h"

#define ARC_STATUS      0
#define ARC_COMMAND     1
#define ARC_ADDR_HI     2
#define ARC_ADDR_LO     3
#define ARC_DATA        4
#define ARC_CONFIG      6
#define ARC_SUBREG      7

#define ARC_CMD_DISABLE_TX      0x01
#define ARC_CMD_DISABLE_RX      0x02
#define ARC_CMD_TX_PAGE1        0x0B    // abilita trasmissione dalla pagina 1
#define ARC_CMD_DEFINE_CONFIG   0x0D    // pacchetti lunghi ammessi
#define ARC_CMD_CLEAR_FLAGS     0x1E
#define ARC_CMD_RX_PAGE0        0x84    // abilita ricezione nella pagina 0, broadcast compresi

#define ARC_RX_PAGE             0x000
#define ARC_TX_PAGE             0x200


static VOID
ArcW(PUCHAR Arc, ULONG Reg, UCHAR Value)               // 0x108FC
{
    WRITE_REGISTER_ULONG((PULONG)(Arc + Reg * 4), Value);
    KeStallExecutionProcessor(2);
}


static UCHAR
ArcR(PUCHAR Arc, ULONG Reg)                            // 0x10920
{
    UCHAR v = (UCHAR)READ_REGISTER_ULONG((PULONG)(Arc + Reg * 4));
    KeStallExecutionProcessor(2);
    return v;
}


static VOID
ArcMemWrite(PUCHAR Arc, ULONG Address, PUCHAR Buffer, ULONG Length)     // 0x10830
{
    ArcW(Arc, ARC_ADDR_HI, (UCHAR)((Address >> 8) | 0x40));
    ArcW(Arc, ARC_ADDR_LO, (UCHAR)Address);

    while (Length != 0) {
        ArcW(Arc, ARC_DATA, *Buffer);
        Buffer++;
        Length--;
    }
}


static VOID
ArcMemRead(PUCHAR Arc, ULONG Address, PUCHAR Buffer, ULONG Length, ULONG VerifyRetries)  // 0x10778
/*++
    Con VerifyRetries > 0 il blocco viene riletto e confrontato, e la lettura
    ripetuta fino a quel numero di volte: l'originale lo fa mentre un DMA e'
    in corso, perche' il traffico sul bus locale puo' sporcare le letture.
--*/
{
    ULONG attempts = (VerifyRetries != 0) ? VerifyRetries : 1;
    UCHAR hi = (UCHAR)((Address >> 8) | 0xC0);
    ULONG attempt;
    ULONG i;

    for (attempt = 0; attempt < attempts; attempt++) {

        ArcW(Arc, ARC_ADDR_HI, hi);
        ArcW(Arc, ARC_ADDR_LO, (UCHAR)Address);
        for (i = 0; i < Length; i++) {
            Buffer[i] = ArcR(Arc, ARC_DATA);
        }

        if (VerifyRetries == 0) {
            return;
        }

        ArcW(Arc, ARC_ADDR_HI, hi);
        ArcW(Arc, ARC_ADDR_LO, (UCHAR)Address);
        for (i = 0; i < Length; i++) {
            if (ArcR(Arc, ARC_DATA) != Buffer[i]) {
                break;
            }
        }
        if (i == Length) {
            return;
        }
    }
}


static UCHAR
ArcGetNodeId(PUCHAR Arc)                                // 0x10712
{
    UCHAR c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)((c & 0xFD) | 0x01));
    return ArcR(Arc, ARC_SUBREG);
}


UCHAR
EkArcReadNextId(PUCHAR Arc)                             // 0x106E4
{
    UCHAR c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)(c | 0x03));
    return ArcR(Arc, ARC_SUBREG);
}


static BOOLEAN
ArcMemTestPattern(PUCHAR Arc, UCHAR Pattern)            // 0x10876
{
    UCHAR wr[32];
    UCHAR rd[32];
    ULONG address;
    ULONG i;

    for (i = 0; i < sizeof(wr); i++) {
        wr[i] = Pattern;
    }

    for (address = 0; address < 0x800; address += 32) {
        ArcMemWrite(Arc, address, wr, 32);
        ArcMemRead(Arc, address, rd, 32, 0);
        for (i = 0; i < 32; i++) {
            if (rd[i] != wr[i]) {
                return FALSE;
            }
        }
    }
    return TRUE;
}


static BOOLEAN
ArcMemTest(PUCHAR Arc)                                  // 0x10742
{
    if (!ArcMemTestPattern(Arc, 0xAA)) return FALSE;
    if (!ArcMemTestPattern(Arc, 0x55)) return FALSE;
    return ArcMemTestPattern(Arc, 0x00);
}


static VOID
ArcClearRxPage(PUCHAR Arc)                              // 0x108E0
{
    UCHAR zero = 0;
    ArcMemWrite(Arc, ARC_RX_PAGE, &zero, 1);
}


BOOLEAN
EkArcInit(PUCHAR Arc, UCHAR NodeId)                     // 0x102C0
{
    UCHAR c;
    UCHAR b;
    UCHAR zero2[2];

    zero2[0] = 0;
    zero2[1] = 0;

    ArcW(Arc, ARC_ADDR_HI, 0);
    (VOID)ArcR(Arc, ARC_ADDR_HI);
    ArcW(Arc, ARC_ADDR_LO, 0);
    (VOID)ArcR(Arc, ARC_ADDR_LO);
    ArcW(Arc, ARC_STATUS, 0);                           // maschera interrupt = 0

    ArcW(Arc, ARC_CONFIG, 0x1A);
    c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)(c | 0x18));

    ArcMemWrite(Arc, 0, zero2, 2);

    c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)((c & 0xFD) | 0x01));  // sottoregistro = ID nodo
    ArcW(Arc, ARC_SUBREG, NodeId);

    c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)(c | 0x80));           // reset
    KeStallExecutionProcessor(100);
    c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)(c & 0x7F));

    ArcW(Arc, ARC_COMMAND, ARC_CMD_DEFINE_CONFIG);
    KeStallExecutionProcessor(100);

    //
    // Dopo il reset il COM20020 scrive 0xD1 all'indirizzo 0 e il proprio ID
    // all'indirizzo 1: e' la prova che il chip c'e' ed e' vivo.
    //
    ArcMemRead(Arc, 0, &b, 1, 0);
    if (b != 0xD1) {
        return FALSE;
    }
    ArcMemRead(Arc, 1, &b, 1, 0);
    if (b != ArcGetNodeId(Arc)) {
        return FALSE;
    }

    ArcW(Arc, ARC_COMMAND, ARC_CMD_DISABLE_RX);
    ArcW(Arc, ARC_COMMAND, ARC_CMD_DISABLE_TX);

    c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)(c | 0x20));           // TXEN

    if (ArcGetNodeId(Arc) != NodeId) {
        return FALSE;
    }

    if (!ArcMemTest(Arc)) {
        return FALSE;
    }

    ArcClearRxPage(Arc);
    ArcW(Arc, ARC_COMMAND, ARC_CMD_RX_PAGE0);
    ArcW(Arc, ARC_STATUS, 0x86);                        // maschera interrupt

    return TRUE;
}


VOID
EkArcShutdown(PUCHAR Arc)                               // 0x1049E
{
    UCHAR c;

    ArcW(Arc, ARC_STATUS, 0);
    ArcW(Arc, ARC_COMMAND, ARC_CMD_DISABLE_RX);
    ArcW(Arc, ARC_COMMAND, ARC_CMD_DISABLE_TX);
    c = ArcR(Arc, ARC_CONFIG);
    ArcW(Arc, ARC_CONFIG, (UCHAR)(c | 0x80));           // resta in reset
}


BOOLEAN
EkArcSend(PUCHAR Arc, UCHAR Source, UCHAR Dest, PUCHAR Data, ULONG Length)     // 0x104E0
/*++
    Pacchetto corto (fino a 253 byte) o lungo (257..507 byte). Le lunghezze
    254-256 non sono rappresentabili in ARCNET e vengono portate a 257 con
    byte di riempimento a zero, come fa l'originale.
--*/
{
    UCHAR hdr[4];
    UCHAR pad[4];
    ULONG padLength = 0;

    if (Length >= 0x1FC) {
        return FALSE;
    }

    pad[0] = pad[1] = pad[2] = pad[3] = 0;
    hdr[0] = Source;
    hdr[1] = Dest;
    hdr[2] = 0;
    hdr[3] = 0;

    if (Length > 0xFD) {

        if (Length < 0x101) {
            padLength = 0x101 - Length;
            Length = 0x101;
        }
        hdr[3] = (UCHAR)(0 - Length);                   // 512 - lunghezza

        ArcMemWrite(Arc, ARC_TX_PAGE, hdr, 4);
        ArcMemWrite(Arc, ARC_TX_PAGE + hdr[3], Data, Length - padLength);
        if (padLength != 0) {
            ArcMemWrite(Arc, ARC_TX_PAGE + hdr[3] + (Length - padLength), pad, padLength);
        }

    } else {

        hdr[2] = (UCHAR)(0 - Length);                   // 256 - lunghezza

        ArcMemWrite(Arc, ARC_TX_PAGE, hdr, 3);
        ArcMemWrite(Arc, ARC_TX_PAGE + hdr[2], Data, Length);
    }

    ArcW(Arc, ARC_COMMAND, ARC_CMD_TX_PAGE1);
    ArcW(Arc, ARC_STATUS, 0x85);                        // maschera, con "trasmettitore libero"

    return TRUE;
}


BOOLEAN
EkArcReceive(PUCHAR Arc, PULONG Source, PUCHAR Data, PULONG Length, ULONG VerifyRetries)   // 0x105CA
{
    UCHAR hdr[4];
    ULONG offset;
    ULONG count;

    ArcMemRead(Arc, ARC_RX_PAGE, hdr, 4, VerifyRetries);

    *Source = hdr[0];
    if (hdr[0] == 0) {
        return FALSE;
    }

    if (hdr[2] != 0) {
        offset = hdr[2];
        count  = 0x100 - offset;
    } else {
        offset = hdr[3];
        count  = 0x200 - offset;
    }

    if (count > *Length) {
        return FALSE;
    }
    *Length = count;

    ArcMemRead(Arc, ARC_RX_PAGE + offset, Data, count, VerifyRetries);

    ArcClearRxPage(Arc);
    ArcW(Arc, ARC_COMMAND, ARC_CMD_RX_PAGE0);

    return TRUE;
}


ULONG
EkArcStatus(PUCHAR Arc)                                 // 0x10658
/*++
    Parola di stato come la costruisce l'originale:
        bit 8..15  registro di stato (| 0x40)
        bit 0..7   registro diagnostico
        bit 13     forzato a 1 se la pagina di ricezione contiene un pacchetto
--*/
{
    ULONG s;
    UCHAR b;

    s  = ((ULONG)ArcR(Arc, ARC_STATUS) | 0x40) << 8;
    s |= ArcR(Arc, ARC_COMMAND);

    ArcMemRead(Arc, ARC_RX_PAGE, &b, 1, 0);
    if (b != 0) {
        s |= 0x2000;
    } else {
        s &= ~0x2000UL;
    }

    ArcW(Arc, ARC_COMMAND, ARC_CMD_CLEAR_FLAGS);

    if (s & 0x0100) ArcW(Arc, ARC_STATUS, 0x84);
    if (s & 0x0400) ArcW(Arc, ARC_STATUS, 0x82);
    if (s & 0x0002) ArcW(Arc, ARC_STATUS, 0x84);

    return s;
}
