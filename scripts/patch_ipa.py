#!/usr/bin/env python3
"""Patch Blockheads so first launch copies bundle portalChest into the
sandbox and a new blockhead spawns holding a portal chest (item 1074).
"""
from __future__ import annotations

import shutil
import struct
import zipfile
from pathlib import Path

APP = Path(r"C:\Users\cedri\Downloads\_bh_ipa_mod\Payload\Blockheads.app")
BIN = APP / "Blockheads"
ORIG_IPA = Path(r"C:\Users\cedri\Downloads\Blockheads_1.7_64bit_CrackerXI.ipa")
OUT_IPA = Path(r"C:\Users\cedri\Downloads\Blockheads_1.7_mod_portalchest.ipa")
PORTAL_SRC = Path(r"C:\Users\cedri\Downloads\Blockheads-MOD-extras\portalChest")

# Known addresses in this 1.7 ARM64 binary
OBJC_MSGSEND = 0x100425F7C
NSSTRING_CLS = 0x1005DEA58
NSFILEMANAGER_CLS = 0x1005DEA98
NSBUNDLE_CLS = 0x1005DEB58
INVENTORYITEM_CLS = 0x1005DEB48
SEL_STRINGWITHFORMAT = 0x1005D14C0
SEL_DEFAULTMANAGER = 0x1005D1690
SEL_FILEEXISTS = 0x1005D1B40
SEL_MAINBUNDLE = 0x1005D1B48
SEL_COPYITEM = 0x1005D2208
SEL_ALLOC = 0x1005D14C8
SEL_INITWITHTYPE = 0x1005D3EA0
SEL_ADDITEM = 0x1005D5320
SEL_ADDITEM_FLASH = 0x1005D4620
SEL_BUNDLEPATH = 0x1005D8EE0
CF_PORTAL_FMT = 0x1004F9948  # @"%@/portalChest"

SITE_COPY = 0x10000B1C0  # MOV x2, x0 after stringWithFormat
SITE_COPY_ORIG = 0xAA0003E2
SITE_SPAWN = 0x100098238  # B back into new-blockhead init
SITE_SPAWN_ORIG = 0x17FFFBBF
SPAWN_CONT = 0x100097134

# Overwrite unused Fabric -init (analytics). Do NOT add a segment after
# LINKEDIT — that is what crashed Sideloadly / dyld on launch.
STUB_VM = 0x1003B083C
STUB_ADD_OFF = 0x200
ITEM_PORTAL_CHEST = 1074


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def p32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


def p64(v):
    return struct.pack("<Q", v & 0xFFFFFFFFFFFFFFFF)


def adrp(rd, pc, target):
    page_diff = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
    immlo = page_diff & 3
    immhi = (page_diff >> 2) & 0x7FFFF
    return 0x90000000 | (immlo << 29) | (immhi << 5) | rd


def add_imm(rd, rn, imm12):
    assert 0 <= imm12 <= 0xFFF
    return 0x91000000 | (imm12 << 10) | (rn << 5) | rd


def ldr64(rt, rn, off):
    assert off % 8 == 0 and 0 <= off <= 0x7FF8
    return 0xF9400000 | ((off // 8) << 10) | (rn << 5) | rt


def bl(pc, target):
    imm = (target - pc) >> 2
    return 0x94000000 | (imm & 0x3FFFFFF)


def b(pc, target):
    imm = (target - pc) >> 2
    return 0x14000000 | (imm & 0x3FFFFFF)


def cbz(rt, pc, target):
    imm19 = (target - pc) >> 2
    return 0xB4000000 | ((imm19 & 0x7FFFF) << 5) | rt


def cbnz(rt, pc, target):
    imm19 = (target - pc) >> 2
    return 0xB5000000 | ((imm19 & 0x7FFFF) << 5) | rt


def mov(rd, rn):
    return 0xAA0003E0 | (rn << 16) | rd


def movz_w(rd, imm):
    return 0x52800000 | ((imm & 0xFFFF) << 5) | rd


def movz_x(rd, imm):
    return 0xD2800000 | ((imm & 0xFFFF) << 5) | rd


def stp_pre(rt, rt2, rn, imm):
    imm7 = (imm // 8) & 0x7F
    return 0xA9800000 | (imm7 << 15) | (rt2 << 10) | (rn << 5) | rt


def ldp_post(rt, rt2, rn, imm):
    imm7 = (imm // 8) & 0x7F
    return 0xA8C00000 | (imm7 << 15) | (rt2 << 10) | (rn << 5) | rt


def stp_off(rt, rt2, rn, imm):
    imm7 = (imm // 8) & 0x7F
    return 0xA9000000 | (imm7 << 15) | (rt2 << 10) | (rn << 5) | rt


def ldp_off(rt, rt2, rn, imm):
    imm7 = (imm // 8) & 0x7F
    return 0xA9400000 | (imm7 << 15) | (rt2 << 10) | (rn << 5) | rt


def ret():
    return 0xD65F03C0


def nop():
    return 0xD503201F


def sub_imm(rd, rn, imm):
    return 0xD1000000 | (imm << 10) | (rn << 5) | rd


def str64(rt, rn, off):
    assert off % 8 == 0 and 0 <= off <= 0x7FF8
    return 0xF9000000 | ((off // 8) << 10) | (rn << 5) | rt


class Asm:
    def __init__(self, base_vm):
        self.base = base_vm
        self.words = []
        self.labels = {}
        self.fixups = []  # (index, kind, label)

    @property
    def pc(self):
        return self.base + len(self.words) * 4

    def label(self, name):
        self.labels[name] = self.pc

    def emit(self, w):
        self.words.append(w & 0xFFFFFFFF)

    def emit_bl(self, target):
        self.emit(bl(self.pc, target))

    def emit_b_label(self, name):
        self.fixups.append((len(self.words), "b", name))
        self.emit(0)

    def emit_cbz_label(self, rt, name):
        self.fixups.append((len(self.words), "cbz", (rt, name)))
        self.emit(0)

    def emit_cbnz_label(self, rt, name):
        self.fixups.append((len(self.words), "cbnz", (rt, name)))
        self.emit(0)

    def resolve(self):
        for idx, kind, arg in self.fixups:
            pc = self.base + idx * 4
            if kind == "b":
                self.words[idx] = b(pc, self.labels[arg])
            elif kind == "cbz":
                rt, name = arg
                self.words[idx] = cbz(rt, pc, self.labels[name])
            elif kind == "cbnz":
                rt, name = arg
                self.words[idx] = cbnz(rt, pc, self.labels[name])

    def bytes(self):
        return b"".join(p32(w) for w in self.words)


def load_sel(asm: Asm, rd_tmp, rt_sel, sel_vm):
    """ADRP+LDR selector into rt_sel using tmp rd_tmp (usually x8)."""
    asm.emit(adrp(rd_tmp, asm.pc, sel_vm))
    asm.emit(ldr64(rt_sel, rd_tmp, sel_vm & 0xFFF))


def load_cls(asm: Asm, rd_tmp, rt_cls, cls_vm):
    load_sel(asm, rd_tmp, rt_cls, cls_vm)


def build_ensure_copy(base):
    """x0 = sandbox NSString path. Copy bundle portalChest there if missing.
    Returns x0 = x2 = sandbox path.
    """
    a = Asm(base)
    # [sp+0x00] vararg slot for stringWithFormat:
    # [sp+0x10] x29, x30
    # [sp+0x20] x19, x20
    # [sp+0x30] x21, x22
    a.emit(sub_imm(31, 31, 0x40))
    a.emit(stp_off(29, 30, 31, 0x10))
    a.emit(stp_off(19, 20, 31, 0x20))
    a.emit(stp_off(21, 22, 31, 0x30))
    a.emit(add_imm(29, 31, 0x10))
    a.emit(mov(19, 0))  # x19 = sandbox path

    # x20 = [NSFileManager defaultManager]
    load_cls(a, 8, 0, NSFILEMANAGER_CLS)
    load_sel(a, 8, 1, SEL_DEFAULTMANAGER)
    a.emit_bl(OBJC_MSGSEND)
    a.emit(mov(20, 0))

    # if ([fm fileExistsAtPath:sandbox]) goto done
    a.emit(mov(0, 20))
    load_sel(a, 8, 1, SEL_FILEEXISTS)
    a.emit(mov(2, 19))
    a.emit_bl(OBJC_MSGSEND)
    a.emit_cbnz_label(0, "done")

    # bundle = [NSBundle mainBundle]
    load_cls(a, 8, 0, NSBUNDLE_CLS)
    load_sel(a, 8, 1, SEL_MAINBUNDLE)
    a.emit_bl(OBJC_MSGSEND)

    # bundlePath
    load_sel(a, 8, 1, SEL_BUNDLEPATH)
    a.emit_bl(OBJC_MSGSEND)
    a.emit(mov(21, 0))  # x21 = bundlePath

    # src = [NSString stringWithFormat:@"%@/portalChest", bundlePath]
    # Match the game: variadic arg on the stack at [sp], and also x3.
    a.emit(str64(21, 31, 0))
    load_cls(a, 8, 0, NSSTRING_CLS)
    load_sel(a, 8, 1, SEL_STRINGWITHFORMAT)
    a.emit(adrp(2, a.pc, CF_PORTAL_FMT))
    a.emit(add_imm(2, 2, CF_PORTAL_FMT & 0xFFF))
    a.emit(mov(3, 21))
    a.emit_bl(OBJC_MSGSEND)
    a.emit_cbz_label(0, "done")
    a.emit(mov(21, 0))  # x21 = src path

    # [fm copyItemAtPath:src toPath:sandbox error:nil]
    a.emit(mov(0, 20))
    load_sel(a, 8, 1, SEL_COPYITEM)
    a.emit(mov(2, 21))
    a.emit(mov(3, 19))
    a.emit(movz_x(4, 0))
    a.emit_bl(OBJC_MSGSEND)

    a.label("done")
    a.emit(mov(0, 19))
    a.emit(mov(2, 19))
    a.emit(ldp_off(19, 20, 31, 0x20))
    a.emit(ldp_off(21, 22, 31, 0x30))
    a.emit(ldp_off(29, 30, 31, 0x10))
    a.emit(add_imm(31, 31, 0x40))
    a.emit(ret())
    a.resolve()
    return a


def build_add_portal(base):
    """New blockhead (x19=self) gets one portal chest in inventory, then
    continues at SPAWN_CONT.
    """
    a = Asm(base)
    a.emit(stp_pre(29, 30, 31, -0x30))
    a.emit(stp_off(19, 20, 31, 0x10))
    # x19 is already the Blockhead — keep it

    # item = [[InventoryItem alloc] initWithType:1074 dataA:0 dataB:0 subItems:nil dict:nil]
    load_cls(a, 8, 0, INVENTORYITEM_CLS)
    load_sel(a, 8, 1, SEL_ALLOC)
    a.emit_bl(OBJC_MSGSEND)
    load_sel(a, 8, 1, SEL_INITWITHTYPE)
    a.emit(movz_w(2, ITEM_PORTAL_CHEST))
    a.emit(movz_w(3, 0))
    a.emit(movz_w(4, 0))
    a.emit(movz_x(5, 0))
    a.emit(movz_x(6, 0))
    a.emit_bl(OBJC_MSGSEND)
    a.emit(mov(20, 0))  # item
    a.emit_cbz_label(20, "out")

    # [blockhead addItemToInventory:item flash:YES]
    a.emit(mov(0, 19))
    load_sel(a, 8, 1, SEL_ADDITEM_FLASH)
    a.emit(mov(2, 20))
    a.emit(movz_w(3, 1))
    a.emit_bl(OBJC_MSGSEND)

    a.label("out")
    a.emit(ldp_off(19, 20, 31, 0x10))
    a.emit(ldp_post(29, 30, 31, 0x30))
    # tail to original continuation (not a ret — we replaced a B)
    a.emit(b(a.pc, SPAWN_CONT))
    a.resolve()
    return a


def verify_sites(data: bytes):
    fo_copy = SITE_COPY - 0x100000000
    fo_spawn = SITE_SPAWN - 0x100000000
    got_copy = u32(data, fo_copy)
    got_spawn = u32(data, fo_spawn)
    if got_copy != SITE_COPY_ORIG:
        raise SystemExit(f"copy site mismatch: {got_copy:08x} expected {SITE_COPY_ORIG:08x}")
    if got_spawn != SITE_SPAWN_ORIG:
        raise SystemExit(f"spawn site mismatch: {got_spawn:08x} expected {SITE_SPAWN_ORIG:08x}")
    print("sites match original bytes")


def patch_binary():
    raw = bytearray(BIN.read_bytes())
    verify_sites(raw)

    ensure = build_ensure_copy(STUB_VM)
    addp = build_add_portal(STUB_VM + 0x200)
    print(f"ensure_copy {len(ensure.words)} insns @ {STUB_VM:#x}")
    print(f"add_portal  {len(addp.words)} insns @ {STUB_VM+0x200:#x}")
    for i, w in enumerate(ensure.words):
        print(f"  E {STUB_VM+i*4:#x}: {w:08x}")
    for i, w in enumerate(addp.words):
        print(f"  A {STUB_VM+0x200+i*4:#x}: {w:08x}")

    eb = ensure.bytes()
    ab = addp.bytes()
    if len(eb) > STUB_ADD_OFF:
        raise SystemExit("ensure_copy stub overlaps add_portal")
    fo_stub = STUB_VM - 0x100000000
    raw[fo_stub : fo_stub + len(eb)] = eb
    raw[fo_stub + STUB_ADD_OFF : fo_stub + STUB_ADD_OFF + len(ab)] = ab

    raw[SITE_COPY - 0x100000000 : SITE_COPY - 0x100000000 + 4] = p32(bl(SITE_COPY, STUB_VM))
    raw[SITE_SPAWN - 0x100000000 : SITE_SPAWN - 0x100000000 + 4] = p32(
        b(SITE_SPAWN, STUB_VM + STUB_ADD_OFF)
    )

    BIN.write_bytes(raw)
    print("patched binary", BIN, "size", len(raw), "(in-place, no extra segment)")
    print(f"  {SITE_COPY:#x} BL {STUB_VM:#x}")
    print(f"  {SITE_SPAWN:#x} B  {STUB_VM+STUB_ADD_OFF:#x}")


def ensure_portal_file():
    dest = APP / "portalChest"
    if PORTAL_SRC.exists():
        if (not dest.exists()) or dest.stat().st_size != PORTAL_SRC.stat().st_size:
            shutil.copy2(PORTAL_SRC, dest)
    if not dest.exists():
        raise SystemExit("portalChest missing in app bundle")
    gr = APP / "GameResources" / "portalChest"
    if not gr.exists():
        shutil.copy2(dest, gr)
    print("portalChest in bundle", dest.stat().st_size, "bytes")


SKIP_PARTS = (
    "/_CodeSignature/",
    "/SC_Info/",
    "/CrackerXI",
    "embedded.mobileprovision",
)


def rebuild_ipa():
    payload = APP.parent
    if OUT_IPA.exists():
        OUT_IPA.unlink()
    with zipfile.ZipFile(OUT_IPA, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in payload.rglob("*"):
            if not p.is_file():
                continue
            arc = p.relative_to(payload.parent).as_posix()
            if any(s in f"/{arc}" for s in SKIP_PARTS):
                continue
            z.write(p, arc)
    print("wrote", OUT_IPA, OUT_IPA.stat().st_size, "(unsigned, for Sideloadly)")


def restore_binary_from_ipa():
    """Extract the original IPA Payload, then restore the untouched binary."""
    work = APP.parent  # .../Payload
    root = work.parent
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ORIG_IPA, "r") as z:
        names = [n for n in z.namelist() if n.startswith("Payload/") and not n.endswith("/")]
        if not any(n.endswith("Blockheads.app/Blockheads") for n in names):
            raise SystemExit("original IPA has no Blockheads binary")
        for n in names:
            dest = root / n
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(n))
    print("extracted Payload from", ORIG_IPA.name, "bin", BIN.stat().st_size)


def main():
    import argparse

    global ORIG_IPA, OUT_IPA, PORTAL_SRC, APP, BIN
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipa", type=Path, default=ORIG_IPA)
    ap.add_argument("--portal", type=Path, default=PORTAL_SRC)
    ap.add_argument("--out", type=Path, default=OUT_IPA)
    ap.add_argument("--app", type=Path, default=APP)
    args = ap.parse_args()
    ORIG_IPA, PORTAL_SRC, OUT_IPA = args.ipa, args.portal, args.out
    APP = args.app
    BIN = APP / "Blockheads"
    APP.mkdir(parents=True, exist_ok=True)
    restore_binary_from_ipa()
    ensure_portal_file()
    patch_binary()
    rebuild_ipa()


if __name__ == "__main__":
    main()
