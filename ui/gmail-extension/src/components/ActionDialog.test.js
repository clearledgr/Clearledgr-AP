import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('./ActionDialog.js', import.meta.url), 'utf8');

describe('ActionDialog contract', () => {
  it('keeps the required-reason and confirm-only control flow intact', () => {
    assert.match(source, /onClose\?\.\(true\)/);
    assert.match(source, /const trimmed = value\.trim\(\)/);
    assert.match(source, /if \(isRequired && !trimmed\)/);
    assert.match(source, /A reason is required for this action\./);
    assert.match(source, /Optional note\. Choose a quick reason or type your own\./);
  });

  it('preserves the promise-based hook contract for opening and closing dialogs', () => {
    assert.match(source, /return new Promise/);
    assert.match(source, /setState\(\{ visible: true, config, resolve \}\)/);
    assert.match(source, /prev\.resolve\?\.\(value\)/);
    assert.match(source, /return \[\{ \.\.\.state, onClose: close \}, open\]/);
  });

  it('cleans up the deferred focus timer on unmount', () => {
    assert.match(source, /const focusTimer = setTimeout/);
    assert.match(source, /return \(\) => clearTimeout\(focusTimer\)/);
    assert.match(source, /if \(isConfirmOnly\) confirmRef\.current\?\.focus\(\)/);
    assert.match(source, /else inputRef\.current\?\.focus\(\)/);
  });
});
