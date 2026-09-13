"""Independent final RAM/event oracle for the two-hart DOT8 C acceptance test.

This is functional evidence, not a performance comparison. The simulation also
checks every overwritten output as it is stored; retained RAM independently
proves the final directed/publication jobs, guards, LR/SC outcomes and 50 events.
"""
import struct

import asterbench_dot8 as bench

UART = b"DOT8 RUNTIME PASS\n"
DOTS = [25393,25689]
DOT_COUNTERS = [v for n in DOTS for v in (n,2*n,n,n)]


def pattern(i,seed,bank):
    mode = seed & 7
    if mode == 0: return 0
    if mode == 1: return 128
    if mode == 2: return 127 if bank else 128
    if mode == 3: return 127 if i & 1 else 128
    return ((seed >> ((i % 4)*8)) ^ (i*73) ^ (i//8) ^ (bank*0x5b)) & 255


def final_buffers(slot):
    seed = 0x81d7e01d if slot == 0 else (0xa57e8000 ^ 0x01000000 ^ (736*0x9e3779b9)) & bench.U32
    k,sa,sb = (32,1,2) if slot == 0 else (64,3,3)
    a,b = (bytes(pattern(i,seed,bank) for i in range(512)) for bank in (0,1))
    signed = lambda value: value if value < 128 else value-256
    outputs = [sum(signed(a[64+sa+(out//5)*k+j])*signed(b[64+sb+j*5+out%5]) for j in range(k)) & bench.U32 for out in range(15)]
    words = [(0x6d5a0000 ^ (i*0x01010101) ^ seed) & bench.U32 for i in range(47)]
    words[16:31] = outputs
    return a,b,struct.pack("<47I",*words)


def validate_ram(data,program,caches):
    bench.integer(caches,0,1)
    bench.require(type(data) is bytes and len(data) == 65536, "complete functional retained RAM required")
    symbols = program["symbols"]
    occupied = []
    def region(name,size,alignment=4,private=False):
        item = symbols[name]; address = item["address"]
        bench.require(type(address) is int and address % alignment == 0 and item["size"] == size and
                      0x10000000 <= address <= (0x10010000 if private else 0x10008000)-size, "invalid functional symbol: "+name)
        bench.require(all(address+size <= low or high <= address for low,high in occupied), "overlapping functional RAM symbols")
        occupied.append((address,address+size))
        return address,data[address-0x10000000:address-0x10000000+size]
    a,aa = region("input_a",1024,64); b,bb = region("input_b",1024,64); y,yy = region("output",376,64)
    for slot in (0,1):
        expected = final_buffers(slot)
        bench.require((aa[slot*512:(slot+1)*512],bb[slot*512:(slot+1)*512],yy[slot*188:(slot+1)*188]) == expected,
                      "functional signed GEMM inputs/outputs/guards differ")
    for name in ("dma_source","dma_destination"):
        bench.require(region(name,512,64)[1] == final_buffers(0)[0], "functional DMA source/copy/guards differ")
    for name,values in (("directed_done",[1,1]),("published",[3]),("consumed",[3]),("reservation_word",[0x12345678]*2)):
        bench.require(region(name,len(values)*4)[1] == struct.pack("<"+str(len(values))+"I",*values), "functional publication/LRSC result differs: "+name)
    metadata = []
    for hart in (0,1):
        address,raw = region("aster_dot8_runtime_results"+str(hart),640,4,True)
        bench.require(address == 0x10008000+hart*0x4000, "wrong private functional result address")
        words = list(struct.unpack("<160I",raw)); metadata.append(words)
        seed0 = (0xa57e8000 ^ (736*0x9e3779b9)) & bench.U32
        expected = [100,736,2,64,3,3,seed0,a,b,y,512,47] if hart == 0 else [100,739,2,32,1,2,0x81d7e01d,a,b,y,512,47]
        bench.require(words[:12] == expected and words[12:21] == [0,0,0,0,736,0,DOTS[hart],16,3], "wrong functional job/alias/alignment/publication metadata")
        bench.require(words[21:32] == ([2,0x80001]+[0]*9 if hart == 0 else [0]*11) and
                      words[132 if hart == 0 else 32:] == [0]*(28 if hart == 0 else 128), "functional metadata/padding changed")
    counts = [metadata[0][32+2*i] | metadata[0][33+2*i]<<32 for i in range(50)]
    cpu = [counts[:14],counts[14:28]]; dma = counts[28:42]; dot = counts[42:]
    bench.require(dot == DOT_COUNTERS, "wrong functional accepted/wait/completed/retired DOT8 totals")
    bench.require(cpu[0][0] == cpu[1][0] and cpu[0][0] > 1000000, "functional common counter window differs")
    for row in cpu:
        bench.require(row[0] >= row[1] > 100 and row[0] >= row[2] > 0 and row[3] >= row[4] and row[5] >= row[6] and
                      row[0] >= row[7] and row[8:11] == [32,16,0], "functional CPU/16 LR-dot-SC counters differ")
        if not caches: bench.require(row[3:7] == [0]*4 and row[11:] == [0]*3, "cache-disabled CPU maintenance")
    bench.require(dma[2:5] == [512,512,2048] and dma[10:] == [4,0,0,0], "functional four acknowledged DMA jobs differ")
    bench.require(dma[2]+dma[3] <= dma[0] <= cpu[0][0] and dma[1] <= dma[0] and dma[5]+dma[7] == dma[2] and
                  dma[6] == dma[3]+dma[8] and dma[8] % 4 == 0 and dma[9] <= 2*dma[3], "functional DMA backing/coherence events inconsistent")
    if not caches: bench.require(dma[7:10] == [0,0,0], "cache-disabled DMA maintenance")
    return dict(metadata=metadata,cpu=cpu,dma=dma,dot8=dot,counts=counts)


def validate_observation(observation,values,boot):
    bench.require(type(observation) is dict and set(observation) == {"boot","counts","pairs","methods","dots","dma_overlap"}, "incomplete functional independent observation")
    for key,value in dict(boot=boot,counts=values["counts"],pairs=[736,736],methods=[1472,1475],dots=DOTS).items():
        bench.require(bench.typed_equal(observation[key],value), "functional RAM/event observation differs: "+key)
    bench.integer(observation["dma_overlap"],1,64)


def host_dma(values):
    # ACK clears terminal status, but the DMA contract deliberately retains
    # completed-byte and last-job-cycle accounting until the next accepted
    # START/reset.  The cycle count is checked as a live positive diagnostic
    # by the physical proof because it depends on the hardware memory path.
    return dict(status=0,bytes_done=values["dma"][2],error_code=0,counting=0,counters=values["dma"])


def validate_host_dma(snapshot, values):
    expected = host_dma(values)
    for key in ("status", "bytes_done", "error_code", "counting", "counters"):
        bench.require(bench.typed_equal(snapshot[key], expected[key]), "functional DMA acknowledgement/accounting differs")
    bench.integer(snapshot["job_cycles"], 1)


def host_dot8(values):
    return dict(counting=0,busy=0,counters=values["dot8"])
