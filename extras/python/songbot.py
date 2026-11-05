# 独立点歌系统（不依赖鲸语内置点歌，绕开网易云登录 cookie 风控）
#  - 中继把「点歌 xxx」写进 tools\logs\song_requests.jsonl
#  - 本脚本读请求 → cloudsearch 匿名搜索（失败再回退 /api/search/get/web）→ 取播放地址 → 播报 → 播放
#  - 监听鲸语 WS：AI 说话时自动 duck（降低音乐音量）
#  - 本场去重：同一首歌只点一次；每场记录 logs\songs_日期_时间.log
import asyncio, json, os, re, sys, time, io, urllib.parse, urllib.request, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import numpy as np
import soundfile as sf
import librosa
import sounddevice as sd
import aiohttp

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQ = os.path.join(BASE, "tools", "logs", "song_requests.jsonl")
LOG = os.path.join(BASE, "tools", "logs", "songbot.log")
API = "http://127.0.0.1:8787"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
SR = 44100


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    try:
        print(line, flush=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def cfg():
    try:
        with open(os.path.join(BASE, "WhaleTalk", "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def norm_query(q):
    return re.sub(r"[\s“”\"'《》()（）\[\]【】·,，.。!！?？~～-]", "", str(q or "")).lower()


def api_post(path, payload, timeout=15):
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(API + path, data=data,
                                 headers={"Content-Type": "application/json", "X-BVB": "1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        log("API %s 失败: %r" % (path, e))
        return ""


def say(text):
    api_post("/api/say", {"text": text})


# ---------------- 网易云（匿名） ----------------
def _http(url, data=None):
    headers = {"User-Agent": UA, "Referer": "https://music.163.com/"}
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def search_songs(kw, limit=8):
    # 1) cloudsearch（匿名可用，最稳）
    try:
        d = _http("https://music.163.com/api/cloudsearch/pc",
                  {"s": kw, "type": 1, "limit": limit, "offset": 0})
        out = []
        for s in (d.get("result") or {}).get("songs") or []:
            artists = "、".join((a or {}).get("name", "") for a in (s.get("ar") or []))
            out.append({"id": s["id"], "name": s.get("name", ""), "artist": artists,
                        "dur": round((s.get("dt") or 0) / 1000)})
        if out:
            return out
    except Exception as e:
        log("cloudsearch 失败: %r" % (e,))
    # 2) 回退旧接口
    try:
        q = urllib.parse.urlencode({"s": kw, "type": 1, "limit": limit, "offset": 0})
        d = _http("https://music.163.com/api/search/get/web?" + q)
        out = []
        for s in (d.get("result") or {}).get("songs") or []:
            out.append({"id": s["id"], "name": s.get("name", ""),
                        "artist": "、".join(a["name"] for a in s.get("artists") or []),
                        "dur": round((s.get("duration") or 0) / 1000)})
        return out
    except Exception as e:
        log("search/get/web 失败: %r" % (e,))
        return []


def song_url(song_id):
    q = urllib.parse.urlencode({"ids": json.dumps([song_id]), "br": 320000})
    try:
        d = _http("https://music.163.com/api/song/enhance/player/url?" + q)
        for it in d.get("data") or []:
            u = it.get("url")
            if u and not it.get("freeTrialInfo"):
                return u
    except Exception as e:
        log("取播放地址失败: %r" % (e,))
    return None


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ---------------- 播放引擎（带 duck） ----------------
class Player:
    def __init__(self):
        self.duck = False
        self.stream = None

    def play(self, pcm):
        def cb(outdata, frames, timing, status):
            nonlocal pcm, pos
            n = min(frames, len(pcm) - pos)
            vol = 0.25 if self.duck else 1.0
            if n > 0:
                outdata[:n, 0] = pcm[pos:pos + n] * vol
                outdata[:n, 1] = pcm[pos:pos + n] * vol
                pos += n
            if n < frames:
                outdata[n:] = 0
                raise sd.CallbackStop
        pos = 0
        self.stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32",
                                      callback=cb, blocksize=2048)
        self.stream.start()
        while self.stream and self.stream.active:
            time.sleep(0.15)
        try:
            self.stream.stop()
        except Exception:
            pass
        self.stream = None


# ---------------- 主流程 ----------------
QUEUE = []
SEEN = {}
SONG_LOG = os.path.join(BASE, "tools", "logs", "songs_bot_%s.log" % time.strftime("%Y%m%d_%H%M%S"))


def record(name, query, status):
    try:
        with open(SONG_LOG, "a", encoding="utf-8") as f:
            f.write("%s\t%s\t%s\t%s\n" % (time.strftime("%H:%M:%S"), name, query, status))
    except Exception:
        pass


async def ws_listener(player):
    """监听鲸语 WS，AI 说话时 duck 音乐"""
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect("ws://127.0.0.1:8787/ws", heartbeat=20) as ws:
                    log("已连上鲸语 WS（用于 duck）")
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            m = json.loads(msg.data)
                        except Exception:
                            continue
                        if m.get("kind") == "speak":
                            player.duck = True
                        elif m.get("kind") == "speak_end":
                            player.duck = False
        except Exception as e:
            log("WS 断开: %r，3 秒后重连" % (e,))
            await asyncio.sleep(3)


CURRENT = {"playing": False}


def handle_request(name, query):
    cfgd = cfg()
    sc = cfgd.get("songs") or {}
    maxq = int(sc.get("max_queue", 20))
    if len(QUEUE) >= maxq:
        say("%s，歌单排满啦，等前面的播完再点吧！" % name)
        return
    nq = norm_query(query)
    if not nq:
        return
    if nq in SEEN:
        log("重复点歌跳过: %s「%s」（本场 %s 已点）" % (name, query, SEEN[nq]))
        record(name, query, "重复跳过")
        return
    songs = search_songs(query)
    if not songs:
        time.sleep(2.5)
        songs = search_songs(query)
    if not songs:
        say("抱歉%s，没找到《%s》能播的版本。" % (name, query))
        record(name, query, "搜索失败")
        return
    # 选一个能拿到播放地址的
    chosen, url = None, None
    for s in songs[:5]:
        u = song_url(s["id"])
        if u:
            chosen, url = s, u
            break
    if not chosen:
        say("抱歉%s，这首暂时放不了，换一首试试？" % name)
        record(name, query, "无版权/无地址")
        return
    SEEN[nq] = name
    pos = len(QUEUE) + (1 if CURRENT.get("playing") else 0) + 1
    QUEUE.append({"name": name, "song": chosen, "url": url, "query": query})
    log("入队: %s → 《%s》- %s  position=%d" % (name, chosen["name"], chosen["artist"], pos))
    record(name, query, "已点")
    say("收到%s点的《%s》，排在第%d首！" % (name, chosen["name"], pos))


def tail_requests():
    os.makedirs(os.path.dirname(REQ), exist_ok=True)
    if not os.path.exists(REQ):
        try:
            open(REQ, "a", encoding="utf-8").close()
        except Exception:
            pass
    try:
        pos = os.path.getsize(REQ)
    except OSError:
        pos = 0
    while True:
        if not os.path.exists(REQ):
            time.sleep(0.5)
            continue
        try:
            size = os.path.getsize(REQ)
            if size < pos:
                pos = 0
            if size == pos:
                time.sleep(0.4)
                continue
            with open(REQ, "r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                chunk = f.read()
                pos = f.tell()
        except Exception:
            time.sleep(0.5)
            continue
        for raw in chunk.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except Exception:
                continue
            try:
                handle_request(str(d.get("name") or "宝子"), str(d.get("query") or ""))
            except Exception as e:
                log("处理点歌失败: %r" % (e,))


def play_loop(player):
    while True:
        if not QUEUE:
            time.sleep(0.5)
            continue
        item = QUEUE.pop(0)
        s = item["song"]
        CURRENT["playing"] = True
        CURRENT["item"] = item
        try:
            log("开始播放: 《%s》- %s" % (s["name"], s["artist"]))
            say("下面播放%s点的《%s》。" % (item["name"], s["name"]))
            time.sleep(1.2)  # 等播报说完
            data = download(item["url"])
            tmpm = os.path.join(tempfile.gettempdir(), "songbot_%d.mp3" % int(time.time() * 1000))
            with open(tmpm, "wb") as f:
                f.write(data)
            y, sr = sf.read(tmpm, dtype="float32", always_2d=False)
            os.remove(tmpm)
            if y.ndim > 1:
                y = y.mean(axis=1)
            if sr != SR:
                y = librosa.resample(y, orig_sr=sr, target_sr=SR)
            peak = float(np.max(np.abs(y))) or 1.0
            y = (y / peak) * 0.8
            player.play(y)
            log("播放结束: 《%s》" % s["name"])
        except Exception as e:
            log("播放失败: %r" % (e,))
        finally:
            CURRENT["playing"] = False



SONGS_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>
html,body{margin:0;background:transparent;font-family:"Microsoft YaHei",sans-serif;color:#EAF2FF;}
#box{max-width:600px;margin:0;background:linear-gradient(160deg,rgba(16,30,58,.72),rgba(8,16,34,.66));
border:2px solid rgba(127,179,255,.45);border-radius:22px;padding:18px 22px;box-shadow:0 10px 30px rgba(0,8,24,.45);}
.title{font-size:28px;color:#8FC1FF;margin-bottom:12px;display:flex;align-items:center;gap:10px}
.title small{font-size:17px;color:#9FB4D8}
.now{font-size:30px;color:#FFFFFF;margin-bottom:4px}
.sub{font-size:18px;color:#9FB4D8;margin-bottom:10px}
ol{list-style:none;padding:0;margin:4px 0 0}
li{display:flex;align-items:center;padding:9px 0;border-top:1px dashed rgba(127,179,255,.25);white-space:nowrap}
li .n{color:#7FB3FF;width:34px;font-size:23px;flex:0 0 34px}
li .s{color:#EAF2FF;flex:1 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;padding-right:16px;font-size:23px}
li .by{color:#8FA8CC;font-size:17px;flex:0 0 auto;margin-left:auto}
.empty{font-size:20px;color:#9FB4D8}
</style></head><body>
<div id="box">
  <div class="title">点歌列表 <small>点歌免费</small></div>
  <div class="now" id="now">当前没有播放</div>
  <div class="sub">弹幕发「点歌 歌名」即可点歌</div>
  <ol id="list"><li><span class="s empty">还没有人点歌</span></li></ol>
</div>
<script>
async function tick(){
  try{
    const r = await fetch('/api/songs', {cache:'no-store'});
    const d = await r.json();
    const now = document.getElementById('now');
    now.textContent = d.current ? ('正在播放：' + d.current.name + (d.current.artist ? ' · ' + d.current.artist : '')) : '当前没有播放';
    const list = document.getElementById('list');
    if(!d.queue.length){
      list.innerHTML = '<li><span class="s empty">还没有人点歌</span></li>';
    } else {
      list.innerHTML = d.queue.map(function(x,i){
        return '<li><span class="n">' + (i+1) + '.</span><span class="s">' + x.name + '</span><span class="by">' + x.by + ' 点</span></li>';
      }).join('');
    }
  }catch(e){}
}
tick(); setInterval(tick, 1500);
</script></body></html>"""


async def songs_web(port=9886):
    from aiohttp import web

    async def handle_songs(request):
        return web.Response(text=SONGS_HTML, content_type="text/html", charset="utf-8")

    async def handle_api(request):
        cur = None
        if CURRENT.get("playing") and CURRENT.get("item"):
            it = CURRENT["item"]
            cur = {"name": it["song"]["name"], "artist": it["song"].get("artist", "")}
        q = [{"name": it["song"]["name"], "by": it["name"]} for it in QUEUE]
        return web.json_response({"current": cur, "queue": q})

    app = web.Application()
    app.router.add_get("/songs", handle_songs)
    app.router.add_get("/api/songs", handle_api)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    log("点歌列表页面: http://127.0.0.1:%d/songs" % port)
    while True:
        await asyncio.sleep(3600)


async def main():
    player = Player()
    asyncio.get_event_loop().run_in_executor(None, tail_requests)
    asyncio.get_event_loop().run_in_executor(None, play_loop, player)
    asyncio.ensure_future(songs_web(9886))
    await ws_listener(player)


if __name__ == "__main__":
    os.makedirs(os.path.join(BASE, "tools", "logs"), exist_ok=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
