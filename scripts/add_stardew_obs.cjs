/**
 * 把「星露谷 AI 状态」加进 OBS 当前场景（浏览器源）。
 *
 * 用法：
 *   node scripts/add_stardew_obs.cjs
 *   node scripts/add_stardew_obs.cjs --scene "鲸live直播" --url "http://127.0.0.1:8790/stardew"
 *
 * 前置：OBS 开着（websocket 4455），status_server.py 已启动。
 */
const obs = require('../app/main/obs.cjs');

const args = process.argv.slice(2);
const getArg = (name, def) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] ? args[i + 1] : def;
};

const HOST = getArg('--host', '127.0.0.1');
const PORT = parseInt(getArg('--port', '4455'), 10);
const PASSWORD = getArg('--password', process.env.OBS_PASSWORD || 'wuDbCRABrFxqUSWA');
const SCENE = getArg('--scene', '');
const URL = getArg('--url', 'http://127.0.0.1:8790/stardew');
const SOURCE = '星露谷AI状态';

(async () => {
  const client = new obs.ObsClient({ host: HOST, port: PORT, password: PASSWORD });
  await client.connect();

  const scenes = await client.request('GetSceneList');
  const names = (scenes.scenes || []).map(s => s.sceneName);
  const scene = SCENE || scenes.currentProgramSceneName || names[0];
  if (!scene) throw new Error('OBS 里没有场景');
  console.log('目标场景:', scene);

  const inputs = await client.request('GetInputList');
  const exists = (inputs.inputs || []).some(i => i.inputName === SOURCE);

  if (!exists) {
    await client.request('CreateInput', {
      sceneName: scene,
      inputName: SOURCE,
      inputKind: 'browser_source',
      inputSettings: {
        url: URL,
        width: 480,
        height: 300,
        reroute_audio: false,
        css: 'body{background:transparent;margin:0} .panel{box-shadow:none!important;border-color:rgba(216,178,90,.35)!important;background:rgba(14,13,16,.55)!important}'
      },
      sceneItemEnabled: true
    });
    console.log('已创建浏览器源:', SOURCE);
  } else {
    await client.request('SetInputSettings', {
      inputName: SOURCE,
      inputSettings: {
        url: URL,
        width: 480,
        height: 300,
        css: 'body{background:transparent;margin:0} .panel{box-shadow:none!important;border-color:rgba(216,178,90,.35)!important;background:rgba(14,13,16,.55)!important}'
      },
      overlay: true
    });
    console.log('已更新已有源:', SOURCE);
  }

  const item = await client.request('GetSceneItemId', { sceneName: scene, sourceName: SOURCE });
  const video = await client.request('GetVideoSettings');
  const W = video.baseWidth || video.outputWidth || 1920;
  const H = video.baseHeight || video.outputHeight || 1080;
  await client.request('SetSceneItemTransform', {
    sceneName: scene,
    sceneItemId: item.sceneItemId,
    sceneItemTransform: {
      positionX: 24,
      positionY: Math.max(0, H - 330),
      boundsType: 'OBS_BOUNDS_SCALE_INNER',
      boundsWidth: 460,
      boundsHeight: 300,
      boundsAlignment: 0
    }
  });
  console.log('已放到左下角');
  process.exit(0);
})().catch(e => {
  console.error('失败:', e.message);
  process.exit(1);
});
