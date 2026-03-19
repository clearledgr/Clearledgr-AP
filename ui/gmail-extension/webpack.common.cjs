'use strict';

const path = require('path');

module.exports = {
  entry: {
    // InboxSDK integration layer (adds native Gmail sidebar nav)
    'inboxsdk-layer': './src/inboxsdk-layer.js',
    // InboxSDK page world script (required)
    pageWorld: '@inboxsdk/core/pageWorld.js',
    // InboxSDK background script
    'inboxsdk-background': '@inboxsdk/core/background.js',
  },
  output: {
    path: path.resolve(__dirname, 'dist'),
    // Keep dist/ as the build output for InboxSDK bundles only.
    // The extension root is the project folder (manifest references dist/...).
    clean: true,
    // Output as IIFE for content script compatibility
    iife: true,
    // Ensure no module syntax in output
    chunkFormat: false,
  },
  plugins: [
  ],
  resolve: {
    extensions: ['.js', '.mjs'],
    alias: {
      '@shared': path.resolve(__dirname, '..', 'shared'),
    },
    modules: [path.resolve(__dirname, 'node_modules'), 'node_modules'],
  },
  // Target web for content script compatibility
  target: 'web',
};
