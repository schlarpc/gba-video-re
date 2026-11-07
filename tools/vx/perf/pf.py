import csv, numpy as np
def load(p):
    rows=list(csv.DictReader(open(p)))
    cols={}
    for k in rows[0].keys():
        cols[k]=np.array([float(r[k]) if r.get(k) not in (None,'') else np.nan for r in rows])
    return cols
