import dnfile

path = r"E:\steam\steamapps\common\Stardew Valley\Stardew Valley.dll"
pe = dnfile.dnPE(path)

tdefs = list(pe.net.mdtables.TypeDef)
rows = list(pe.net.mdtables.MethodDef)

def rid_start(idx):
    for attr in ("StartRid", "row_index", "Rid"):
        v = getattr(idx, attr, None)
        if v:
            return int(v)
    return None

wanted = {"HoeDirt", "Farmer", "GameLocation", "Object", "Crop", "ShippingBin", "Game1", "Utility", "Farm"}
out = {}
for i, t in enumerate(tdefs):
    tname = str(t.TypeName)
    if tname not in wanted:
        continue
    start = rid_start(t.MethodList)
    if not start:
        continue
    end = None
    if i + 1 < len(tdefs):
        end = rid_start(tdefs[i + 1].MethodList)
    names = []
    rid = start
    while rid < (end or len(rows) + 1) and rid - 1 < len(rows):
        m = rows[rid - 1]
        names.append(str(m.Name))
        rid += 1
    out.setdefault(tname, []).extend(names)

keys = ("plant", "seed", "shipping", "sell", "eat", "water", "harvest", "crop", "additem", "money", "object")
for tname, names in out.items():
    hit = sorted({n for n in names if any(k in n.lower() for k in keys)})
    print("\n==", tname, f"({len(names)} methods)")
    for n in hit:
        print("  ", n)
