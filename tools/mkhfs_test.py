#!/usr/bin/env python3
"""Create a minimal case-sensitive HFS+ volume ('SweetRoot') with /sbin/launchd."""
import struct, sys


def be16(x): return struct.pack('>H', x)
def be32(x): return struct.pack('>I', x)
def be64(x): return struct.pack('>Q', x)


def u16_str(s):
    b = s.encode('utf-16-be')
    return be16(len(s)) + b


def make_bnode(kind, height, num_recs, records, node_size=4096):
    desc = struct.pack('>IIbBHI', 0, 0, kind, height, num_recs, 0)
    body = bytearray(desc)
    offsets = [14]
    for r in records:
        body.extend(r)
        if len(body) & 1:
            body.append(0)
        offsets.append(len(body))
    assert len(body) + 2 * len(offsets) <= node_size
    out = bytearray(node_size)
    out[:len(body)] = body
    for i, off in enumerate(offsets):
        struct.pack_into('>H', out, node_size - 2 * (i + 1), off)
    return bytes(out)


def make_header_rec(depth, root, leaf_recs, first_leaf, last_leaf, node_size, total_nodes, free_nodes, key_cmp=0xBC):
    hdr = struct.pack(
        '>HIIIIHHHIIHBBI',
        depth, root, leaf_recs, first_leaf, last_leaf,
        node_size, 516, total_nodes, free_nodes,
        0, 4096, 0, key_cmp, 0x00000006
    ) + b'\x00' * 64
    user = b'\x00' * 128
    bmap = bytearray(node_size - 256)
    used = total_nodes - free_nodes
    for i in range(used):
        bmap[i >> 3] |= 0x80 >> (i & 7)
    return [hdr, user, bytes(bmap)]


def cat_key(parent_id, name):
    body = be32(parent_id) + u16_str(name)
    return be16(len(body)) + body


def cat_folder(cnid, valence, mode=0o040755):
    return struct.pack(
        '>hHIUUUUUUUUHHHHII',
        1, 0, valence, cnid, 0, 0, 0, 0,
        0, 0, 0, mode, 1, 0, 0
    ) if False else (
        struct.pack('>hHIIIII', 1, 0, valence, cnid, 0, 0, 0)
        + struct.pack('>II', 0, 0)
        + struct.pack('>IIII', 0, 0, mode, 1)
        + b'\x00' * 32
    )


def cat_file(cnid, data_bytes, start_block, nblocks, mode=0o100755):
    fork = be64(data_bytes) + be32(0) + be32(nblocks) + struct.pack('>II', start_block, nblocks) + b'\x00' * 56
    rfork = b'\x00' * 80
    return (
        struct.pack('>hHIIIII', 2, 2, 0, cnid, 0, 0, 0)
        + struct.pack('>II', 0, 0)
        + struct.pack('>IIII', 0, 0, mode, 1)
        + b'\x00' * 32
        + fork + rfork
    )


def cat_thread(rec_type, parent_id, name):
    return struct.pack('>hHI', rec_type, 0, parent_id) + u16_str(name)


def build_hfs(init_bin, out_path):
    block_size = 4096
    total_blocks = 2048  # 8 MiB
    img = bytearray(total_blocks * block_size)

    # Allocation layout (4 KiB blocks):
    # 0: boot + volume header (at offset 1024)
    # 1: allocation bitmap (1 block = 32768 bits)
    # 2..3: extents overflow B-tree (2 nodes)
    # 4..5: catalog B-tree (node 0 = header, node 1 = leaf)
    # 6..6+N-1: /sbin/launchd file data
    # total_blocks - 1: alternate volume header
    init_blocks = (len(init_bin) + block_size - 1) // block_size
    init_start = 6
    img[init_start * block_size : init_start * block_size + len(init_bin)] = init_bin

    used_blocks = init_start + init_blocks
    alloc_bmap = bytearray(block_size)
    for b in range(used_blocks):
        alloc_bmap[b >> 3] |= 0x80 >> (b & 7)
    # Also mark last block (alt volume header)
    alloc_bmap[(total_blocks - 1) >> 3] |= 0x80 >> ((total_blocks - 1) & 7)
    img[1 * block_size : 2 * block_size] = alloc_bmap

    # Extents B-tree (empty header node at block 2)
    ext_hdr_recs = make_header_rec(0, 0, 0, 0, 0, 4096, 2, 1, key_cmp=0)
    img[2 * block_size : 3 * block_size] = make_bnode(1, 0, 3, ext_hdr_recs)

    # Catalog B-tree records sorted by (parent_id, case-sensitive name):
    # CNIDs: 1=parent of root, 2=root ('SweetRoot'), 16='sbin', 17='launchd'
    recs = [
        cat_key(1, 'SweetRoot') + cat_folder(2, 1),
        cat_key(2, '') + cat_thread(3, 1, 'SweetRoot'),
        cat_key(2, 'sbin') + cat_folder(16, 1),
        cat_key(16, '') + cat_thread(3, 2, 'sbin'),
        cat_key(16, 'launchd') + cat_file(17, len(init_bin), init_start, init_blocks),
        cat_key(17, '') + cat_thread(4, 16, 'launchd'),
    ]
    cat_hdr_recs = make_header_rec(1, 1, len(recs), 1, 1, 4096, 2, 0, key_cmp=0xBC)
    img[4 * block_size : 5 * block_size] = make_bnode(1, 0, 3, cat_hdr_recs)
    img[5 * block_size : 6 * block_size] = make_bnode(-1, 1, len(recs), recs)

    def fork_data(logical, start, count, clump=4096):
        return be64(logical) + be32(clump) + be32(count) + struct.pack('>II', start, count) + b'\x00' * 56

    vh = bytearray(512)
    struct.pack_into(
        '>2sHHIIIIIIIIIIIIIIIIII',
        vh, 0,
        b'HX', 5,
        0x00000100,  # kHFSVolumeUnmountedBit
        0x4846534A,  # lastMountedVersion 'HFSJ'
        0,
        0, 0, 0, 0,
        1,           # fileCount (/sbin/launchd)
        1,           # folderCount (/sbin; excluding root)
        block_size,
        total_blocks,
        total_blocks - used_blocks - 1,
        used_blocks,
        65536, 65536,
        18,          # nextCatalogID
        1,           # writeCount
        1, 0
    )
    vh[80:112] = b'\x00' * 24 + b'sweetrot'
    vh[112:192] = fork_data(block_size, 1, 1)       # allocationFile
    vh[192:272] = fork_data(2 * block_size, 2, 2)   # extentsFile
    vh[272:352] = fork_data(2 * block_size, 4, 2)   # catalogFile
    vh[352:432] = b'\x00' * 80                      # attributesFile
    vh[432:512] = b'\x00' * 80                      # startupFile

    img[1024:1536] = vh
    img[(total_blocks - 1) * block_size + (block_size - 1024) : (total_blocks - 1) * block_size + (block_size - 512)] = vh
    open(out_path, 'wb').write(img)
    print(f'{out_path}: {len(img)} bytes (init={len(init_bin)} bytes in {init_blocks} blocks)')


if __name__ == '__main__':
    build_hfs(open(sys.argv[1], 'rb').read(), sys.argv[2])
