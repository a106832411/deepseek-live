#!/usr/bin/env node
/** 无 Electron 模式：node server/index.cjs [--port 8788] */
const args = process.argv.slice(2);
function arg(name, def) {
  const i = args.indexOf('--' + name);
  return i >= 0 ? args[i + 1] : def;
}
const port = Number(process.env.WT_PORT || arg('port', '8788'));
require('../app/main/core.cjs').start(port);