#!/usr/bin/env node

const path = require('node:path');
const webpack = require('webpack');
const { computeSourceFingerprint } = require('./source-fingerprint.cjs');

const EXTENSION_ROOT = path.resolve(__dirname, '..');

function stamp(label) {
  const now = new Date().toISOString();
  console.log(`[debug-webpack-build] ${now} ${label}`);
}

function main() {
  const fingerprint = computeSourceFingerprint(EXTENSION_ROOT).fingerprint;
  process.env.CLEARLEDGR_SOURCE_FINGERPRINT = fingerprint;
  const configPath = path.join(EXTENSION_ROOT, 'webpack.dev.cjs');
  delete require.cache[require.resolve(configPath)];
  const config = require(configPath);
  const compiler = webpack(config);

  const timeout = setTimeout(() => {
    stamp(`timeout activeHandles=${process._getActiveHandles().length}`);
    process.exit(124);
  }, 90000);

  [
    'beforeRun',
    'run',
    'compile',
    'thisCompilation',
    'make',
    'emit',
    'afterEmit',
    'done',
    'failed',
  ].forEach((hookName) => {
    const hook = compiler.hooks[hookName];
    if (!hook || typeof hook.tap !== 'function') return;
    hook.tap('ClearledgrDebugWebpackBuild', () => {
      stamp(`hook:${hookName}`);
    });
  });

  stamp('starting compiler.run');
  compiler.run((error, stats) => {
    stamp('compiler.run callback');
    clearTimeout(timeout);
    if (error) {
      console.error(error);
      process.exit(1);
      return;
    }
    if (stats?.hasErrors()) {
      console.error(stats.toString({ colors: false, all: false, errors: true, warnings: true }));
      process.exit(1);
      return;
    }
    console.log(stats?.toString({
      colors: false,
      chunks: false,
      modules: false,
      children: false,
      timings: true,
      assets: true,
      warnings: true,
    }));
    compiler.close(() => process.exit(0));
  });
}

main();
