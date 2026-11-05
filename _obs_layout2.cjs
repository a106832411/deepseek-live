const fs = require('fs');
const obs = require(String.raw`D:\文档\deepseek-harness\default-workspace\deepseek-live\app\main\obs.cjs`);

const SCENE = '鲸live直播';
const API = 'http://127.0.0.1:8788';
const SHOTS = String.raw`D:\文档\deepseek-harness\default-workspace\whaletalk\pvz_ai\shots`;

(async () => {
  const c = new obs.ObsClient({ host: '127.0.0.1', port: 4455, password: 'wuDbCRABrFxqUSWA' });
  await c.connect();

  async function transform(name, x, y, w, h) {
    const r = await c.request('GetSceneItemId', { sceneName: SCENE, sourceName: name });
    await c.request('SetSceneItemTransform', {
      sceneName: SCENE,
      sceneItemId: r.sceneItemId,
      sceneItemTransform: {
        positionX: x, positionY: y,
        boundsType: 'OBS_BOUNDS_SCALE_INNER',
        boundsAlignment: 0,
        boundsWidth: w, boundsHeight: h,
        width: w, height: h
      }
    });
    return r.sceneItemId;
  }

  // live2d 缩放到合适大小
  await c.request('SetInputSettings', { inputName: '皮套', inputSettings: { url: API + '/live2d?scale=0.42', width: 540, height: 540 }, overlay: true });

  await transform('背景', 0, 0, 1080, 1920);
  await transform('游戏画面-窗口', 0, 176, 1080, 608);
  await transform('皮套', 0, 960, 540, 540);
  await transform('弹幕层', 540, 960, 540, 540);
  await transform('歌单', 0, 1500, 540, 420);
  await transform('本场数据', 540, 1500, 540, 420);

  await new Promise((r) => setTimeout(r, 2500));
  for (const name of ['游戏画面-窗口', '皮套', '弹幕层', '歌单', '本场数据']) {
    const img = await c.request('GetSourceScreenshot', { sourceName: name, imageFormat: 'png', imageWidth: 270, imageHeight: 270 }).catch(() => null);
    if (img && img.imageData) fs.writeFileSync(`${SHOTS}\\src_${name}.png`, Buffer.from(img.imageData.replace(/^data:image\/png;base64,/, ''), 'base64'));
  }
  const img = await c.request('GetSourceScreenshot', { sourceName: SCENE, imageFormat: 'png', imageWidth: 540, imageHeight: 960 });
  fs.writeFileSync(`${SHOTS}\\obs_layout_v2.png`, Buffer.from(img.imageData.replace(/^data:image\/png;base64,/, ''), 'base64'));
  console.log('saved obs_layout_v2.png + source shots');
  c.close();
  process.exit(0);
})().catch((e) => { console.log('ERR', e.message); process.exit(1); });
