# 星露谷 AI 种植/钓鱼/挖矿（鲸live · Stardew 模块）

## 目录结构
- `agent/`            Python 端：Agent / 管家 / 长跑脚本
  - `stardew_agent.py`       LLM 决策 → 发订单（对接 WhalePlayer）
  - `stardew_supervisor.py`  确定性管家（清杂物/浇水/收获/播种/出货/钓鱼 + 看门狗）
  - `stardew_run.py`         长时段跑测脚本
- `mods/`             C# mod 源码
  - `WhalePlayer/`           直接控制玩家角色（走路/工具动画/商店/出货/宝箱/钓鱼补丁）
  - `WhaleShop/`             订单式买卖
  - `WhaleAutoLoad/`         自动读最新存档
  - `stardew-mcp-src/`       bridge 源码（植物/工具动画/出货补丁）
  - `inspect/`               反射调试工具

## 部署方式
把编译产物复制到 `D:\steam\steamapps\common\Stardew Valley\Mods\` 下对应目录，
用 SMAPI 启动游戏；Agent 通过 `Mods\WhalePlayer\player_state.json` / `orders\` 通信。

## 常用命令
```powershell
# 编译 WhalePlayer
cd mods\WhalePlayer
dotnet build -c Release "-p:GamePath=D:\steam\steamapps\common\Stardew Valley"

# 运行 Agent（需要游戏已启动）
python agent\stardew_agent.py --once
# 跑长时段管家
python agent\stardew_supervisor.py --minutes 90 --pattern "farm,farm,fish"
```
