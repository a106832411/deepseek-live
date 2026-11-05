"""把真游戏画面做成 HTTP MJPEG 流，供 OBS 浏览器源显示（OBS 直接采集抓不到这个老游戏）。"""
import asyncio
import io
import sys
import time

from aiohttp import web
from PIL import Image

sys.path.insert(0, r"D:\文档\deepseek-harness\default-workspace\whaletalk\pvz_ai")
from util import capture_game  # noqa: E402

PORT = 9800
TITLE = "Plants vs. Zombies"
_latest = {"jpg": None, "t": 0}


async def grab_loop():
    loop = asyncio.get_event_loop()
    while True:
        try:
            img = await loop.run_in_executor(None, lambda: capture_game(TITLE, tries=2))
            if img is not None:
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=85)
                _latest["jpg"] = buf.getvalue()
                _latest["t"] = time.time()
        except Exception as e:
            print("grab error", repr(e), flush=True)
        await asyncio.sleep(0.12)


async def handle_cam(request):
    resp = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "multipart/x-mixed-replace; boundary=frame",
            "Cache-Control": "no-store",
        },
    )
    await resp.prepare(request)
    last = None
    while True:
        jpg = _latest["jpg"]
        if jpg and jpg is not last:
            last = jpg
            try:
                await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
            except Exception:
                break
        await asyncio.sleep(0.08)
    return resp


async def handle_snap(request):
    jpg = _latest["jpg"]
    if not jpg:
        return web.Response(status=503)
    return web.Response(body=jpg, content_type="image/jpeg")


async def handle_index(request):
    return web.Response(text="<html><body style='margin:0;background:transparent'>"
                              "<img src='/cam' style='width:100%'></body></html>",
                        content_type="text/html")


async def main():
    app = web.Application()
    app.router.add_get("/cam", handle_cam)
    app.router.add_get("/snap", handle_snap)
    app.router.add_get("/", handle_index)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", PORT)
    await site.start()
    print("gamecam on http://127.0.0.1:%d/cam" % PORT, flush=True)
    await grab_loop()


if __name__ == "__main__":
    asyncio.run(main())
