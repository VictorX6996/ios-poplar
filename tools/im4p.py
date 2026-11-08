#!/usr/bin/env python3
"""Extract an LZFSE-compressed (bvx2/bvx1/bvx-) IM4P payload (e.g. DeviceTree.d22ap.im4p)."""
import struct, sys

L_EXTRA = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0, 2,3,5,8]
L_BASE = [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15, 16,20,28,60]
M_EXTRA = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0, 3,5,8,11]
M_BASE = [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15, 16,24,56,312]
D_EXTRA = [i // 4 for i in range(64)]
D_BASE = [0] * 64
for _i in range(1, 64):
    D_BASE[_i] = D_BASE[_i - 1] + (1 << D_EXTRA[_i - 1])

FREQ_NB = [2,3,2,5,2,3,2,8,2,3,2,5,2,3,2,14, 2,3,2,5,2,3,2,8,2,3,2,5,2,3,2,14]
FREQ_VAL = [0,2,1,4,0,3,1,-1,0,2,1,5,0,3,1,-1, 0,2,1,6,0,3,1,-1,0,2,1,7,0,3,1,-1]


def init_fse_decoder(nstates, freqs):
    table = []
    for sym, f in enumerate(freqs):
        if f == 0:
            continue
        k = nstates.bit_length() - f.bit_length()
        j0 = ((2 * nstates) >> k) - f
        for i in range(f):
            if i < j0:
                table.append((k, sym, ((f + i) << k) - nstates))
            else:
                table.append((k - 1, sym, (i - j0) << (k - 1)))
    assert len(table) == nstates, (len(table), nstates)
    return table


def init_value_fse_decoder(nstates, freqs, vbits, vbase):
    table = []
    for sym, f in enumerate(freqs):
        if f == 0:
            continue
        k = nstates.bit_length() - f.bit_length()
        j0 = ((2 * nstates) >> k) - f
        vb, vbs = vbits[sym], vbase[sym]
        for i in range(f):
            if i < j0:
                table.append((k, vb, ((f + i) << k) - nstates, vbs))
            else:
                table.append((k - 1, vb, (i - j0) << (k - 1), vbs))
    assert len(table) == nstates, (len(table), nstates)
    return table


class ForwardBitStream:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.accum = 0
        self.nbits = 0

    def pull(self, n):
        while self.nbits < n:
            b = self.data[self.pos] if self.pos < len(self.data) else 0
            self.pos += 1
            self.accum |= b << self.nbits
            self.nbits += 8
        v = self.accum & ((1 << n) - 1)
        self.accum >>= n
        self.nbits -= n
        return v


class BackwardBitStream:
    def __init__(self, buf, offset, nbits_in_last):
        self.buf = buf
        self.pos = offset
        self.accum = 0
        self.nbits = 0
        if nbits_in_last < 0:
            self.pos += (-nbits_in_last) // 8
            nbits_in_last = -((-nbits_in_last) & 7)
        self.fill()
        if nbits_in_last > 0:
            self.nbits -= 8 - nbits_in_last
            self.accum &= (1 << self.nbits) - 1
        elif nbits_in_last < 0:
            self.nbits -= -nbits_in_last
            self.accum &= (1 << self.nbits) - 1

    def fill(self):
        while self.nbits <= 56 and self.pos > 0:
            self.pos -= 1
            self.accum = (self.accum << 8) | self.buf[self.pos]
            self.nbits += 8
        while self.nbits <= 56:
            self.accum <<= 8
            self.nbits += 8

    def pull(self, n):
        if n == 0:
            return 0
        self.nbits -= n
        v = (self.accum >> self.nbits) & ((1 << n) - 1)
        self.accum &= (1 << self.nbits) - 1
        return v


def decode_bvx1_or_2(buf, pos, is_v2, out):
    start_len = len(out)
    if is_v2:
        n_raw, p0, p1, p2 = struct.unpack_from('<IQQQ', buf, pos + 4)
        n_literals = p0 & 0xfffff
        n_lit_payload = (p0 >> 20) & 0xfffff
        n_matches = (p0 >> 40) & 0xfffff
        lit_bits = ((p0 >> 60) & 7) - 7
        s_lit0 = (p1 >> 0) & 0x3ff
        s_lit1 = (p1 >> 10) & 0x3ff
        s_lit2 = (p1 >> 20) & 0x3ff
        s_lit3 = (p1 >> 30) & 0x3ff
        n_lmd_payload = (p1 >> 40) & 0xfffff
        lmd_bits = ((p1 >> 60) & 7) - 7
        header_size = (p2 >> 0) & 0xffffffff
        s_l = (p2 >> 32) & 0x3ff
        s_m = (p2 >> 42) & 0x3ff
        s_d = (p2 >> 52) & 0x3ff
        fbs = ForwardBitStream(buf[pos + 32:pos + header_size])
        all_freqs = []
        for _ in range(20 + 20 + 64 + 256):
            while fbs.nbits < 14:
                b = fbs.data[fbs.pos] if fbs.pos < len(fbs.data) else 0
                fbs.pos += 1
                fbs.accum |= b << fbs.nbits
                fbs.nbits += 8
            bits14 = fbs.accum & 0x3fff
            nb = FREQ_NB[bits14 & 0x1f]
            val = FREQ_VAL[bits14 & 0x1f]
            if nb == 8:
                val = 8 + ((bits14 >> 4) & 0xf)
            elif nb == 14:
                val = 24 + ((bits14 >> 4) & 0x3ff)
            fbs.accum >>= nb
            fbs.nbits -= nb
            all_freqs.append(val)
        l_freq = all_freqs[0:20]
        m_freq = all_freqs[20:40]
        d_freq = all_freqs[40:104]
        lit_freq = all_freqs[104:360]
        payload_start = pos + header_size
    else:
        hdr = struct.unpack_from('<7I7H', buf, pos + 4)
        n_raw, n_payload, n_literals, n_matches, n_lit_payload, n_lmd_payload, lit_bits = hdr[:7]
        s_lit0, s_lit1, s_lit2, s_lit3, lmd_bits, s_l, s_m, s_d = (
            hdr[7], hdr[8], hdr[9], hdr[10], struct.unpack_from('<i', buf, pos + 32 + 8)[0],
            hdr[11], hdr[12], hdr[13])
        raise NotImplementedError('bvx1')

    lit_tab = init_fse_decoder(1024, lit_freq)
    l_tab = init_value_fse_decoder(64, l_freq, L_EXTRA, L_BASE)
    m_tab = init_value_fse_decoder(64, m_freq, M_EXTRA, M_BASE)
    d_tab = init_value_fse_decoder(256, d_freq, D_EXTRA, D_BASE)

    lit_bs = BackwardBitStream(buf[payload_start:payload_start + n_lit_payload], n_lit_payload, lit_bits)
    literals = bytearray()
    for _ in range(0, n_literals, 4):
        lit_bs.fill()
        k, sym, delta = lit_tab[s_lit0]
        s_lit0 = delta + lit_bs.pull(k)
        literals.append(sym)
        k, sym, delta = lit_tab[s_lit1]
        s_lit1 = delta + lit_bs.pull(k)
        literals.append(sym)
        k, sym, delta = lit_tab[s_lit2]
        s_lit2 = delta + lit_bs.pull(k)
        literals.append(sym)
        k, sym, delta = lit_tab[s_lit3]
        s_lit3 = delta + lit_bs.pull(k)
        literals.append(sym)

    lmd_start = payload_start + n_lit_payload
    lmd_bs = BackwardBitStream(buf[lmd_start:lmd_start + n_lmd_payload], n_lmd_payload, lmd_bits)
    lit_idx = 0
    prev_d = -1
    for _ in range(n_matches):
        lmd_bs.fill()
        k, vb, delta, vbase = l_tab[s_l]
        s_l = delta + lmd_bs.pull(k)
        l_val = vbase + lmd_bs.pull(vb)

        lmd_bs.fill()
        k, vb, delta, vbase = m_tab[s_m]
        s_m = delta + lmd_bs.pull(k)
        m_val = vbase + lmd_bs.pull(vb)

        lmd_bs.fill()
        k, vb, delta, vbase = d_tab[s_d]
        s_d = delta + lmd_bs.pull(k)
        d_val = vbase + lmd_bs.pull(vb)
        if d_val == 0:
            d_val = prev_d
        else:
            prev_d = d_val

        out.extend(literals[lit_idx:lit_idx + l_val])
        lit_idx += l_val
        for _ in range(m_val):
            out.append(out[-d_val])

    rem = n_raw - (len(out) - start_len)
    assert 0 <= rem <= n_literals - lit_idx < rem + 4, (rem, n_literals - lit_idx)
    out.extend(literals[lit_idx:lit_idx + rem])
    assert len(out) - start_len == n_raw, (len(out) - start_len, n_raw)
    return lmd_start + n_lmd_payload


def decompress_lzfse(buf):
    pos = 0
    out = bytearray()
    while pos < len(buf):
        magic = buf[pos:pos + 4]
        if magic == b'bvx$':
            break
        elif magic == b'bvx2':
            pos = decode_bvx1_or_2(buf, pos, True, out)
        elif magic == b'bvx-':
            n_raw, = struct.unpack_from('<I', buf, pos + 4)
            out.extend(buf[pos + 8:pos + 8 + n_raw])
            pos += 8 + n_raw
        else:
            raise ValueError(f'unknown block magic {magic!r} at {pos:#x}')
    return bytes(out)


if __name__ == '__main__':
    raw = open(sys.argv[1], 'rb').read()
    idx = raw.find(b'bvx2')
    assert idx >= 0, 'no bvx2 LZFSE payload found in IM4P'
    adt_bytes = decompress_lzfse(raw[idx:])
    open(sys.argv[2], 'wb').write(adt_bytes)
    print(f'{sys.argv[2]}: {len(adt_bytes)} bytes')
