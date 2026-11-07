import sys, time
sys.path.insert(0, r"C:\Users\49820\Desktop\deepseek-live\extras\python")
import nav2, mine_real
nav2.live()
b = nav2.rd(); print("start", b.get("location"), nav2.tile(b), "inv", [(i["name"], i["stack"]) for i in b["player"]["inventory"] if i])
ok = mine_real.enter_mine_walk()
print("enter:", ok)
if ok:
    n = mine_real.mine_rocks(2)
    print("mined", n)
b2 = nav2.rd()
print("loc", b2.get("location"), "inv", [(i["name"], i["stack"]) for i in b2["player"]["inventory"] if i])
money0 = b2["player"]["money"]
nav2.send({"actionType":"sell_crops"}, 1.0)
b3 = nav2.rd()
print("after sell_crops money", money0, "->", b3["player"]["money"])
print("inv", [(i["name"], i["stack"]) for i in b3["player"]["inventory"] if i])