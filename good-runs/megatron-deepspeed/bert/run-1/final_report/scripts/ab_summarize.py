import gzip,json,glob,os,sys,statistics
def metrics(d):
    fs=sorted(glob.glob(os.path.join(d,'traces/raw','*-app.pfw.gz')))
    fs=[f for f in fs if os.path.getsize(f)>0]
    if not fs: return None
    out={}
    for f in fs[:1]:
        dur={}; cnt={}; tmin=tmax=None
        with gzip.open(f,'rt',errors='ignore') as fh:
            for line in fh:
                line=line.strip().rstrip(',')
                if not line or line[0]!='{': continue
                try: e=json.loads(line)
                except: continue
                n=e.get('name',''); v=e.get('dur',0) or 0; ts=e.get('ts')
                if n: dur[n]=dur.get(n,0)+v; cnt[n]=cnt.get(n,0)+1
                if isinstance(ts,(int,float)):
                    tmin=ts if tmin is None else min(tmin,ts); tmax=ts if tmax is None else max(tmax,ts+v)
        out={'wall':(tmax-tmin)/1e6,'train_step':dur.get('train_step',0)/1e6,
             'n_steps':cnt.get('train_step',0),'init':dur.get('initialize_megatron',0)/1e6,
             'ranks':len(fs)}
    return out
for d in sys.argv[1:]:
    m=metrics(d)
    if m and m['n_steps']:
        print(f"{os.path.basename(d):16s} ranks={m['ranks']:2d} steps={m['n_steps']:4d} "
              f"train_step={m['train_step']:7.2f}s wall={m['wall']:7.1f}s init={m['init']:6.2f}s")
    else:
        print(f"{os.path.basename(d):16s} (incomplete)")
