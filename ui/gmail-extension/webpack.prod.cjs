'use strict';

const common = require('./webpack.common.cjs');

module.exports = {
  ...common,
  mode: 'production',
  devtool: 'source-map',
};
