'use strict';

const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');

module.exports = {
  entry: {
    // InboxSDK integration layer (adds native Gmail sidebar nav)
    'inboxsdk-layer': './src/inboxsdk-layer.js',
    // InboxSDK page world script (required)
    pageWorld: '@inboxsdk/core/pageWorld.js',
    // InboxSDK background script
    'inboxsdk-background': '@inboxsdk/core/background.js',
  },
  devtool: 'source-map',
  module: {
    rules: [
      {
        test: /\.m?js$/,
        enforce: 'pre',
        use: ['source-map-loader'],
      },
    ],
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
    new CopyPlugin({
      patterns: [
        // Copy pageWorld to web_accessible_resources
        { from: 'node_modules/@inboxsdk/core/pageWorld.js', to: 'pageWorld.js' },
      ],
    }),
  ],
  resolve: {
    extensions: ['.js', '.mjs'],
  },
  // Target web for content script compatibility
  target: 'web',
};
