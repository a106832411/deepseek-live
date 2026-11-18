#!/usr/bin/env node
/** 无 Electron 模式：node server/index.cjs [--port 8788] */
const args = process.argv.slice(2);
function arg(name, def) {
  const i = args.indexOf('--' + name);
  return i >= 0 ? args[i + 1] : def;
}
const core = require('../app/main/core.cjs');
const port = core.resolvePort(Number(arg('port', 0)) || undefined);
core.start(port);