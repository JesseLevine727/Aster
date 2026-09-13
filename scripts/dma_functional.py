"""Independent RAM/counter contracts for real DMA runtime and code publication.

Functional firmware exercises two active harts and includes serial waits in
its common window. Validate exact outcomes/transaction counts and sensible
timing bounds; do not pretend its cycles must match a faster-baud simulation.
"""
import struct

import asterbench_dma as bench

PROGRAMS = {"runtime":"dma_runtime", "publication":"dma_publication"}
UART = {"runtime":b"DMA DIRECTED PASS\nDMA RESERVATIONS PASS\n"+b"DMA JOB PASS\n"*3+b"DMA COUNTERS PASS\n",
        "publication":b"DMA CODE PASS\n"}
SIZES = (0,1,2,3,4,7,8,15,16,31,32,63,64,127,128,255,256,511,512,1024)


def chunks(source_offset,destination_offset,size):
    count = 0
    while size:
        step = 4 if source_offset % 4 == destination_offset % 4 == 0 and size >= 4 else 1
        source_offset += step; destination_offset += step; size -= step; count += 1
    return count


def runtime_totals():
    # 320 directed copies, restart after abort, six LR/SC interactions,
    # eight selective-stop copies, then three published jobs. Abort adds a
    # separately bounded byte prefix; no invented exact abort latency.
    size = 16*sum(SIZES)+1024+12+8*1024+3*1024
    writes = sum(chunks(s,d,n) for s in range(4) for d in range(4) for n in SIZES)+1024+6+8*1024+3*1024
    checks = 320*(3+2304)+9+6+2304+1+2304+12+8*(2+2304)+3*(1+2304)
    return dict(bytes=size,writes=writes,checks=checks,success=320+1+1+6+8+3,aborts=1,errors=6)


def validate_ram(data,program,kind,caches):
    bench.require(kind in PROGRAMS and type(caches) is int and caches in (0,1), "invalid DMA functional profile/cache")
    bench.require(type(data) is bytes and len(data) == 65536, "complete functional stopped RAM required")
    symbols = program["symbols"]; name = "dma_results" if kind == "runtime" else "publication_results"
    symbol = symbols[name]
    bench.require(symbol == dict(address=0x10008000,size=512), "wrong private functional publication layout")
    words = list(struct.unpack_from("<128I",data,0x8000))
    device = list(struct.unpack_from("<14Q",data,0x8000+16*4))
    cpu = [list(struct.unpack_from("<14Q",data,0x8000+(44+h*28)*4)) for h in range(2)]
    bench.require(all(w == 0 for w in words[100:]), "functional publication padding changed")

    def region(name,length):
        s = symbols[name]; address = s["address"]
        bench.require(type(address) is int and address % 64 == 0 and 0x10000000 <= address <= 0x10008000-length and
                      s["size"] == length, "invalid functional buffer symbol")
        return address,data[address-0x10000000:address-0x10000000+length]

    if kind == "runtime":
        source,source_bytes = region("source",1152); destination,dest_bytes = region("destination",1152)
        bench.require(source+1152 <= destination or destination+1152 <= source, "overlapping functional buffers")
        totals = runtime_totals()
        work = symbols["independent_work"]
        bench.require(work["size"] == 4 and 0x10000000 <= work["address"] < 0x10008000 and work["address"] % 4 == 0,
                      "invalid independent work symbol")
        bench.require(words[5] == struct.unpack_from("<I",data,work["address"]-0x10000000)[0],
                      "published independent work differs from actual shared AMO result")
        bench.require(words[:5] == [3,320,totals["checks"],source,destination] and words[5] > 0 and
                      words[6:16] == [2,1024,5,31250000,6+caches,0,0,0,0,0], "wrong functional runtime outcomes")
        pattern = lambda i: ((i*73) ^ (i >> 3) ^ (2003*41)) & 255
        bench.require(source_bytes == bytes(pattern(i) for i in range(1152)), "DMA runtime source/guards changed")
        bench.require(dest_bytes == bytes(pattern(i+1) if 64 <= i < 1088 else (0xa5 ^ i)&255 for i in range(1152)),
                      "DMA runtime destination/prefix/suffix differs")
        prefix = device[4]-totals["bytes"]
        bench.require(0 <= prefix < 1024 and device[3] == totals["writes"]+prefix and device[3] <= device[2] <= device[3]+1,
                      "wrong DMA functional accepted-byte/request counts")
        bench.require(device[10:] == [totals["success"],1,6,0], "missing/extra functional DMA completions/errors")
        bench.require(cpu[0][8:11] == [12,2,4] and cpu[1][8] >= words[5] > 0 and cpu[1][9:11] == [0,0],
                      "missing directed LR/SC or secondary independent AMO execution")
    else:
        code,code_bytes = region("code",64); staging,staging_bytes = region("staging",64)
        bench.require(code+64 <= staging or staging+64 <= code, "overlapping RAM code/source")
        bench.require(words[:16] == [8,0,288,0x138,0x138,code,2,8,5,31250000,6+caches,staging,0,0,0,0],
                      "code publication epoch/execution/handoff result differs")
        for raw,guard in ((code_bytes,0xc0010000),(staging_bytes,0xa57e0000)):
            expected = [0x13800513,0x00008067]+[guard ^ i for i in range(2,16)]
            bench.require(raw == struct.pack("<16I",*expected), "copied executable instructions or guards differ")
        bench.require(device[2:5] == [16,16,64] and device[10:] == [8,0,0,0], "RAM code was not copied by eight DMA jobs")
        bench.require(all(c[8:11] == [0,0,0] for c in cpu), "unexpected publication atomic operations")

    bench.require(cpu[0][0] == cpu[1][0] and cpu[0][0] > 0, "functional common counter windows differ")
    for row in cpu:
        bench.require(row[0] >= row[1] > 100 and row[0] >= row[2] > 0 and row[3] >= row[4] and row[5] >= row[6] and
                      row[0] >= row[7] and row[1] >= row[8] >= row[9]+row[10], "implausible functional CPU counters")
        if not caches: bench.require(row[3:7] == [0]*4 and row[11:] == [0]*3, "cache-disabled CPU maintenance")
    bench.require(device[2]+device[3] <= device[0] <= cpu[0][0] and device[1] <= device[0] and
                  device[5]+device[7] == device[2] and device[6] == device[3]+device[8] and device[8] % 4 == 0 and
                  device[9] <= 2*device[3], "inconsistent DMA busy/wait/backing/coherence counters")
    if not caches: bench.require(device[7:10] == [0,0,0], "cache-disabled DMA maintenance")
    return dict(metadata=words[:16],cpu=cpu,dma=device)


def validate_host_dma(snapshot,values,kind):
    bench.require(type(snapshot) is dict and set(snapshot) == {"status","bytes_done","error_code","counting","job_cycles","counters"},
                  "incomplete physical DMA diagnostics")
    cycles = bench.integer(snapshot["job_cycles"],1,values["dma"][0])
    expected = dict(status=2,bytes_done=1024 if kind == "runtime" else 8,error_code=0,counting=0,job_cycles=cycles,counters=values["dma"])
    bench.require(bench.typed_equal(snapshot,expected), "host DMA diagnostics differ from frozen RAM results")


def validate_observation(observation,values,program,kind):
    bench.require(type(observation) is dict and set(observation) == {"boot","kind","cpu","dma","job_cycles","ram_retired","ram_pcs"},
                  "missing independent functional event observation")
    bench.integer(observation["boot"],1,2)
    bench.require(observation["kind"] == ("dma" if kind == "runtime" else "publication") and
                  bench.typed_equal(observation["cpu"],values["cpu"]) and bench.typed_equal(observation["dma"],values["dma"]),
                  "published counters differ from actual independent event window")
    bench.integer(observation["job_cycles"],1,values["dma"][0])
    code = program["symbols"].get("code",{}).get("address")
    expected_retired = [0,0] if kind == "runtime" else [32,16]
    expected_pcs = [[],[]] if kind == "runtime" else [[code,code+4],[code,code+4]]
    bench.require(bench.typed_equal(observation["ram_retired"],expected_retired) and bench.typed_equal(observation["ram_pcs"],expected_pcs),
                  "wrong actual RAM instruction retirements/PCs")
