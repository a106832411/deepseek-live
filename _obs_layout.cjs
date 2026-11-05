const fs = require('fs');
const obs = require(String.raw`D:\文档\deepseek-harness\default-workspace\deepseek-live\app\main\obs.cjs`);

const SCENE = '鲸live直播';
const API = 'http://127.0.0.1:8788';

(async () => {
  const c = new obs.ObsClient({ host: '127.0.0.1', port: 4455, password: 'wuDbCRABrFxqUSWA' });
  await c.connect();
  const list = await c.request('GetSceneList');
  const scene = list.scenes.some((s) => s.sceneName === SCENE) ? SCENE : list.scenes[0].sceneName;
  console.log('scene:', scene);

  const items = await c.request('GetSceneItemList', { sceneName: scene });
  const byName = {};
  items.sceneItems.forEach((i) => (byName[i.sourceName] = i.sceneItemId));
  console.log('items:', Object.keys(byName).join(' | '));

  async function ensureBrowser(name, url, props) {
    if (byName[name]) {
      // 若同名源不是 browser_source（比如原来的文字源），先删掉重建
      try {
        await c.request('SetInputSettings', { inputName: name, inputSettings: { url, width: props.w, height: props.h }, overlay: true });
        return;
      } catch (e) {
        await c.request('RemoveInput', { inputName: name }).catch(() => {});
        delete byName[name];
      }
    }
    await c.request('CreateInput', {
      sceneName: scene,
      inputName: name,
      inputKind: 'browser_source',
      inputSettings: { url, width: props.w, height: props.h, reroute_audio: false },
      sceneItemEnabled: true
    });
    const r = await c.request('GetSceneItemId', { sceneName: scene, sourceName: name });
    byName[name] = r.sceneItemId;
  }

  // 窗口源（星露谷）
  async function ensureWindow() {
    const name = '游戏画面-窗口';
    if (byName[name]) return;
    await c.request('CreateInput', { sceneName: scene, inputName: name, inputKind: 'window_capture', inputSettings: {}, sceneItemEnabled: true });
    const r = await c.request('GetSceneItemId', { sceneName: scene, sourceName: name });
    byName[name] = r.sceneItemId;
    const props = await c.request('GetInputPropertiesListPropertyItems', { inputName: name, propertyName: 'window' });
    const cand = (props.propertyItems || []).find((i) => /Stardew Valley/i.test(i.itemName || ''));
    if (cand) await c.request('SetInputSettings', { inputName: name, inputSettings: { window: cand.itemValue }, overlay: true });
  }
  await ensureWindow();

  // 本场数据 -> /stats
  await c.request('RemoveInput', { inputName: '本场数据' }).catch(() => {});
  await ensureBrowser('本场数据', API + '/stats', { w: 540, h: 420 });

  // 其它浏览器源统一尺寸
  await ensureBrowser('皮套', API + '/live2d?scale=0.95', { w: 540, h: 540 });
  await ensureBrowser('弹幕层', API + '/overlay', { w: 540, h: 540 });
  await ensureBrowser('歌单', API + '/songs', { w: 540, h: 420 });

  // 布局（画布 1080x1920）
  const layout = [
    { name: '背景', x: 0, y: 0, w: 1080, h: 1920, show: true },
    { name: '游戏画面-窗口', x: 0, y: 176, w: 1080, h: 608, show: true },
    { name: '皮套', x: 0, y: 960, w: 540, h: 540, show: true },
    { name: '弹幕层', x: 540, y: 960, w: 540, h: 540, show: true },
    { name: '歌单', x: 0, y: 1500, w: 540, h: 420, show: true },
    { name: '本场数据', x: 540, y: 1500, w: 540, h: 420, show: true }
  ];
  const hideList = ['背景板', '游戏画面', '文字叠加', '字幕', '游戏面板'];

  for (const L of layout) {
    if (!byName[L.name]) { console.log('missing:', L.name); continue; }
    await c.request('SetSceneItemTransform', {
      sceneName: scene,
      sceneItemId: byName[L.name],
      sceneItemTransform: { positionX: L.x, positionY: L.y, width: L.w, height: L.h }
    });
    await c.request('SetSceneItemEnabled', { sceneName: scene, sceneItemId: byName[L.name], sceneItemEnabled: L.show });
  }
  for (const n of hideList) {
    if (byName[n]) await c.request('SetSceneItemEnabled', { sceneName: scene, sceneItemId: byName[n], sceneItemEnabled: false }).catch(() => {});
  }

  // 层序：背景最底，游戏第二，其余在上
  const order = ['背景', '游戏画面-窗口', '皮套', '弹幕层', '歌单', '本场数据'];
  for (let i = 0; i < order.length; i++) {
    if (byName[order[i]]) await c.request('SetSceneItemIndex', { sceneName: scene, sceneItemId: byName[order[i]], sceneItemIndex: i }).catch(() => {});
  }

  await c.request('SetCurrentProgramScene', { sceneName: scene });
  await new Promise((r) => setTimeout(r, 2500));
  const img = await c.request('GetSourceScreenshot', { sourceName: scene, imageFormat: 'png', imageWidth: 540, imageHeight: 960 });
  fs.writeFileSync(String.raw`D:\文档\deepseek-harness\default-workspace\whaletalk\pvz_ai\shots\obs_layout_new.png`, Buffer.from(img.imageData.replace(/^data:image\/png;base64,/, ''), 'base64'));
  console.log('saved obs_layout_new.png');

  const finalItems = await c.request('GetSceneItemList', { sceneName: scene });
  console.log('final:', finalItems.sceneItems.map((i) => i.sourceName + (i.sceneItemEnabled ? '' : '(隐藏)')).join(' | '));
  c.close();
  process.exit(0);
})().catch((e) => { console.log('ERR', e.message); process.exit(1); });
