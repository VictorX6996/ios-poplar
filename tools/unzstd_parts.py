#!/usr/bin/env python3
"""Decompress split zstd parts of ios16-root.simg into an Android sparse image."""
import glob, os, sys, time, zstandard as zstd


class MultiFileReader:
    def __init__(self, filenames):
        self.filenames = filenames
        self.idx = 0
        self.current = open(self.filenames[0], 'rb')

    def read(self, size=-1):
        if self.idx >= len(self.filenames):
            return b''
        chunk = self.current.read(size)
        if not chunk:
            self.current.close()
            self.idx += 1
            if self.idx < len(self.filenames):
                self.current = open(self.filenames[self.idx], 'rb')
                return self.read(size)
            return b''
        return chunk


def main():
    parts = sorted(glob.glob('ios16-root-simg/ios16-root.simg.zst.part-*'))
    if not parts:
        sys.exit('No part files found in ios16-root-simg/')

    total_in_bytes = sum(os.path.getsize(p) for p in parts)
    out_path = 'ios16-root.simg'
    print(f'Decompressing {len(parts)} parts ({total_in_bytes / 1e9:.2f} GB compressed) -> {out_path}...')

    t0 = time.time()
    reader = MultiFileReader(parts)
    dctx = zstd.ZstdDecompressor()
    bytes_written = 0

    with open(out_path, 'wb') as out_f:
        with dctx.stream_reader(reader) as z_stream:
            chunk_size = 16 * 1024 * 1024
            while True:
                chunk = z_stream.read(chunk_size)
                if not chunk:
                    break
                out_f.write(chunk)
                bytes_written += len(chunk)
                elapsed = time.time() - t0
                speed = bytes_written / (1024 * 1024) / max(elapsed, 0.001)
                print(f'\rWritten: {bytes_written / (1024*1024):.1f} MB ({speed:.1f} MB/s)', end='', flush=True)

    print(f'\nDone in {time.time() - t0:.1f}s: {out_path} ({bytes_written} bytes)')


if __name__ == '__main__':
    main()
