# WhaleShop · 给星露谷 AI 补"买种子"动作

> 这是 [deepseek-live](../deepseek-live) 里 `extras/python/stardew_agent.py` 的配套 SMAPI 模组。
> 原有 `StardewMCPBridge` 只支持 `warp / move_to / use_tool / interact / water_all / harvest_all`，
> 没有"买东西"，所以 AI 只能种背包里已有的种子，利润表算得再好也落不了地。
> 这个 mod 补上 `buy_seed`。

## 一、它怎么工作

```
stardew_agent.py                    WhaleShop（本 mod）
   写 orders/xxx.json  ───────────▶  每 ~0.33s 扫一次
   轮询 xxx.result.json ◀──────────  买入并写结果，然后删掉订单
```

订单格式：

```json
{"actionType":"buy_seed","item":"Strawberry","count":8}
{"actionType":"buy_seed","itemId":"745","count":8,"price":100}
```

- `item`：种子英文名或作物英文名（`Strawberry` / `Strawberry Seeds` 都行），也接受纯数字 ID
- `count`：数量（1~999）
- `price`：可选，强制单价；不填会自动查 `Data/Shops` 里的定价，查不到再用物件基础价
- 结果：`{"ok":true,"itemId":"745","name":"Strawberry Seeds","count":8,"unit":100,"cost":800,"money":1200}`

## 二、构建（在装了游戏的机器上做）

前置：.NET SDK 6.0+（没装的话可以用仓库里的 `_dotnet/dotnet-install.ps1` 装到 `D:\live\.tools\dotnet`）

```powershell
cd D:\live\stardew-mod\WhaleShop
.\build.ps1 -GameDir "E:\steam\steamapps\common\Stardew Valley"
```

`build.ps1` 会：
1. 校验 `GameDir` 下有 `Stardew Valley.dll` 和 `StardewModdingAPI.dll`
2. 找 `dotnet`（PATH 里没有就试 `D:\live\.tools\dotnet\dotnet.exe`）
3. `dotnet build -c Release -p:GamePath=<GameDir>`
4. 把 `WhaleShop.dll / Manifest.json` 拷到 `<GameDir>\Mods\WhaleShop\`

## 三、验证

1. 启动游戏（SMAPI），日志里应出现：`WhaleShop 就绪，订单目录：...\Mods\WhaleShop\orders`
2. 手动丢一个订单：

```powershell
$dir = "E:\steam\steamapps\common\Stardew Valley\Mods\WhaleShop\orders"
'{"actionType":"buy_seed","item":"Parsnip","count":5}' | Set-Content "$dir\test.json" -Encoding UTF8
Start-Sleep -Seconds 2
Get-Content "$dir\test.result.json"
```

3. Agent 侧不需要额外配置：它会自动写到 `<模组目录>\orders`，也能用 `STARDEW_SHOP_DIR` 指定。

## 四、已知限制 / 注意

- **不联网、不碰商店 UI**：直接扣钱+塞背包，等于在后台"代购"。想更拟真可以改成要求玩家/同伴站在种子店里（代码里加个位置校验即可）。
- **名称解析**依赖 `Game1.objectData` 里的 `Name` 字段。中文客户端如果 `Name` 是本地化文本，请用英文名或直接给 `itemId`（agent 默认发英文名）。
- 单价优先取 `Data/Shops` 的定价；如果某个种子不在任何商店（比如节日限定），需要订单里带 `price`。
- 没有加"卖东西"动作（`sell`）；需要的话同理扩展。
