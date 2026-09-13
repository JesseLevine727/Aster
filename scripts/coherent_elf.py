"""Small read-only ELF32/RISC-V audit: boot bytes and actual benchmark symbols.

No executable named by an untrusted capture is invoked. This is deliberately
stricter than the generic firmware converter, and independent of nm output.
"""
import struct

from asterbench_coherent import require


def inspect_elf(data, *, profile="benchmark", dma_size=None, dma_jobs=None):
    require(profile in ("benchmark", "runtime", "lifecycle", "dma_benchmark", "dma_runtime", "dma_publication", "dma_stop_fixture"), "unknown audited firmware profile")
    if profile == "dma_benchmark":
        require(type(dma_size) is int and 0 <= dma_size <= 8192 and type(dma_jobs) is int and 1 <= dma_jobs <= 8,
                "DMA benchmark requires exact size/job symbol contract")
    else:
        require(dma_size is None and dma_jobs is None, "unexpected DMA benchmark arguments for another ELF profile")
    require(type(data) is bytes and 52 <= len(data) <= 16*1024*1024, "invalid ELF length")
    h = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    require(h[0][:7] == b"\x7fELF\x01\x01\x01" and h[1:5] == (2, 243, 1, 0) and h[7] == 0,
            "expected uncompressed soft-float ELF32 RISC-V executable at reset vector zero")
    require(h[8] == 52 and h[9] == 32 and 1 <= h[10] <= 32 and h[11] == 40 and 1 <= h[12] <= 4096,
            "unsupported ELF table format")

    def region(offset, size):
        require(0 <= offset <= offset+size <= len(data), "ELF region outside file")
        return data[offset:offset+size]

    region(h[5], h[9]*h[10]); region(h[6], h[11]*h[12])
    image = bytearray(65536); populated = bytearray(65536); loads = 0
    for i in range(h[10]):
        kind, offset, _virtual, physical, size, memory_size, _flags, _align = struct.unpack_from("<8I", data, h[5]+i*32)
        require(kind != 3, "dynamic interpreter is outside this bare-metal ABI")
        if kind != 1 or size == 0:
            continue
        require(size <= memory_size and physical+size <= 65536 and not any(populated[physical:physical+size]),
                "overlapping/oversized ROM load segment")
        image[physical:physical+size] = region(offset, size)
        populated[physical:physical+size] = b"\x01"*size; loads += 1
    require(loads and populated[0:4] == b"\x01"*4, "missing reset vector load")
    sections = [struct.unpack_from("<10I", data, h[6]+i*40) for i in range(h[12])]
    wanted = {"aster_coherent_kernel": (2, 0, 65536),
              "aster_coherent_output": (1, 0x10000000, 0x10008000),
              "aster_coherent_results": (1, 0x10008000, 0x1000b000)}
    sizes = {}
    if profile == "dma_benchmark":
        allocation = (dma_size+194) & ~63
        wanted = {"aster_dma_cpu_memcpy": (2, 0, 65536), "aster_dma_copy": (2, 0, 65536),
                  "aster_dma_submit": (2, 0, 65536), "aster_dma_poll": (2, 0, 65536),
                  "aster_dma_bench_source": (1, 0x10001000, 0x10001000+allocation),
                  "aster_dma_bench_destination": (1, 0x10005080, 0x10005080+allocation),
                  "aster_dma_bench_results": (1, 0x10008000, 0x1000b000)}
        sizes = {"aster_dma_bench_source": allocation, "aster_dma_bench_destination": allocation,
                 "aster_dma_bench_results": dma_jobs*2*108*4}
    elif profile == "dma_runtime":
        wanted = {"main": (2, 0, 65536), "aster_secondary_main": (2, 0, 65536),
                  "aster_dma_copy": (2, 0, 65536), "aster_dma_submit": (2, 0, 65536),
                  "aster_dma_poll": (2, 0, 65536), "aster_dma_abort_and_wait": (2, 0, 65536),
                  "source": (1, 0x10000000, 0x10008000), "destination": (1, 0x10000000, 0x10008000),
                  "dma_results": (1, 0x10008000, 0x1000b000)}
        sizes = {"source": 1152, "destination": 1152, "dma_results": 512}
    elif profile == "dma_publication":
        wanted = {"main": (2, 0, 65536), "aster_secondary_main": (2, 0, 65536), "execute_code": (2, 0, 65536),
                  "aster_dma_copy": (2, 0, 65536), "aster_dma_submit": (2, 0, 65536), "aster_dma_poll": (2, 0, 65536),
                  "staging": (1, 0x10000000, 0x10008000), "code": (1, 0x10000000, 0x10008000),
                  "publication_results": (1, 0x10008000, 0x1000b000)}
        sizes = {"staging": 64, "code": 64, "publication_results": 512}
    elif profile == "dma_stop_fixture":
        wanted = {"main": (2, 0, 65536), "aster_secondary_main": (2, 0, 65536), "aster_dma_submit": (2, 0, 65536),
                  "source": (1, 0x10000000, 0x10008000), "destination": (1, 0x10000000, 0x10008000)}
        sizes = {"source": 384, "destination": 384}
    elif profile != "benchmark":
        wanted = {"main": (2, 0, 65536), "aster_secondary_main": (2, 0, 65536)}
        sizes = ({"probe_results": 32, "directed_word": 4, "counter": 4, "cas_counter": 4,
                  "lrsc_counter": 4, "protected_counter": 4, "protected_sum": 4, "lock_word": 4,
                  "start_epoch": 4, "done": 8} if profile == "runtime" else
                 {"requested_epoch": 4, "done_epoch": 4, "generations": 4, "worker_error": 4,
                  "primary_reservation": 4, "secondary_reservation": 4, "payload": 256,
                  "primary_private": 64, "secondary_private": 64})
        for name in sizes:
            low, high = ((0x10008000, 0x1000b000) if name in ("probe_results", "primary_private") else
                         (0x1000c000, 0x1000f000) if name == "secondary_private" else (0x10000000, 0x10008000))
            wanted[name] = (1, low, high)
    found = {}
    for s in sections:
        if s[1] != 2:  # SHT_SYMTAB, not a dynamic-symbol substitute.
            continue
        require(s[9] == 16 and s[5] % 16 == 0 and s[6] < len(sections), "invalid symbol table")
        strings = sections[s[6]]
        require(strings[1] == 3, "symbol names are not a string table")
        names = region(strings[4], strings[5]); region(s[4], s[5])
        for offset in range(s[4], s[4]+s[5], 16):
            name, value, size, info, _other, index = struct.unpack_from("<IIIBBH", data, offset)
            require(name < len(names), "symbol name outside table")
            end = names.find(b"\0", name); require(end >= 0, "unterminated symbol name")
            text = names[name:end].decode("ascii")
            if text not in wanted:
                continue
            kind, low, high = wanted[text]
            # The startup assembly has a local untyped branch label named
            # primary_private, distinct from the lifecycle C data object.
            if profile != "benchmark" and info & 15 == 0:
                continue
            require(text not in found and info & 15 == kind and 0 < index < len(sections) and
                    value % 4 == 0 and low <= value < value+size <= high and
                    (text not in sizes or size == sizes[text]), "duplicate/invalid firmware symbol")
            owner = sections[index]
            require(owner[2] & 2 and owner[3] <= value < value+size <= owner[3]+owner[5], "symbol outside allocated section")
            if kind == 2:
                require(owner[2] & 4 and owner[1] == 1 and all(populated[value:value+size]), "kernel lacks loaded executable bytes")
            else:
                require(owner[2] & 1 and owner[1] == 8, "result/output must be writable NOLOAD RAM")
            found[text] = {"address": value, "size": size}
    require(set(found) == set(wanted), "missing actual ELF benchmark symbols")
    if profile.startswith("dma_"):
        allocations = sorted((found[name]["address"], found[name]["address"]+found[name]["size"])
                             for name, (kind, _low, _high) in wanted.items() if kind == 1)
        require(all(left[1] <= right[0] for left, right in zip(allocations, allocations[1:])), "overlapping DMA firmware allocations")
    return {"symbols": found, "image": bytes(image)}
