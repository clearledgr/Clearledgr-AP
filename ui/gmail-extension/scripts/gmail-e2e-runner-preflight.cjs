#!/usr/bin/env node

const fs = require('node:fs');
const path = require('node:path');

function parseArgs(argv = process.argv.slice(2)) {
  const options = {
    profileDir: process.env.GMAIL_E2E_PROFILE_DIR || '',
    skipBrowserLaunch: process.env.GMAIL_E2E_PREFLIGHT_SKIP_BROWSER_LAUNCH === '1',
    executablePath: process.env.GMAIL_E2E_EXECUTABLE_PATH || '',
    profileDirectory: process.env.GMAIL_E2E_PROFILE_DIRECTORY || '',
  };

  for (let i = 0; i < argv.length; i += 1) {
    const token = String(argv[i] || '');
    const next = argv[i + 1];
    if (token === '--profile-dir') {
      options.profileDir = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--skip-browser-launch') {
      options.skipBrowserLaunch = true;
      continue;
    }
    if (token === '--executable-path') {
      options.executablePath = String(next || '').trim();
      i += 1;
      continue;
    }
    if (token === '--profile-directory') {
      options.profileDirectory = String(next || '').trim();
      i += 1;
      continue;
    }
  }

  return options;
}

function ensureReadableDirectory(dirPath) {
  const resolved = path.resolve(dirPath);
  if (!fs.existsSync(resolved)) {
    throw new Error(`profile_dir_not_found:${resolved}`);
  }
  const stat = fs.statSync(resolved);
  if (!stat.isDirectory()) {
    throw new Error(`profile_dir_not_directory:${resolved}`);
  }
  fs.accessSync(resolved, fs.constants.R_OK);
  return resolved;
}

function ensureProfileHasState(profileDir) {
  const entries = fs.readdirSync(profileDir);
  if (!entries.length) {
    throw new Error(`profile_dir_empty:${profileDir}`);
  }
  const looksInitialized = entries.some((entry) => {
    const lower = String(entry || '').toLowerCase();
    return lower.includes('default') || lower.includes('local state') || lower.includes('preferences');
  });
  if (!looksInitialized) {
    throw new Error(`profile_dir_missing_chromium_state:${profileDir}`);
  }
}

function ensureExecutablePath(executablePath) {
  if (!executablePath) return '';
  const resolved = path.resolve(executablePath);
  if (!fs.existsSync(resolved)) {
    throw new Error(`executable_path_not_found:${resolved}`);
  }
  const stat = fs.statSync(resolved);
  if (!stat.isFile()) {
    throw new Error(`executable_path_not_file:${resolved}`);
  }
  fs.accessSync(resolved, fs.constants.R_OK | fs.constants.X_OK);
  return resolved;
}

async function ensurePlaywrightLaunchable({ skipLaunch = false, executablePath = '' } = {}) {
  let playwright;
  try {
    playwright = require('playwright');
  } catch (error) {
    const message = String(error && error.message ? error.message : error);
    throw new Error(`playwright_unavailable:${message}`);
  }

  if (skipLaunch) {
    return { launchTested: false };
  }

  const chromium = playwright && playwright.chromium;
  if (!chromium || typeof chromium.launch !== 'function') {
    throw new Error('playwright_chromium_unavailable');
  }

  let browser;
  try {
    browser = await chromium.launch({
      headless: true,
      executablePath: executablePath || undefined,
    });
    return { launchTested: true };
  } catch (error) {
    const message = String(error && error.message ? error.message : error).split('\n')[0];
    throw new Error(`playwright_launch_failed:${message}`);
  } finally {
    if (browser) {
      try {
        await browser.close();
      } catch (_) {
        // no-op
      }
    }
  }
}

async function run() {
  const options = parseArgs();
  if (!options.profileDir) {
    throw new Error('missing_profile_dir: set GMAIL_E2E_PROFILE_DIR or pass --profile-dir');
  }

  const profileDir = ensureReadableDirectory(options.profileDir);
  ensureProfileHasState(profileDir);
  const executablePath = ensureExecutablePath(options.executablePath);
  const playwright = await ensurePlaywrightLaunchable({
    skipLaunch: options.skipBrowserLaunch,
    executablePath,
  });

  const summary = {
    status: 'ok',
    profile_dir: profileDir,
    profile_directory: options.profileDirectory || null,
    skip_browser_launch: options.skipBrowserLaunch,
    browser_launch_tested: playwright.launchTested,
    executable_path: executablePath || null,
    checked_at: new Date().toISOString(),
  };
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
}

if (require.main === module) {
  run().catch((error) => {
    const message = String(error && error.message ? error.message : error);
    process.stderr.write(`gmail_e2e_runner_preflight_failed:${message}\n`);
    process.exitCode = 1;
  });
}

module.exports = {
  parseArgs,
  ensureReadableDirectory,
  ensureProfileHasState,
  ensureExecutablePath,
  ensurePlaywrightLaunchable,
  run,
};
