import struct, re
d=open('../player/ewram.bin','rb').read()
L=struct.unpack('<4096H',d[0x10e4:0x10e4+0x2000])
# derive codebook: group entries by (len, prefix)
cb={}
for i,e in enumerate(L):
    ln=e&15; lev=(e>>4)&31; run=(e>>9)&63; last=e>>15
    if (i>>5)==3: continue  # escape region, never looked up
    if ln>12:  # sign bit beyond 12-bit peek; codeword is all 12 bits
        key=(12,i)
    else:
        key=(ln-1 if ln>1 else 0, i>>(12-(ln-1))) if ln>1 else ('inv',i>>3)
    sym=(last,run,lev,ln)
    cb.setdefault(key,set()).add(sym)
# for ln<=12: codeword length ln-1 (sign excluded) prefix + sign bit; check all entries consistent
codes=[]
for k,v in cb.items():
    assert len(v)==1,(k,v)
    (last,run,lev,ln),=v
    if k[0]=='inv': continue
    codes.append((k[0],k[1],last,run,lev))
codes=sorted(set(codes))
print(len(codes))
# compare to mpeg4 intra
src=open('../ref/mpeg4data.h').read()
blk=src[src.index('ff_mpeg4_intra_vlc'):]
vlc=[tuple(int(x,0) for x in m) for m in re.findall(r'\{\s*(0x[0-9a-f]+),\s*(\d+)\s*\}',blk[:blk.index('};')])]
lv=[int(x) for x in re.findall(r'-?\d+',blk[blk.index('ff_mpeg4_intra_level[102] = {')+30:].split('};')[0])]
rn=[int(x) for x in re.findall(r'-?\d+',blk[blk.index('ff_mpeg4_intra_run[102] = {')+28:].split('};')[0])]
ref=set()
for i in range(102):
    c,n=vlc[i]; ref.add((n,c,1 if i>=67 else 0,rn[i],lv[i]))
mine=set(codes)
print('mine-ref',sorted(mine-ref)); print('ref-mine',sorted(ref-mine))
