"""
Equivalenza del percorso DMA: codice macchina originale (AdapterControl
0x1236E, troncamento 0x1246E, avvio 0x12626) contro dma.c/hw.c.

Si confrontano:
  - l'immagine dei descrittori scritti nella memoria della scheda, dopo
    l'aggancio e dopo ogni avvio con una lunghezza diversa;
  - la sequenza di accessi ai registri PLX e FPGA dell'avvio;
  - il numero di descrittori.
"""
import ctypes, os, random, sys
import pefile
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
from unicorn.x86_const import UC_X86_REG_ESP, UC_X86_REG_EAX, UC_X86_REG_EDX, UC_X86_REG_EIP

HERE = os.path.dirname(os.path.abspath(__file__))
# Il driver Kodak originale non sta nel repository (e' proprietario):
# va messo in verifica/originale/, oppure indicato con EKFRAME_ORIGINALE.
SYS = os.environ.get("EKFRAME_ORIGINALE", os.path.join(HERE, "originale", "EKframe.sys"))

PLX_BASE   = 0x50000000
LOCAL_BASE = 0x60000000
DESC_LO, DESC_HI = LOCAL_BASE + 0x100000, LOCAL_BASE + 0x180000
PAGE = 0x1000


class Bus:
    def __init__(self):
        self.mem = {}
        self.trace = []

    def write(self, a, v):
        self.mem[a] = v & 0xFFFFFFFF
        self.trace.append(("W", a, v & 0xFFFFFFFF))

    def read(self, a):
        v = self.mem.get(a, 0)
        self.trace.append(("R", a, v))
        return v

    def stall(self, us):
        self.trace.append(("S", us, 0))

    def descriptors(self):
        return {a: v for a, v in self.mem.items() if DESC_LO <= a < DESC_HI}

    def control_trace(self):
        return [t for t in self.trace if t[0] == "S" or not (DESC_LO <= t[1] < DESC_HI)]


class PageMap:
    """VA -> PA con contiguita' controllata."""
    def __init__(self, va, length, runs_pages, seed):
        rnd = random.Random(seed)
        first = va & ~(PAGE - 1)
        npages = ((va & (PAGE - 1)) + length + PAGE - 1) // PAGE
        self.pa = {}
        p = 0
        base = 0x10000000
        while p < npages:
            run = min(rnd.choice(runs_pages), npages - p)
            base += rnd.randint(1, 50) * PAGE * 3
            for k in range(run):
                self.pa[first + (p + k) * PAGE] = base + k * PAGE
            base += run * PAGE
            p += run
        self.va, self.length = va, length

    def phys(self, va):
        return self.pa[va & ~(PAGE - 1)] + (va & (PAGE - 1))

    def contiguous(self, va, want):
        got = PAGE - (va & (PAGE - 1))
        while got < want:
            a = va + got
            if self.pa[a & ~(PAGE - 1)] != self.phys(va) + got:
                break
            got += PAGE
        return min(got, want)

    def sg(self):
        """Elementi massimi contigui, come li darebbe GetScatterGatherList."""
        out = []
        va, rem = self.va, self.length
        while rem:
            n = self.contiguous(va, rem)
            out.append((self.phys(va), n))
            va += n; rem -= n
        return out


# ---------------------------------------------------------------------------
class Original:
    STOP, THUNKS = 0x7FFF0000, 0x80000000
    DEV, CHAN0, IRP, MDL = 0x00300000, 0x00300554, 0x00320000, 0x00321000

    def __init__(self, bus, pmap):
        pe = pefile.PE(SYS)
        self.bus, self.pmap = bus, pmap
        mu = self.mu = Uc(UC_ARCH_X86, UC_MODE_32)
        base = pe.OPTIONAL_HEADER.ImageBase
        mu.mem_map(base, 0x10000)
        for s in pe.sections:
            mu.mem_write(base + s.VirtualAddress, s.get_data())
        mu.mem_map(self.THUNKS, 0x1000); mu.mem_map(self.STOP, 0x1000)
        mu.mem_map(0x00100000, 0x10000); mu.mem_map(0x00300000, 0x30000)
        names = {}
        n = 0
        for e in pe.DIRECTORY_ENTRY_IMPORT:
            for i in e.imports:
                t = self.THUNKS + n * 16; n += 1
                mu.mem_write(i.address, t.to_bytes(4, "little")); mu.mem_write(t, b"\xC3")
                names[t] = i.name.decode()
        self.names = names
        mu.hook_add(UC_HOOK_CODE, self._hook)

    def r32(self, a): return int.from_bytes(self.mu.mem_read(a, 4), "little")
    def w32(self, a, v): self.mu.mem_write(a, (v & 0xFFFFFFFF).to_bytes(4, "little"))

    def _hook(self, mu, addr, size, _):
        if addr in self.names:
            name = self.names[addr]
            esp = mu.reg_read(UC_X86_REG_ESP)
            arg = lambda k: self.r32(esp + 4 + 4 * k)
            ret = self.r32(esp)
            edx = 0
            if name == "WRITE_REGISTER_ULONG":
                self.bus.write(arg(0), arg(1)); nargs, eax = 2, 0
            elif name == "READ_REGISTER_ULONG":
                eax = self.bus.read(arg(0)); nargs = 1
            elif name == "KeStallExecutionProcessor":
                self.bus.stall(arg(0)); nargs, eax = 1, 0
            elif name == "IoMapTransfer":
                cur, plen = arg(3), arg(4)
                want = self.r32(plen)
                got = self.pmap.contiguous(cur, want)
                self.w32(plen, got)
                eax = self.pmap.phys(cur); nargs = 6
            else:
                raise RuntimeError(name)
            mu.reg_write(UC_X86_REG_EAX, eax); mu.reg_write(UC_X86_REG_EDX, edx)
            mu.reg_write(UC_X86_REG_ESP, esp + 4 + 4 * nargs); mu.reg_write(UC_X86_REG_EIP, ret)

    def call(self, func, args):
        esp = 0x0010F000 - 4 * (len(args) + 1)
        for k, v in enumerate([self.STOP] + list(args)):
            self.w32(esp + 4 * k, v)
        self.mu.reg_write(UC_X86_REG_ESP, esp)
        self.mu.emu_start(func, self.STOP, count=20_000_000)
        return self.mu.reg_read(UC_X86_REG_EAX)

    def chan(self, ch): return self.CHAN0 + ch * 0x23C

    def arm(self, ch, va, length):
        c = self.chan(ch)
        self.w32(self.DEV + 0x118, LOCAL_BASE); self.w32(self.DEV + 0x128, PLX_BASE)
        self.w32(self.DEV + 0x9CC, 0xFFFFFFFF)
        self.w32(c + 0x04, ch); self.w32(c + 0x14, LOCAL_BASE + 0x100000 + ch * 0x40000)
        self.w32(c + 0x1C, self.IRP); self.w32(self.IRP + 4, self.MDL)
        self.w32(c + 0x20, length); self.w32(c + 0x28, va)
        self.call(0x1236E, [0, self.IRP, 0x1234, c])
        return self.r32(c + 0x24) + (0 if length == 0 else 1)   # 0x24 = indice dell'ultimo

    def start(self, ch, length):
        self.w32(self.DEV + 0x9CC, 0xFFFFFFFF)                   # DMA precedente concluso
        self.call(0x1246E, [self.chan(ch), length])
        self.call(0x12626, [self.DEV, ch])


# ---------------------------------------------------------------------------
lib = ctypes.CDLL(os.path.join(HERE, "libdma.so"))
WCB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32)
RCB = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)
SCB = ctypes.CFUNCTYPE(None, ctypes.c_uint32)
CUR = {}
_w = WCB(lambda a, v: CUR["bus"].write(a, v))
_r = RCB(lambda a: CUR["bus"].read(a))
_s = SCB(lambda u: CUR["bus"].stall(u))
lib.set_cb(_w, _r, _s)


class SGE(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_uint32), ("High", ctypes.c_int32), ("Length", ctypes.c_uint32),
                ("Reserved", ctypes.c_size_t)]


def make_sg(elems):
    class SGL(ctypes.Structure):
        _fields_ = [("Number", ctypes.c_uint32), ("Reserved", ctypes.c_size_t), ("E", SGE * len(elems))]
    s = SGL(); s.Number = len(elems)
    for k, (pa, n) in enumerate(elems):
        s.E[k].Low, s.E[k].High, s.E[k].Length = pa, 0, n
    return s


class Mine:
    def __init__(self, bus):
        self.bus = bus
        CUR["bus"] = bus
        lib.tb_setup(ctypes.c_void_p(PLX_BASE), 0x100, ctypes.c_void_p(LOCAL_BASE), 0x200000)

    def arm(self, ch, va, length, elems):
        self.sg = make_sg(elems)
        st = lib.tb_arm(ch, va, length, ctypes.byref(self.sg))
        assert (st & 0xFFFFFFFF) == 0x103, hex(st)
        return lib.tb_desc_count(ch)

    def start(self, ch, length):
        lib.tb_set_active(-1)
        st = lib.tb_start(ch, length) & 0xFFFFFFFF
        assert st == 0, hex(st)


# ---------------------------------------------------------------------------
results = []

def scenario(name, ch, va, length, runs, seed, lengths, expect_diff_at=None):
    pmap = PageMap(va, length, runs, seed)
    bo, bm = Bus(), Bus()
    o, m = Original(bo, pmap), Mine(bm)
    no = o.arm(ch, va, length)
    nm = m.arm(ch, va, length, pmap.sg())
    ok = (no == nm) and (bo.descriptors() == bm.descriptors())
    detail = ["aggancio: %d descrittori %s" % (nm, "uguali" if ok else "DIVERSI (orig %d)" % no)]
    for L in lengths:
        bo.trace.clear(); bm.trace.clear()
        o.start(ch, L); m.start(ch, L)
        same_desc = bo.descriptors() == bm.descriptors()
        same_ctrl = bo.control_trace() == bm.control_trace()
        step_ok = same_desc and same_ctrl
        if expect_diff_at is not None and L == expect_diff_at:
            detail.append("avvio %d: %s (atteso: difetto dell'originale)" % (L, "differenza" if not step_ok else "uguale"))
            continue
        ok = ok and step_ok
        if not step_ok:
            detail.append("avvio %d: descrittori %s, registri %s" % (L, same_desc, same_ctrl))
    results.append(ok)
    print("%-48s %s" % (name, "UGUALE " if ok else "DIVERSO"))
    for d in detail:
        print("      " + d)


SCAN = 2100 * 1400 * 2
scenario("1 pagina, canale 0", 0, 0x00A00000, 4096, [1], 1, [4096, 100])
scenario("1 pagina, ritorno a piena lunghezza dopo troncamento", 0, 0x00A00000, 4096, [1], 1, [100, 4096], expect_diff_at=4096)
scenario("scansione 2100x1400x16bit, pagine sparse", 0, 0x01000000, SCAN, [1], 2,
         [SCAN, 4096 * 7, SCAN // 2, 4096 * 7 + 13, SCAN])
scenario("scansione, canale 1, tratti contigui misti", 1, 0x02000000 + 0x340, SCAN, [1, 2, 8, 64], 3,
         [SCAN, 12345, 2 * 1024 * 1024, 12345, SCAN])
scenario("10 MB, tratti contigui da 6 MB (spezzati a 4 MB)", 0, 0x03000000, 10 * 1024 * 1024, [1536], 4,
         [10 * 1024 * 1024, 4 * 1024 * 1024, 4 * 1024 * 1024 + 1, 10 * 1024 * 1024])
scenario("buffer non allineato, lunghezze sul confine", 0, 0x04000123, 300000, [1, 3], 5,
         [300000, 4096 - 0x123, 4096 - 0x123 + 1, 8192, 300000])
scenario("troncamento nell'ultimo descrittore", 0, 0x05000000, 20480, [1], 6,
         [20480 - 100, 20480, 20480 - 100], expect_diff_at=20480)

print()
print("SCENARI: %d   UGUALI: %d" % (len(results), sum(results)))
sys.exit(0 if all(results) else 1)
