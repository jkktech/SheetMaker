"""Write rsrc_windows_amd64.syso: a COFF object with a .rsrc section (icon group + version info)
that the Go linker puts into the exe. Usage: python make_syso.py sheetmaker.ico out.syso"""
import struct
import sys

RT_ICON, RT_GROUP_ICON, RT_VERSION, RT_MANIFEST = 3, 14, 16, 24
LANG = 0x0409

MANIFEST = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <assemblyIdentity type="win32" name="SheetMaker.Launcher" version="1.0.0.0"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security><requestedPrivileges><requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security>
  </trustInfo>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings><dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true</dpiAware></windowsSettings>
  </application>
</assembly>
"""


def read_ico(path):
    d = open(path, "rb").read()
    _, typ, n = struct.unpack_from("<HHH", d, 0)
    assert typ == 1
    out = []
    for i in range(n):
        w, h, cc, _, planes, bpp, size, off = struct.unpack_from("<BBBBHHII", d, 6 + 16 * i)
        out.append(((w, h, cc, planes, bpp), d[off:off + size]))
    return out


def version_info(ver=(1, 0, 0, 0)):
    def s16(x):
        return x.encode("utf-16-le") + b"\0\0"

    def block(key, value=b"", children=b"", typ=1, vlen=None):
        k = s16(key)
        head_len = 6 + len(k)
        pad1 = (4 - head_len % 4) % 4
        body = value + b"\0" * ((4 - len(value) % 4) % 4 if children else 0) + children
        vl = vlen if vlen is not None else (len(value) // 2 if typ == 1 else len(value))
        total = head_len + pad1 + len(body)
        return struct.pack("<HHH", total, vl, typ) + k + b"\0" * pad1 + body

    def pad4(b):
        return b + b"\0" * ((4 - len(b) % 4) % 4)

    strings = b""
    for k, v in (("CompanyName", "SheetMaker"), ("FileDescription", "SheetMaker"), ("FileVersion", "1.0.0.0"),
                 ("InternalName", "SheetMaker"), ("OriginalFilename", "SheetMaker.exe"),
                 ("ProductName", "SheetMaker"), ("ProductVersion", "1.0.0.0")):
        strings += pad4(block(k, s16(v)))
    table = block("040904B0", children=strings, vlen=0)
    sfi = block("StringFileInfo", children=pad4(table), vlen=0)
    var = block("VarFileInfo", children=block("Translation", struct.pack("<HH", 0x0409, 0x04B0), typ=0), vlen=0)
    ffi = struct.pack("<13I", 0xFEEF04BD, 0x00010000, (ver[0] << 16) | ver[1], (ver[2] << 16) | ver[3],
                      (ver[0] << 16) | ver[1], (ver[2] << 16) | ver[3], 0x3F, 0, 0x40004, 1, 0, 0, 0)
    return block("VS_VERSION_INFO", ffi, pad4(sfi) + pad4(var), typ=0, vlen=len(ffi))


def build(ico_path, out_path):
    icons = read_ico(ico_path)
    # resources: {type: {id: data}}
    res = {RT_ICON: {}, RT_GROUP_ICON: {}, RT_VERSION: {1: version_info()}, RT_MANIFEST: {1: MANIFEST}}
    grp = struct.pack("<HHH", 0, 1, len(icons))
    for i, ((w, h, cc, planes, bpp), data) in enumerate(icons, 1):
        res[RT_ICON][i] = data
        grp += struct.pack("<BBBBHHIH", w, h, cc, 0, planes, bpp, len(data), i)
    res[RT_GROUP_ICON][1] = grp

    # layout: root dir, type dirs, name dirs, data entries, then data
    types = sorted(res)
    dir_size = lambda n: 16 + 8 * n
    off = dir_size(len(types))
    type_off, name_off = {}, {}
    for t in types:
        type_off[t] = off
        off += dir_size(len(res[t]))
    for t in types:
        for i in sorted(res[t]):
            name_off[(t, i)] = off
            off += dir_size(1)
    entry_off = {}
    for t in types:
        for i in sorted(res[t]):
            entry_off[(t, i)] = off
            off += 16
    data_off = {}
    for t in types:
        for i in sorted(res[t]):
            off = (off + 7) & ~7
            data_off[(t, i)] = off
            off += len(res[t][i])
    size = (off + 7) & ~7
    sec = bytearray(size)

    def put_dir(at, entries):
        struct.pack_into("<IIHHHH", sec, at, 0, 0, 0, 0, 0, len(entries))
        for k, (ident, target) in enumerate(entries):
            struct.pack_into("<II", sec, at + 16 + 8 * k, ident, target)

    put_dir(0, [(t, 0x80000000 | type_off[t]) for t in types])
    for t in types:
        put_dir(type_off[t], [(i, 0x80000000 | name_off[(t, i)]) for i in sorted(res[t])])
        for i in sorted(res[t]):
            put_dir(name_off[(t, i)], [(LANG, entry_off[(t, i)])])
    relocs = []
    for t in types:
        for i in sorted(res[t]):
            e = entry_off[(t, i)]
            struct.pack_into("<IIII", sec, e, data_off[(t, i)], len(res[t][i]), 0, 0)
            relocs.append(e)                              # OffsetToData needs the section's RVA added
            sec[data_off[(t, i)]:data_off[(t, i)] + len(res[t][i])] = res[t][i]

    # COFF object
    hdr_size, sh_size = 20, 40
    raw_ptr = hdr_size + sh_size
    rel_ptr = raw_ptr + size
    sym_ptr = rel_ptr + 10 * len(relocs)
    out = bytearray()
    out += struct.pack("<HHIIIHH", 0x8664, 1, 0, sym_ptr, 1, 0, 0)
    out += struct.pack("<8sIIIIIIHHI", b".rsrc", 0, 0, size, raw_ptr, rel_ptr, 0, len(relocs), 0, 0x40000040)
    out += sec
    for r in relocs:
        out += struct.pack("<IIH", r, 0, 3)           # IMAGE_REL_AMD64_ADDR32NB against symbol 0 (.rsrc)
    out += struct.pack("<8sIhHBB", b".rsrc", 0, 1, 0, 3, 0)   # section symbol, static
    out += struct.pack("<I", 4)                        # empty string table
    open(out_path, "wb").write(out)
    print(f"{out_path}: {len(out)} bytes, {len(icons)} icons")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
