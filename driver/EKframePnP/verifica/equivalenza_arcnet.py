"""
Verifica di equivalenza: codice macchina ORIGINALE di EKframe.sys (eseguito in
emulazione x86 con unicorn) contro la traduzione in C di arcnet.c, entrambi
collegati allo STESSO modello simulato del controller ARCNET COM20020.

Per ogni scenario si confrontano:
  - la sequenza completa degli accessi ai registri (lettura/scrittura,
    registro, valore) e delle attese KeStallExecutionProcessor;
  - il valore di ritorno;
  - i dati prodotti (buffer letti, memoria del chip scritta).
"""
import ctypes, copy, sys, os
import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UcError
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EIP

HERE = os.path.dirname(os.path.abspath(__file__))
# Il driver Kodak originale non sta nel repository (e' proprietario):
# va messo in verifica/originale/, oppure indicato con EKFRAME_ORIGINALE.
SYS = os.environ.get("EKFRAME_ORIGINALE", os.path.join(HERE, "originale", "EKframe.sys"))
ARC_BASE = 0x40008000           # base fittizia del COM20020 (finestra locale + 0x8000)


# ---------------------------------------------------------------------------
# Modello del COM20020
# ---------------------------------------------------------------------------
class Com20020:
    def __init__(self, node=0x00, status=0x00, diag=0x00, ram_fault=None, no_signature=False):
        self.ram = bytearray(2048)
        self.ptr = 0
        self.ptr_read = False
        self.ptr_autoinc = False
        self.reg = {0: 0, 1: 0, 6: 0, 7: 0}
        self.imr = 0
        self.node_id = node
        self.next_id = 0x77
        self.status = status
        self.diag = diag
        self.commands = []
        self.trace = []
        self.ram_fault = ram_fault          # (indirizzo, bit bloccato a 0)
        self.no_signature = no_signature

    def _sub(self):
        return self.reg[6] & 3

    def write(self, reg, val):
        val &= 0xFFFFFFFF
        self.trace.append(("W", reg, val))
        b = val & 0xFF
        if reg == 0:
            self.imr = b
        elif reg == 1:
            self.commands.append(b)
        elif reg == 2:
            self.ptr_read = bool(b & 0x80)
            self.ptr_autoinc = bool(b & 0x40)
            self.ptr = ((b & 0x07) << 8) | (self.ptr & 0xFF)
        elif reg == 3:
            self.ptr = (self.ptr & 0x700) | b
        elif reg == 4:
            v = b
            if self.ram_fault and self.ptr == self.ram_fault[0]:
                v &= ~self.ram_fault[1] & 0xFF
            self.ram[self.ptr & 0x7FF] = v
            if self.ptr_autoinc:
                self.ptr = (self.ptr + 1) & 0x7FF
        elif reg == 6:
            old = self.reg[6]
            self.reg[6] = b
            if (old & 0x80) and not (b & 0x80):     # uscita dal reset
                if not self.no_signature:
                    self.ram[0] = 0xD1
                    self.ram[1] = self.node_id
        elif reg == 7:
            if self._sub() == 1:
                self.node_id = b
            elif self._sub() == 3:
                self.next_id = b
            else:
                self.reg[7] = b

    def read(self, reg):
        if reg == 0:
            v = self.status
        elif reg == 1:
            v = self.diag
        elif reg == 2:
            v = (0x80 if self.ptr_read else 0) | (0x40 if self.ptr_autoinc else 0) | (self.ptr >> 8)
        elif reg == 3:
            v = self.ptr & 0xFF
        elif reg == 4:
            v = self.ram[self.ptr & 0x7FF]
            if self.ptr_autoinc:
                self.ptr = (self.ptr + 1) & 0x7FF
        elif reg == 6:
            v = self.reg[6]
        elif reg == 7:
            v = {1: self.node_id, 3: self.next_id}.get(self._sub(), self.reg[7])
        else:
            v = 0
        self.trace.append(("R", reg, v))
        return v

    def stall(self, us):
        self.trace.append(("S", us, 0))


def reg_of(addr):
    off = addr - ARC_BASE
    assert off % 4 == 0 and 0 <= off < 0x40, hex(addr)
    return off // 4


# ---------------------------------------------------------------------------
# Esecuzione del codice originale
# ---------------------------------------------------------------------------
class Original:
    STOP = 0x7FFF0000
    THUNKS = 0x80000000
    DATA = 0x00200000

    def __init__(self):
        self.pe = pefile.PE(SYS)
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        self.imports = {}
        for e in self.pe.DIRECTORY_ENTRY_IMPORT:
            for i in e.imports:
                self.imports[i.address] = i.name.decode()

    def _setup(self, chip):
        mu = Uc(UC_ARCH_X86, UC_MODE_32)
        mu.mem_map(self.base, 0x10000)
        for s in self.pe.sections:
            mu.mem_write(self.base + s.VirtualAddress, s.get_data())
        mu.mem_map(self.THUNKS, 0x1000)
        mu.mem_map(self.STOP & ~0xFFF, 0x1000)
        mu.mem_map(self.DATA, 0x100000)
        mu.mem_map(0x00100000, 0x10000)           # stack
        self.thunk_of = {}
        for n, (iat, name) in enumerate(sorted(self.imports.items())):
            t = self.THUNKS + n * 16
            mu.mem_write(iat, t.to_bytes(4, "little"))
            mu.mem_write(t, b"\xC3")
            self.thunk_of[t] = name

        def hook(mu, addr, size, _):
            if addr in self.thunk_of:
                name = self.thunk_of[addr]
                esp = mu.reg_read(UC_X86_REG_ESP)
                arg = lambda k: int.from_bytes(mu.mem_read(esp + 4 + 4 * k, 4), "little")
                ret = int.from_bytes(mu.mem_read(esp, 4), "little")
                if name == "WRITE_REGISTER_ULONG":
                    chip.write(reg_of(arg(0)), arg(1)); nargs = 2; eax = 0
                elif name == "READ_REGISTER_ULONG":
                    eax = chip.read(reg_of(arg(0))); nargs = 1
                elif name == "KeStallExecutionProcessor":
                    chip.stall(arg(0)); nargs = 1; eax = 0
                else:
                    raise RuntimeError("import inatteso: " + name)
                mu.reg_write(UC_X86_REG_EAX, eax)
                mu.reg_write(UC_X86_REG_ESP, esp + 4 + 4 * nargs)
                mu.reg_write(UC_X86_REG_EIP, ret)
            elif addr == 0x10E36:                   # registrazione errori: ignorata
                esp = mu.reg_read(UC_X86_REG_ESP)
                ret = int.from_bytes(mu.mem_read(esp, 4), "little")
                mu.reg_write(UC_X86_REG_ESP, esp + 4 + 0x24)
                mu.reg_write(UC_X86_REG_EIP, ret)
        mu.hook_add(UC_HOOK_CODE, hook)
        return mu

    def call(self, chip, func, args, prepare=None):
        mu = self._setup(chip)
        if prepare:
            prepare(mu)
        esp = 0x0010F000
        frame = [self.STOP] + list(args)
        esp -= 4 * len(frame)
        for k, v in enumerate(frame):
            mu.mem_write(esp + 4 * k, (v & 0xFFFFFFFF).to_bytes(4, "little"))
        mu.reg_write(UC_X86_REG_ESP, esp)
        mu.emu_start(func, self.STOP, count=5_000_000)
        return mu, mu.reg_read(UC_X86_REG_EAX)


# ---------------------------------------------------------------------------
# Esecuzione della traduzione in C
# ---------------------------------------------------------------------------
lib = ctypes.CDLL(os.path.join(HERE, "libarc.so"))
WCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32)
RCB = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)
SCB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)
CURRENT = {"chip": None}
_w = WCB(lambda a, v: CURRENT["chip"].write(reg_of(a), v))
_r = RCB(lambda a: CURRENT["chip"].read(reg_of(a)))
_s = SCB(lambda u: CURRENT["chip"].stall(u))
lib.set_cb(_w, _r, _s)
for fn, res, argt in [
    ("EkArcInit", ctypes.c_uint8, [ctypes.c_void_p, ctypes.c_uint8]),
    ("EkArcShutdown", None, [ctypes.c_void_p]),
    ("EkArcSend", ctypes.c_uint8, [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_char_p, ctypes.c_uint32]),
    ("EkArcReceive", ctypes.c_uint8, [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]),
    ("EkArcStatus", ctypes.c_uint32, [ctypes.c_void_p]),
    ("EkArcReadNextId", ctypes.c_uint8, [ctypes.c_void_p]),
]:
    getattr(lib, fn).restype = res
    getattr(lib, fn).argtypes = argt


def c_call(chip, fn, *args):
    CURRENT["chip"] = chip
    return getattr(lib, fn)(ctypes.c_void_p(ARC_BASE), *args)


# ---------------------------------------------------------------------------
# Scenari
# ---------------------------------------------------------------------------
orig = Original()
results = []


def compare(name, chip_o, chip_c, ret_o, ret_c, extra_o=None, extra_c=None):
    same_trace = chip_o.trace == chip_c.trace
    same_ret = ret_o == ret_c
    same_ram = chip_o.ram == chip_c.ram
    same_extra = extra_o == extra_c
    ok = same_trace and same_ret and same_ram and same_extra
    results.append(ok)
    print("%-52s %s  (%d accessi%s)" % (name, "UGUALE " if ok else "DIVERSO", len(chip_o.trace),
          "" if ok else "  trace=%s ret=%s/%s ram=%s extra=%s" % (same_trace, ret_o, ret_c, same_ram, same_extra)))
    if not same_trace:
        for k, (a, b) in enumerate(zip(chip_o.trace, chip_c.trace)):
            if a != b:
                print("    primo scarto all'accesso %d: originale %s, C %s" % (k, a, b)); break
        else:
            print("    lunghezze diverse: %d contro %d" % (len(chip_o.trace), len(chip_c.trace)))


def mkchip(**kw):
    return Com20020(**kw)


# 1. Inizializzazione
for label, kw in [("nominale, nodo 0x55", {}),
                  ("chip senza firma 0xD1", {"no_signature": True}),
                  ("RAM difettosa (bit bloccato a 0x123)", {"ram_fault": (0x123, 0x10)})]:
    a, b = mkchip(**kw), mkchip(**kw)
    _, ro = orig.call(a, 0x102C0, [ARC_BASE, 0x55, Original.DATA + 0x10000])
    rc = c_call(b, "EkArcInit", 0x55)
    compare("EkArcInit: " + label, a, b, ro & 0xFF, rc)

# 2. Spegnimento
a, b = mkchip(), mkchip()
orig.call(a, 0x1049E, [ARC_BASE]); c_call(b, "EkArcShutdown")
compare("EkArcShutdown", a, b, 0, 0)

# 3. Trasmissione, tutte le lunghezze di confine
for n in [1, 2, 100, 252, 253, 254, 255, 256, 257, 258, 300, 506, 507, 508, 600]:
    payload = bytes((i * 7 + 3) & 0xFF for i in range(n))
    a, b = mkchip(), mkchip()
    def prep(mu, p=payload):
        mu.mem_write(Original.DATA, p)
    _, ro = orig.call(a, 0x104E0, [ARC_BASE, 0x55, 0xAA, Original.DATA, n], prep)
    rc = c_call(b, "EkArcSend", 0x55, 0xAA, payload, n)
    compare("EkArcSend: %d byte" % n, a, b, ro & 0xFF, rc)

# 4. Ricezione: pacchetti corti e lunghi, con e senza verifica (DMA attivo)
def place(chip, src, dst, data):
    n = len(data)
    chip.ram[0] = src; chip.ram[1] = dst
    if n <= 253:
        off = 256 - n; chip.ram[2] = off
    else:
        off = 512 - n; chip.ram[2] = 0; chip.ram[3] = off
    chip.ram[off:off + n] = data

for n in [1, 50, 253, 257, 400, 507]:
    for active in (False, True):
        data = bytes((i * 13 + 1) & 0xFF for i in range(n))
        a, b = mkchip(), mkchip()
        place(a, 0xAA, 0x55, data); place(b, 0xAA, 0x55, data)
        DEV = Original.DATA + 0x10000
        def prep(mu, act=active):
            mu.mem_write(DEV + 0x9CC, (0 if act else 0xFFFFFFFF).to_bytes(4, "little"))
            mu.mem_write(Original.DATA + 0x100, (0x200).to_bytes(4, "little"))
        mu, ro = orig.call(a, 0x105CA, [ARC_BASE, Original.DATA + 0x104, Original.DATA + 0x200,
                                         Original.DATA + 0x100, DEV], prep)
        o_src = int.from_bytes(mu.mem_read(Original.DATA + 0x104, 4), "little")
        o_len = int.from_bytes(mu.mem_read(Original.DATA + 0x100, 4), "little")
        o_data = bytes(mu.mem_read(Original.DATA + 0x200, o_len)) if ro & 0xFF else b""
        src = ctypes.c_uint32(0); ln = ctypes.c_uint32(0x200); buf = ctypes.create_string_buffer(0x200)
        rc = c_call(b, "EkArcReceive", ctypes.byref(src), buf, ctypes.byref(ln), 3 if active else 0)
        c_data = buf.raw[:ln.value] if rc else b""
        compare("EkArcReceive: %d byte, DMA %s" % (n, "attivo" if active else "fermo"),
                a, b, ro & 0xFF, rc, (o_src, o_len, o_data), (src.value, ln.value, c_data))

a, b = mkchip(), mkchip()                          # pagina vuota
DEV = Original.DATA + 0x10000
def prep(mu):
    mu.mem_write(DEV + 0x9CC, (0xFFFFFFFF).to_bytes(4, "little"))
    mu.mem_write(Original.DATA + 0x100, (0x200).to_bytes(4, "little"))
_, ro = orig.call(a, 0x105CA, [ARC_BASE, Original.DATA + 0x104, Original.DATA + 0x200, Original.DATA + 0x100, DEV], prep)
src = ctypes.c_uint32(0); ln = ctypes.c_uint32(0x200); buf = ctypes.create_string_buffer(0x200)
rc = c_call(b, "EkArcReceive", ctypes.byref(src), buf, ctypes.byref(ln), 0)
compare("EkArcReceive: nessun pacchetto", a, b, ro & 0xFF, rc)

# 5. Parola di stato, tutte le combinazioni rilevanti
for st in [0x00, 0x01, 0x02, 0x04, 0x05, 0x80, 0x81, 0x85, 0xFF]:
    for dg in [0x00, 0x02, 0x10, 0xFF]:
        for rx in (0, 0xAA):
            a, b = mkchip(status=st, diag=dg), mkchip(status=st, diag=dg)
            a.ram[0] = rx; b.ram[0] = rx
            _, ro = orig.call(a, 0x10658, [ARC_BASE])
            rc = c_call(b, "EkArcStatus")
            compare("EkArcStatus: stato %02X diag %02X rx %02X" % (st, dg, rx), a, b, ro, rc)

# 6. ID nodo successivo
a, b = mkchip(), mkchip()
_, ro = orig.call(a, 0x106E4, [ARC_BASE]); rc = c_call(b, "EkArcReadNextId")
compare("EkArcReadNextId", a, b, ro & 0xFF, rc)

print()
print("SCENARI: %d   UGUALI: %d   DIVERSI: %d" % (len(results), sum(results), len(results) - sum(results)))
sys.exit(0 if all(results) else 1)
