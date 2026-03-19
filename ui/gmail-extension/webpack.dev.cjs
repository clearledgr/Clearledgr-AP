'use strict';

const common = require('./webpack.common.cjs');

module.exports = {
  ...common,
  mode: 'development',
  devtool: false,
};
