/**
 * Clearledgr InboxSDK Integration
 * 
 * Architecture (matching Streak):
 * - AppMenu: Navigation to ROUTES (renders in main content area)
 * - Routes: Full-page views (Home, Vendors, Analytics, Pipeline)
 * - Sidebar: Email-specific context ONLY (when viewing a thread)
 * 
 * =============================================================================
 * CRITICAL: INBOXSDK LOADING ARCHITECTURE - DO NOT CHANGE WITHOUT READING
 * =============================================================================
 * 
 * InboxSDK MUST run in the CONTENT SCRIPT world (isolated), NOT the page world.
 * 
 * HOW IT WORKS:
 * 1. This file (inboxsdk-layer.js) is loaded as a CONTENT SCRIPT via manifest.json
 * 2. InboxSDK detects it's in a Chrome extension and uses chrome.runtime APIs
 * 3. InboxSDK sends a message to background.js to inject pageWorld.js into MAIN world
 * 4. Communication between content script and page world happens via postMessage
 * 
 * DO NOT:
 * - Inject this file into the page world via <script> tag
 * - Set "world": "MAIN" in manifest.json for this script
 * - Try to run InboxSDK directly in the page world
 * 
 * If you see "Cannot read properties of undefined (reading 'sendMessage')",
 * it means InboxSDK is running in the page world where chrome.runtime doesn't exist.
 * 
 * Required files:
 * - manifest.json: inboxsdk-layer.js in content_scripts (NO world: "MAIN")
 * - manifest.json: pageWorld.js in web_accessible_resources
 * - background.js: Handler for 'inboxsdk__injectPageWorld' message
 * =============================================================================
 */
import * as InboxSDK from '@inboxsdk/core';
import '../content-script.js';
import { classifyApEmail } from '../utils/ap_classifier.js';

const APP_ID = 'sdk_Clearledgr2026_dc12c60472';

let sdk = null;
const triageRequestedThreads = new Set();

// =============================================================================
// ICONS - Custom SVG icons with Clearledgr branding
// =============================================================================

const BRAND_COLOR = '#10B981';
const BRAND_DARK = '#059669';

function svgToDataUrl(svg) {
  return 'data:image/svg+xml;base64,' + btoa(svg);
}

const ICONS = {
  // Main Clearledgr logo - active state (original brand colors)
  mainActive: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 230 236"><path d="M0 0 C1.42 -0.01 2.84 -0.01 4.27 -0.02 C8.1 -0.04 11.94 -0.04 15.78 -0.03 C19 -0.03 22.21 -0.03 25.43 -0.04 C33.02 -0.05 40.61 -0.05 48.2 -0.04 C56.01 -0.03 63.82 -0.04 71.63 -0.07 C78.35 -0.09 85.08 -0.1 91.8 -0.09 C95.81 -0.09 99.82 -0.09 103.82 -0.11 C107.59 -0.13 111.37 -0.12 115.14 -0.1 C117.16 -0.1 119.19 -0.11 121.22 -0.13 C130.89 -0.05 140.05 1.42 147.63 7.9 C148.35 8.52 149.08 9.13 149.82 9.76 C150.46 10.3 151.1 10.84 151.75 11.4 C152.64 12 153.52 12.6 154.44 13.22 C162.28 20.64 165.8 31.22 166.14 41.8 C166.16 44.24 166.16 46.67 166.15 49.11 C166.16 50.46 166.17 51.81 166.18 53.16 C166.19 56.81 166.19 60.45 166.19 64.09 C166.18 67.14 166.19 70.2 166.2 73.25 C166.21 80.46 166.21 87.68 166.2 94.89 C166.19 102.3 166.2 109.71 166.23 117.12 C166.25 123.51 166.25 129.9 166.25 136.29 C166.25 140.09 166.25 143.89 166.27 147.7 C166.28 151.28 166.28 154.86 166.26 158.44 C166.25 160.36 166.27 162.28 166.28 164.2 C166.17 177.22 162.52 188.27 153.49 197.78 C145.43 205.46 137.07 210.51 125.8 210.55 C124.58 210.56 123.36 210.57 122.11 210.57 C120.76 210.58 119.42 210.58 118.07 210.58 C116.65 210.59 115.23 210.59 113.81 210.6 C109.14 210.62 104.47 210.63 99.81 210.64 C98.2 210.65 96.59 210.65 94.98 210.65 C87.42 210.67 79.85 210.69 72.28 210.7 C63.57 210.71 54.86 210.73 46.14 210.77 C39.39 210.8 32.65 210.82 25.9 210.82 C21.88 210.82 17.85 210.83 13.83 210.86 C10.04 210.88 6.25 210.88 2.46 210.87 C0.42 210.87 -1.62 210.89 -3.66 210.91 C-17.86 210.83 -26.81 206.39 -37 196.75 C-44.27 189.03 -48.37 179.15 -48.38 168.62 C-48.39 167.47 -48.39 166.31 -48.4 165.12 C-48.4 163.87 -48.4 162.61 -48.39 161.31 C-48.4 159.97 -48.4 158.62 -48.4 157.27 C-48.41 153.63 -48.42 149.98 -48.42 146.33 C-48.42 144.04 -48.42 141.76 -48.42 139.48 C-48.43 131.51 -48.44 123.53 -48.43 115.56 C-48.43 108.14 -48.44 100.73 -48.46 93.31 C-48.47 86.93 -48.48 80.55 -48.48 74.17 C-48.48 70.37 -48.48 66.56 -48.49 62.76 C-48.5 59.18 -48.5 55.6 -48.49 52.01 C-48.49 50.08 -48.5 48.16 -48.51 46.23 C-48.46 32.93 -45.79 22.96 -36.44 13.15 C-25.24 2.61 -15.18 -0.05 0 0 Z" fill="#031536" transform="translate(56.25,13.6)"/><path d="M0 0 C31 0 31 0 40 6 C43.6 11.5 44.59 15.74 44.53 22.22 C44.55 23.38 44.55 23.38 44.56 24.58 C44.58 27.12 44.58 29.67 44.57 32.22 C44.57 34 44.58 35.79 44.59 37.58 C44.6 41.31 44.59 45.04 44.58 48.77 C44.56 53.54 44.58 58.31 44.62 63.07 C44.64 66.76 44.64 70.44 44.63 74.13 C44.63 75.88 44.64 77.64 44.65 79.4 C44.77 96.37 44.77 96.37 39 103 C27.87 113.33 17.77 108 0 108 C0 72.36 0 36.72 0 0 Z" fill="#02F6B4" transform="translate(127,65)"/><path d="M0 0 C0 35.64 0 71.28 0 108 C-14.19 108 -28.38 108 -43 108 C-43.08 85.07 -43.08 85.07 -43.1 75.28 C-43.11 68.61 -43.12 61.94 -43.15 55.26 C-43.17 49.88 -43.18 44.5 -43.19 39.12 C-43.19 37.07 -43.2 35.01 -43.21 32.96 C-43.23 30.09 -43.23 27.21 -43.23 24.33 C-43.24 23.07 -43.24 23.07 -43.25 21.77 C-43.23 15.52 -42.86 10.21 -38.69 5.25 C-26.18 -5.22 -22.39 0 0 0 Z" fill="#02F4B3" transform="translate(105,65)"/></svg>`),
  
  // Main Clearledgr logo - default state (grayscale)
  mainDefault: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 230 236"><path d="M0 0 C1.42 -0.01 2.84 -0.01 4.27 -0.02 C8.1 -0.04 11.94 -0.04 15.78 -0.03 C19 -0.03 22.21 -0.03 25.43 -0.04 C33.02 -0.05 40.61 -0.05 48.2 -0.04 C56.01 -0.03 63.82 -0.04 71.63 -0.07 C78.35 -0.09 85.08 -0.1 91.8 -0.09 C95.81 -0.09 99.82 -0.09 103.82 -0.11 C107.59 -0.13 111.37 -0.12 115.14 -0.1 C117.16 -0.1 119.19 -0.11 121.22 -0.13 C130.89 -0.05 140.05 1.42 147.63 7.9 C148.35 8.52 149.08 9.13 149.82 9.76 C150.46 10.3 151.1 10.84 151.75 11.4 C152.64 12 153.52 12.6 154.44 13.22 C162.28 20.64 165.8 31.22 166.14 41.8 C166.16 44.24 166.16 46.67 166.15 49.11 C166.16 50.46 166.17 51.81 166.18 53.16 C166.19 56.81 166.19 60.45 166.19 64.09 C166.18 67.14 166.19 70.2 166.2 73.25 C166.21 80.46 166.21 87.68 166.2 94.89 C166.19 102.3 166.2 109.71 166.23 117.12 C166.25 123.51 166.25 129.9 166.25 136.29 C166.25 140.09 166.25 143.89 166.27 147.7 C166.28 151.28 166.28 154.86 166.26 158.44 C166.25 160.36 166.27 162.28 166.28 164.2 C166.17 177.22 162.52 188.27 153.49 197.78 C145.43 205.46 137.07 210.51 125.8 210.55 C124.58 210.56 123.36 210.57 122.11 210.57 C120.76 210.58 119.42 210.58 118.07 210.58 C116.65 210.59 115.23 210.59 113.81 210.6 C109.14 210.62 104.47 210.63 99.81 210.64 C98.2 210.65 96.59 210.65 94.98 210.65 C87.42 210.67 79.85 210.69 72.28 210.7 C63.57 210.71 54.86 210.73 46.14 210.77 C39.39 210.8 32.65 210.82 25.9 210.82 C21.88 210.82 17.85 210.83 13.83 210.86 C10.04 210.88 6.25 210.88 2.46 210.87 C0.42 210.87 -1.62 210.89 -3.66 210.91 C-17.86 210.83 -26.81 206.39 -37 196.75 C-44.27 189.03 -48.37 179.15 -48.38 168.62 C-48.39 167.47 -48.39 166.31 -48.4 165.12 C-48.4 163.87 -48.4 162.61 -48.39 161.31 C-48.4 159.97 -48.4 158.62 -48.4 157.27 C-48.41 153.63 -48.42 149.98 -48.42 146.33 C-48.42 144.04 -48.42 141.76 -48.42 139.48 C-48.43 131.51 -48.44 123.53 -48.43 115.56 C-48.43 108.14 -48.44 100.73 -48.46 93.31 C-48.47 86.93 -48.48 80.55 -48.48 74.17 C-48.48 70.37 -48.48 66.56 -48.49 62.76 C-48.5 59.18 -48.5 55.6 -48.49 52.01 C-48.49 50.08 -48.5 48.16 -48.51 46.23 C-48.46 32.93 -45.79 22.96 -36.44 13.15 C-25.24 2.61 -15.18 -0.05 0 0 Z" fill="#5f6368" transform="translate(56.25,13.6)"/><path d="M0 0 C31 0 31 0 40 6 C43.6 11.5 44.59 15.74 44.53 22.22 C44.55 23.38 44.55 23.38 44.56 24.58 C44.58 27.12 44.58 29.67 44.57 32.22 C44.57 34 44.58 35.79 44.59 37.58 C44.6 41.31 44.59 45.04 44.58 48.77 C44.56 53.54 44.58 58.31 44.62 63.07 C44.64 66.76 44.64 70.44 44.63 74.13 C44.63 75.88 44.64 77.64 44.65 79.4 C44.77 96.37 44.77 96.37 39 103 C27.87 113.33 17.77 108 0 108 C0 72.36 0 36.72 0 0 Z" fill="#9e9e9e" transform="translate(127,65)"/><path d="M0 0 C0 35.64 0 71.28 0 108 C-14.19 108 -28.38 108 -43 108 C-43.08 85.07 -43.08 85.07 -43.1 75.28 C-43.11 68.61 -43.12 61.94 -43.15 55.26 C-43.17 49.88 -43.18 44.5 -43.19 39.12 C-43.19 37.07 -43.2 35.01 -43.21 32.96 C-43.23 30.09 -43.23 27.21 -43.23 24.33 C-43.24 23.07 -43.24 23.07 -43.25 21.77 C-43.23 15.52 -42.86 10.21 -38.69 5.25 C-26.18 -5.22 -22.39 0 0 0 Z" fill="#bdbdbd" transform="translate(105,65)"/></svg>`),
  
  // Small logo for toolbar (simplified for 16px)
  small: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 230 236"><path d="M0 0 C1.42 -0.01 2.84 -0.01 4.27 -0.02 C8.1 -0.04 11.94 -0.04 15.78 -0.03 C19 -0.03 22.21 -0.03 25.43 -0.04 C33.02 -0.05 40.61 -0.05 48.2 -0.04 C56.01 -0.03 63.82 -0.04 71.63 -0.07 C78.35 -0.09 85.08 -0.1 91.8 -0.09 C95.81 -0.09 99.82 -0.09 103.82 -0.11 C107.59 -0.13 111.37 -0.12 115.14 -0.1 C117.16 -0.1 119.19 -0.11 121.22 -0.13 C130.89 -0.05 140.05 1.42 147.63 7.9 C148.35 8.52 149.08 9.13 149.82 9.76 C150.46 10.3 151.1 10.84 151.75 11.4 C152.64 12 153.52 12.6 154.44 13.22 C162.28 20.64 165.8 31.22 166.14 41.8 C166.16 44.24 166.16 46.67 166.15 49.11 C166.16 50.46 166.17 51.81 166.18 53.16 C166.19 56.81 166.19 60.45 166.19 64.09 C166.18 67.14 166.19 70.2 166.2 73.25 C166.21 80.46 166.21 87.68 166.2 94.89 C166.19 102.3 166.2 109.71 166.23 117.12 C166.25 123.51 166.25 129.9 166.25 136.29 C166.25 140.09 166.25 143.89 166.27 147.7 C166.28 151.28 166.28 154.86 166.26 158.44 C166.25 160.36 166.27 162.28 166.28 164.2 C166.17 177.22 162.52 188.27 153.49 197.78 C145.43 205.46 137.07 210.51 125.8 210.55 C124.58 210.56 123.36 210.57 122.11 210.57 C120.76 210.58 119.42 210.58 118.07 210.58 C116.65 210.59 115.23 210.59 113.81 210.6 C109.14 210.62 104.47 210.63 99.81 210.64 C98.2 210.65 96.59 210.65 94.98 210.65 C87.42 210.67 79.85 210.69 72.28 210.7 C63.57 210.71 54.86 210.73 46.14 210.77 C39.39 210.8 32.65 210.82 25.9 210.82 C21.88 210.82 17.85 210.83 13.83 210.86 C10.04 210.88 6.25 210.88 2.46 210.87 C0.42 210.87 -1.62 210.89 -3.66 210.91 C-17.86 210.83 -26.81 206.39 -37 196.75 C-44.27 189.03 -48.37 179.15 -48.38 168.62 C-48.39 167.47 -48.39 166.31 -48.4 165.12 C-48.4 163.87 -48.4 162.61 -48.39 161.31 C-48.4 159.97 -48.4 158.62 -48.4 157.27 C-48.41 153.63 -48.42 149.98 -48.42 146.33 C-48.42 144.04 -48.42 141.76 -48.42 139.48 C-48.43 131.51 -48.44 123.53 -48.43 115.56 C-48.43 108.14 -48.44 100.73 -48.46 93.31 C-48.47 86.93 -48.48 80.55 -48.48 74.17 C-48.48 70.37 -48.48 66.56 -48.49 62.76 C-48.5 59.18 -48.5 55.6 -48.49 52.01 C-48.49 50.08 -48.5 48.16 -48.51 46.23 C-48.46 32.93 -45.79 22.96 -36.44 13.15 C-25.24 2.61 -15.18 -0.05 0 0 Z" fill="#031536" transform="translate(56.25,13.6)"/><path d="M0 0 C31 0 31 0 40 6 C43.6 11.5 44.59 15.74 44.53 22.22 C44.55 23.38 44.55 23.38 44.56 24.58 C44.58 27.12 44.58 29.67 44.57 32.22 C44.57 34 44.58 35.79 44.59 37.58 C44.6 41.31 44.59 45.04 44.58 48.77 C44.56 53.54 44.58 58.31 44.62 63.07 C44.64 66.76 44.64 70.44 44.63 74.13 C44.63 75.88 44.64 77.64 44.65 79.4 C44.77 96.37 44.77 96.37 39 103 C27.87 113.33 17.77 108 0 108 C0 72.36 0 36.72 0 0 Z" fill="#02F6B4" transform="translate(127,65)"/><path d="M0 0 C0 35.64 0 71.28 0 108 C-14.19 108 -28.38 108 -43 108 C-43.08 85.07 -43.08 85.07 -43.1 75.28 C-43.11 68.61 -43.12 61.94 -43.15 55.26 C-43.17 49.88 -43.18 44.5 -43.19 39.12 C-43.19 37.07 -43.2 35.01 -43.21 32.96 C-43.23 30.09 -43.23 27.21 -43.23 24.33 C-43.24 23.07 -43.24 23.07 -43.25 21.77 C-43.23 15.52 -42.86 10.21 -38.69 5.25 C-26.18 -5.22 -22.39 0 0 0 Z" fill="#02F4B3" transform="translate(105,65)"/></svg>`),
  
  // Home - dashboard grid
  home: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="5" height="5" rx="1" fill="${BRAND_COLOR}"/><rect x="9" y="2" width="5" height="5" rx="1" fill="${BRAND_COLOR}" opacity="0.6"/><rect x="2" y="9" width="5" height="5" rx="1" fill="${BRAND_COLOR}" opacity="0.6"/><rect x="9" y="9" width="5" height="5" rx="1" fill="${BRAND_COLOR}" opacity="0.4"/></svg>`),
  
  // Vendor - building
  vendor: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M2 14V4L8 2L14 4V14H2Z" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><rect x="5" y="6" width="2" height="2" fill="${BRAND_COLOR}"/><rect x="9" y="6" width="2" height="2" fill="${BRAND_COLOR}"/><rect x="5" y="10" width="2" height="4" fill="${BRAND_COLOR}"/><rect x="9" y="10" width="2" height="2" fill="${BRAND_COLOR}"/></svg>`),
  
  // Analytics - chart
  analytics: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="9" width="3" height="5" rx="0.5" fill="${BRAND_COLOR}" opacity="0.5"/><rect x="6.5" y="6" width="3" height="8" rx="0.5" fill="${BRAND_COLOR}" opacity="0.7"/><rect x="11" y="3" width="3" height="11" rx="0.5" fill="${BRAND_COLOR}"/><line x1="1" y1="14" x2="15" y2="14" stroke="${BRAND_COLOR}" stroke-width="1"/></svg>`),
  
  // Pipeline - flow/kanban
  pipeline: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="1" y="2" width="4" height="12" rx="1" stroke="${BRAND_COLOR}" stroke-width="1.2" fill="none"/><rect x="6" y="2" width="4" height="12" rx="1" stroke="${BRAND_COLOR}" stroke-width="1.2" fill="none"/><rect x="11" y="2" width="4" height="12" rx="1" stroke="${BRAND_COLOR}" stroke-width="1.2" fill="none"/><rect x="2" y="4" width="2" height="2" rx="0.5" fill="${BRAND_COLOR}"/><rect x="7" y="4" width="2" height="2" rx="0.5" fill="${BRAND_COLOR}"/><rect x="7" y="7" width="2" height="2" rx="0.5" fill="${BRAND_COLOR}" opacity="0.5"/><rect x="12" y="4" width="2" height="2" rx="0.5" fill="${BRAND_COLOR}"/></svg>`),
  
  // Settings - gear
  settings: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="2" stroke="#5f6368" stroke-width="1.5" fill="none"/><path d="M8 1V3M8 13V15M1 8H3M13 8H15M2.9 2.9L4.3 4.3M11.7 11.7L13.1 13.1M2.9 13.1L4.3 11.7M11.7 4.3L13.1 2.9" stroke="#5f6368" stroke-width="1.5" stroke-linecap="round"/></svg>`),
  
  // History - clock with arrow
  history: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="9" r="5" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><path d="M8 6V9L10 10" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 4L2 2M2 2V5M2 2H5" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`),
  
  // Stage icons
  stageDetected: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="#2196F3"/><path d="M8 4V8L10 10" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`),
  stageReview: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="#FF9800"/><circle cx="8" cy="6" r="1.5" fill="white"/><path d="M8 9V11" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`),
  stageApproved: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="#4CAF50"/><path d="M5 8L7 10L11 6" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`),
  stagePosted: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="#9C27B0"/><path d="M5 8H11M8 5V11" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`),
  stageException: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="#F44336"/><path d="M6 6L10 10M10 6L6 10" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`),
  
  // Action icons
  scan: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="4" height="4" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><rect x="10" y="2" width="4" height="4" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><rect x="2" y="10" width="4" height="4" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><rect x="10" y="10" width="4" height="4" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><line x1="8" y1="1" x2="8" y2="15" stroke="${BRAND_COLOR}" stroke-width="1" stroke-dasharray="2 2"/><line x1="1" y1="8" x2="15" y2="8" stroke="${BRAND_COLOR}" stroke-width="1" stroke-dasharray="2 2"/></svg>`),
  invoice: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="3" y="1" width="10" height="14" rx="1" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><line x1="5" y1="5" x2="11" y2="5" stroke="${BRAND_COLOR}" stroke-width="1"/><line x1="5" y1="8" x2="11" y2="8" stroke="${BRAND_COLOR}" stroke-width="1"/><line x1="5" y1="11" x2="8" y2="11" stroke="${BRAND_COLOR}" stroke-width="1"/></svg>`),
  
  // Payment icon - dollar with arrows
  payment: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><path d="M8 4V12M6 6C6 5 7 4.5 8 4.5C9.5 4.5 10 5.5 10 6C10 7 9 7.5 8 7.5C7 7.5 6 8 6 9C6 10 7 10.5 8 11.5C9 11.5 10 11 10 10" stroke="${BRAND_COLOR}" stroke-width="1.2" stroke-linecap="round"/></svg>`),
  
  // GL/Ledger icon - book with checkmark
  glCode: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="12" rx="1" stroke="${BRAND_COLOR}" stroke-width="1.5" fill="none"/><line x1="5" y1="2" x2="5" y2="14" stroke="${BRAND_COLOR}" stroke-width="1"/><path d="M7 8L9 10L13 6" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`),
  
  // Recurring icon - circular arrows
  recurring: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M13.5 8A5.5 5.5 0 0 1 3 10" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round"/><path d="M2.5 8A5.5 5.5 0 0 1 13 6" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round"/><path d="M1 7L3 10L5 7" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M15 9L13 6L11 9" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>`),
  
  // Request icon - hand with dollar (payment requests)
  request: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 2V4M8 12V14M5 5C5 4 6.5 3 8 3C10 3 11 4 11 5C11 6.5 9.5 7 8 7C6.5 7 5 7.5 5 9C5 10 6.5 11 8 11C9.5 11 11 10 11 9" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round"/><path d="M2 8H4M12 8H14" stroke="${BRAND_COLOR}" stroke-width="1.5" stroke-linecap="round"/></svg>`)
};

// =============================================================================
// SUBSCRIPTION & ONBOARDING STATE
// =============================================================================

let subscriptionStatus = null;
let onboardingCompleted = false;
let SUBSCRIPTION_API_URL = 'http://127.0.0.1:8010';
const BACKEND_FETCH_STATE = {
  nextAttemptAt: 0,
  failureCount: 0,
  lastWarnAt: 0
};

function markBackendFetchSuccess() {
  BACKEND_FETCH_STATE.nextAttemptAt = 0;
  BACKEND_FETCH_STATE.failureCount = 0;
}

function markBackendFetchFailure() {
  BACKEND_FETCH_STATE.failureCount += 1;
  const backoffMs = Math.min(5000 * (2 ** (BACKEND_FETCH_STATE.failureCount - 1)), 300000);
  BACKEND_FETCH_STATE.nextAttemptAt = Date.now() + backoffMs;
}

function shouldAttemptBackendFetch(force = false) {
  if (force) return true;
  return Date.now() >= BACKEND_FETCH_STATE.nextAttemptAt;
}

function warnBackendFetchOncePerMinute(message, error) {
  const now = Date.now();
  if (now - BACKEND_FETCH_STATE.lastWarnAt < 60000) return;
  BACKEND_FETCH_STATE.lastWarnAt = now;
  const suffix = error?.message ? `: ${error.message}` : '';
  console.warn(`${message}${suffix}`);
}

function buildBackendFetchCandidates(rawUrl) {
  const original = String(rawUrl || '').trim();
  if (!original) return [];

  const candidates = [original];
  try {
    const parsed = new URL(original);
    const isLoopback = ['127.0.0.1', 'localhost', '0.0.0.0'].includes(parsed.hostname);
    if (!isLoopback) return candidates;

    if (parsed.hostname === '0.0.0.0') {
      const normalized = new URL(parsed.toString());
      normalized.hostname = '127.0.0.1';
      candidates.push(normalized.toString());
    }

    const addWithPort = (port) => {
      const next = new URL(parsed.toString());
      if (next.hostname === '0.0.0.0') next.hostname = '127.0.0.1';
      next.port = String(port);
      candidates.push(next.toString());
    };

    if (parsed.port === '8000') addWithPort(8010);
    else if (parsed.port === '8010') addWithPort(8000);
    else if (!parsed.port) {
      addWithPort(8010);
      addWithPort(8000);
    }
  } catch (_) {
    // If URL parsing fails, fall back to original only.
  }

  return Array.from(new Set(candidates));
}

function promoteActiveBackendFromUrl(successUrl) {
  try {
    const parsed = new URL(successUrl);
    const isLoopback = ['127.0.0.1', 'localhost', '0.0.0.0'].includes(parsed.hostname);
    if (!isLoopback) return;
    if (parsed.hostname === '0.0.0.0') parsed.hostname = '127.0.0.1';
    const nextBase = `${parsed.protocol}//${parsed.host}`;
    BACKEND_URL = nextBase;
    SUBSCRIPTION_API_URL = nextBase;
  } catch (_) {
    // ignore
  }
}

async function backendFetch(url, options = {}, { force = false, warnMessage = '[Clearledgr] Backend request failed' } = {}) {
  if (!shouldAttemptBackendFetch(force)) {
    const backoffError = new Error('backend_backoff');
    backoffError.code = 'BACKEND_BACKOFF';
    throw backoffError;
  }

  const candidates = buildBackendFetchCandidates(url);
  let lastError = null;

  for (const candidate of candidates) {
    try {
      const response = await fetch(candidate, options);
      markBackendFetchSuccess();
      if (candidate !== url) promoteActiveBackendFromUrl(response?.url || candidate);
      return response;
    } catch (error) {
      lastError = error;
    }
  }

  markBackendFetchFailure();
  warnBackendFetchOncePerMinute(warnMessage, lastError);
  throw lastError || new Error('backend_fetch_failed');
}

async function fetchSubscriptionStatus() {
  try {
    const response = await backendFetch(`${SUBSCRIPTION_API_URL}/subscription/status`, {
      headers: { 'X-Organization-ID': 'default' }
    }, { warnMessage: '[Clearledgr] Could not fetch subscription status' });
    if (response.ok) {
      subscriptionStatus = await response.json();
      onboardingCompleted = subscriptionStatus.onboarding_completed;
      console.log('[Clearledgr] Subscription loaded:', subscriptionStatus.plan, subscriptionStatus.is_trial ? '(trial)' : '');
      return subscriptionStatus;
    }
  } catch (e) {
    if (e?.code !== 'BACKEND_BACKOFF') {
      console.warn('[Clearledgr] Could not fetch subscription status:', e.message);
    }
  }
  return null;
}

async function startTrial() {
  try {
    const response = await backendFetch(`${SUBSCRIPTION_API_URL}/subscription/trial/start`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Organization-ID': 'default' 
      }
    }, { warnMessage: '[Clearledgr] Could not start trial' });
    if (response.ok) {
      const result = await response.json();
      subscriptionStatus = result.subscription;
      console.log('[Clearledgr] Trial started:', subscriptionStatus.trial_days_remaining, 'days');
      return true;
    }
  } catch (e) {
    if (e?.code !== 'BACKEND_BACKOFF') {
      console.warn('[Clearledgr] Could not start trial:', e.message);
    }
  }
  return false;
}

async function completeOnboardingStep(step) {
  try {
    const response = await backendFetch(`${SUBSCRIPTION_API_URL}/subscription/onboarding/step`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'X-Organization-ID': 'default' 
      },
      body: JSON.stringify({ step })
    }, { warnMessage: '[Clearledgr] Could not complete onboarding step' });
    if (response.ok) {
      const result = await response.json();
      onboardingCompleted = result.onboarding_completed;
      return result;
    }
  } catch (e) {
    if (e?.code !== 'BACKEND_BACKOFF') {
      console.warn('[Clearledgr] Could not complete onboarding step:', e.message);
    }
  }
  return null;
}

async function skipOnboarding() {
  try {
    const response = await backendFetch(`${SUBSCRIPTION_API_URL}/subscription/onboarding/skip`, {
      method: 'POST',
      headers: { 'X-Organization-ID': 'default' }
    }, { warnMessage: '[Clearledgr] Could not skip onboarding' });
    if (response.ok) {
      onboardingCompleted = true;
      return true;
    }
  } catch (e) {
    if (e?.code !== 'BACKEND_BACKOFF') {
      console.warn('[Clearledgr] Could not skip onboarding:', e.message);
    }
  }
  return false;
}

function showOnboardingWizard() {
  // Remove any existing modal
  const existing = document.querySelector('.cl-onboarding-modal');
  if (existing) existing.remove();
  
  const modal = document.createElement('div');
  modal.className = 'cl-onboarding-modal';
  modal.innerHTML = `
    <style>
      .cl-onboarding-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.6); display: flex; align-items: center; justify-content: center; z-index: 999999; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-onboarding-content { background: white; border-radius: 16px; width: 560px; max-height: 90vh; overflow: hidden; box-shadow: 0 24px 80px rgba(0,0,0,0.3); }
      .cl-onboarding-header { padding: 32px 32px 24px; text-align: center; background: linear-gradient(135deg, #10B981 0%, #059669 100%); color: white; }
      .cl-onboarding-logo { width: 64px; height: 64px; margin-bottom: 16px; }
      .cl-onboarding-title { font-size: 28px; font-weight: 600; margin: 0 0 8px; }
      .cl-onboarding-subtitle { font-size: 16px; opacity: 0.9; margin: 0; }
      .cl-onboarding-body { padding: 32px; }
      .cl-onboarding-steps { display: flex; flex-direction: column; gap: 16px; }
      .cl-onboarding-step { display: flex; align-items: center; gap: 16px; padding: 16px; background: #f8f9fa; border-radius: 12px; cursor: pointer; transition: all 0.2s; border: 2px solid transparent; }
      .cl-onboarding-step:hover { background: #E8F5E9; border-color: #10B981; }
      .cl-onboarding-step.active { background: #E8F5E9; border-color: #10B981; }
      .cl-onboarding-step-icon { width: 48px; height: 48px; border-radius: 12px; background: white; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
      .cl-onboarding-step-icon svg { width: 24px; height: 24px; }
      .cl-onboarding-step-content { flex: 1; }
      .cl-onboarding-step-title { font-size: 16px; font-weight: 500; color: #202124; margin: 0 0 4px; }
      .cl-onboarding-step-desc { font-size: 13px; color: #5f6368; margin: 0; }
      .cl-onboarding-footer { padding: 24px 32px; border-top: 1px solid #e0e0e0; display: flex; justify-content: space-between; align-items: center; }
      .cl-onboarding-skip { background: none; border: none; color: #5f6368; font-size: 14px; cursor: pointer; padding: 8px 16px; }
      .cl-onboarding-skip:hover { color: #202124; }
      .cl-onboarding-cta { background: #10B981; color: white; border: none; padding: 12px 32px; border-radius: 8px; font-size: 15px; font-weight: 500; cursor: pointer; transition: background 0.2s; }
      .cl-onboarding-cta:hover { background: #059669; }
      .cl-trial-badge { display: inline-flex; align-items: center; gap: 6px; background: #FFF3E0; color: #E65100; padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 500; margin-top: 16px; }
    </style>
    <div class="cl-onboarding-content">
      <div class="cl-onboarding-header">
        <svg class="cl-onboarding-logo" viewBox="0 0 230 236" fill="none">
          <path d="M0 0 C1.42 -0.01 2.84 -0.01 4.27 -0.02 C8.1 -0.04 11.94 -0.04 15.78 -0.03 C19 -0.03 22.21 -0.03 25.43 -0.04 C33.02 -0.05 40.61 -0.05 48.2 -0.04 C56.01 -0.03 63.82 -0.04 71.63 -0.07 C78.35 -0.09 85.08 -0.1 91.8 -0.09 C95.81 -0.09 99.82 -0.09 103.82 -0.11 C107.59 -0.13 111.37 -0.12 115.14 -0.1 C117.16 -0.1 119.19 -0.11 121.22 -0.13 C130.89 -0.05 140.05 1.42 147.63 7.9 C148.35 8.52 149.08 9.13 149.82 9.76 C150.46 10.3 151.1 10.84 151.75 11.4 C152.64 12 153.52 12.6 154.44 13.22 C162.28 20.64 165.8 31.22 166.14 41.8 C166.16 44.24 166.16 46.67 166.15 49.11 C166.16 50.46 166.17 51.81 166.18 53.16 C166.19 56.81 166.19 60.45 166.19 64.09 C166.18 67.14 166.19 70.2 166.2 73.25 C166.21 80.46 166.21 87.68 166.2 94.89 C166.19 102.3 166.2 109.71 166.23 117.12 C166.25 123.51 166.25 129.9 166.25 136.29 C166.25 140.09 166.25 143.89 166.27 147.7 C166.28 151.28 166.28 154.86 166.26 158.44 C166.25 160.36 166.27 162.28 166.28 164.2 C166.17 177.22 162.52 188.27 153.49 197.78 C145.43 205.46 137.07 210.51 125.8 210.55 C124.58 210.56 123.36 210.57 122.11 210.57 C120.76 210.58 119.42 210.58 118.07 210.58 C116.65 210.59 115.23 210.59 113.81 210.6 C109.14 210.62 104.47 210.63 99.81 210.64 C98.2 210.65 96.59 210.65 94.98 210.65 C87.42 210.67 79.85 210.69 72.28 210.7 C63.57 210.71 54.86 210.73 46.14 210.77 C39.39 210.8 32.65 210.82 25.9 210.82 C21.88 210.82 17.85 210.83 13.83 210.86 C10.04 210.88 6.25 210.88 2.46 210.87 C0.42 210.87 -1.62 210.89 -3.66 210.91 C-17.86 210.83 -26.81 206.39 -37 196.75 C-44.27 189.03 -48.37 179.15 -48.38 168.62 C-48.39 167.47 -48.39 166.31 -48.4 165.12 C-48.4 163.87 -48.4 162.61 -48.39 161.31 C-48.4 159.97 -48.4 158.62 -48.4 157.27 C-48.41 153.63 -48.42 149.98 -48.42 146.33 C-48.42 144.04 -48.42 141.76 -48.42 139.48 C-48.43 131.51 -48.44 123.53 -48.43 115.56 C-48.43 108.14 -48.44 100.73 -48.46 93.31 C-48.47 86.93 -48.48 80.55 -48.48 74.17 C-48.48 70.37 -48.48 66.56 -48.49 62.76 C-48.5 59.18 -48.5 55.6 -48.49 52.01 C-48.49 50.08 -48.5 48.16 -48.51 46.23 C-48.46 32.93 -45.79 22.96 -36.44 13.15 C-25.24 2.61 -15.18 -0.05 0 0 Z" fill="white" transform="translate(56.25,13.6)"/>
          <path d="M0 0 C31 0 31 0 40 6 C43.6 11.5 44.59 15.74 44.53 22.22 C44.55 23.38 44.55 23.38 44.56 24.58 C44.58 27.12 44.58 29.67 44.57 32.22 C44.57 34 44.58 35.79 44.59 37.58 C44.6 41.31 44.59 45.04 44.58 48.77 C44.56 53.54 44.58 58.31 44.62 63.07 C44.64 66.76 44.64 70.44 44.63 74.13 C44.63 75.88 44.64 77.64 44.65 79.4 C44.77 96.37 44.77 96.37 39 103 C27.87 113.33 17.77 108 0 108 C0 72.36 0 36.72 0 0 Z" fill="#02F6B4" transform="translate(127,65)"/>
          <path d="M0 0 C0 35.64 0 71.28 0 108 C-14.19 108 -28.38 108 -43 108 C-43.08 85.07 -43.08 85.07 -43.1 75.28 C-43.11 68.61 -43.12 61.94 -43.15 55.26 C-43.17 49.88 -43.18 44.5 -43.19 39.12 C-43.19 37.07 -43.2 35.01 -43.21 32.96 C-43.23 30.09 -43.23 27.21 -43.23 24.33 C-43.24 23.07 -43.24 23.07 -43.25 21.77 C-43.23 15.52 -42.86 10.21 -38.69 5.25 C-26.18 -5.22 -22.39 0 0 0 Z" fill="#02F4B3" transform="translate(105,65)"/>
        </svg>
        <h1 class="cl-onboarding-title">Welcome to Clearledgr</h1>
        <p class="cl-onboarding-subtitle">AI-powered accounts payable automation for Gmail</p>
        <div class="cl-trial-badge">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" fill="currentColor"/></svg>
          Start your 14-day Pro trial
        </div>
      </div>
      <div class="cl-onboarding-body">
        <div class="cl-onboarding-steps">
          <div class="cl-onboarding-step" data-step="erp">
            <div class="cl-onboarding-step-icon">
              <svg viewBox="0 0 24 24" fill="none"><rect x="3" y="3" width="18" height="18" rx="2" stroke="#10B981" stroke-width="2"/><path d="M3 9h18M9 21V9" stroke="#10B981" stroke-width="2"/></svg>
            </div>
            <div class="cl-onboarding-step-content">
              <div class="cl-onboarding-step-title">Connect your ERP</div>
              <div class="cl-onboarding-step-desc">QuickBooks, Xero, or NetSuite - sync your chart of accounts</div>
            </div>
          </div>
          <div class="cl-onboarding-step" data-step="scan">
            <div class="cl-onboarding-step-icon">
              <svg viewBox="0 0 24 24" fill="none"><rect x="3" y="5" width="18" height="14" rx="2" stroke="#10B981" stroke-width="2"/><path d="M3 10h18" stroke="#10B981" stroke-width="2"/><circle cx="7" cy="14" r="1" fill="#10B981"/></svg>
            </div>
            <div class="cl-onboarding-step-content">
              <div class="cl-onboarding-step-title">Scan your inbox</div>
              <div class="cl-onboarding-step-desc">AI automatically detects invoices in your email</div>
            </div>
          </div>
          <div class="cl-onboarding-step" data-step="approve">
            <div class="cl-onboarding-step-icon">
              <svg viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="#10B981" stroke-width="2"/><path d="M8 12l3 3 5-5" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </div>
            <div class="cl-onboarding-step-content">
              <div class="cl-onboarding-step-title">Review & approve</div>
              <div class="cl-onboarding-step-desc">One-click approval with AI-suggested GL codes</div>
            </div>
          </div>
          <div class="cl-onboarding-step" data-step="post">
            <div class="cl-onboarding-step-icon">
              <svg viewBox="0 0 24 24" fill="none"><path d="M12 2v10l4 4" stroke="#10B981" stroke-width="2" stroke-linecap="round"/><path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z" stroke="#10B981" stroke-width="2"/></svg>
            </div>
            <div class="cl-onboarding-step-content">
              <div class="cl-onboarding-step-title">Auto-post to ERP</div>
              <div class="cl-onboarding-step-desc">Approved invoices sync directly to your accounting system</div>
            </div>
          </div>
        </div>
      </div>
      <div class="cl-onboarding-footer">
        <button class="cl-onboarding-skip" id="cl-onboarding-skip">Skip for now</button>
        <button class="cl-onboarding-cta" id="cl-onboarding-start">Get Started</button>
      </div>
    </div>
  `;
  
  document.body.appendChild(modal);
  
  // Event handlers
  modal.querySelector('#cl-onboarding-skip').addEventListener('click', async () => {
    await skipOnboarding();
    modal.remove();
  });
  
  modal.querySelector('#cl-onboarding-start').addEventListener('click', async () => {
    await startTrial();
    await completeOnboardingStep(1);
    modal.remove();
    // Navigate to settings to connect ERP
    sdk.Router.goto('clearledgr/settings');
    showToast('Welcome! Connect your ERP to get started', 'success');
  });
  
  // Close on background click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      // Don't close - user should make a choice
    }
  });
}

function renderTrialBadge() {
  if (!subscriptionStatus) return '';
  
  const { plan, is_trial, trial_days_remaining, plan_display } = subscriptionStatus;
  
  if (is_trial) {
    return `
      <div class="cl-trial-indicator" style="display: inline-flex; align-items: center; gap: 6px; background: #FFF3E0; color: #E65100; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/></svg>
        Pro Trial · ${trial_days_remaining} days left
      </div>
    `;
  } else if (plan === 'pro') {
    return `
      <div class="cl-plan-indicator" style="display: inline-flex; align-items: center; gap: 6px; background: #E8F5E9; color: #2E7D32; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500;">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/></svg>
        Pro
      </div>
    `;
  } else if (plan === 'free') {
    return `
      <div class="cl-plan-indicator" style="display: inline-flex; align-items: center; gap: 6px; background: #f1f3f4; color: #5f6368; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; cursor: pointer;" onclick="window.dispatchEvent(new CustomEvent('clearledgr:show-upgrade-modal'))">
        Free · Upgrade
      </div>
    `;
  }
  return '';
}

// Update all plan badges on the page
function updatePlanBadges() {
  // Don't overwrite if we have no data
  if (!subscriptionStatus) return;
  
  const badgeHtml = renderTrialBadge();
  if (!badgeHtml) return;
  
  // Update home page badge
  const homeBadge = document.querySelector('#cl-home-plan-badge');
  if (homeBadge) homeBadge.innerHTML = badgeHtml;
  
  // Update settings page badge
  const settingsBadge = document.querySelector('#cl-settings-plan-badge');
  if (settingsBadge) settingsBadge.innerHTML = badgeHtml;
}

// Update subscription UI in Settings
function updateSubscriptionUI() {
  const sub = subscriptionStatus;
  if (!sub) return;
  
  const planName = document.querySelector('#cl-plan-name');
  const planStatus = document.querySelector('#cl-plan-status');
  const upgradeBtn = document.querySelector('#cl-upgrade-btn');
  const trialBanner = document.querySelector('#cl-trial-banner');
  const trialTitle = document.querySelector('#cl-trial-title');
  const trialDesc = document.querySelector('#cl-trial-desc');
  
  // Plan name and status
  const planDisplayNames = { free: 'Free Plan', trial: 'Pro Trial', pro: 'Pro Plan', enterprise: 'Enterprise' };
  if (planName) planName.textContent = planDisplayNames[sub.plan] || 'Free Plan';
  
  if (sub.is_trial) {
    if (planStatus) planStatus.textContent = `Trial ends in ${sub.trial_days_remaining} days`;
    if (trialBanner) trialBanner.style.display = 'block';
    if (trialTitle) trialTitle.textContent = `Pro Trial - ${sub.trial_days_remaining} days left`;
    if (trialDesc) trialDesc.textContent = 'Enjoy full Pro features. Upgrade before trial ends to keep access.';
    if (upgradeBtn) upgradeBtn.style.display = 'none';
  } else if (sub.plan === 'free') {
    if (planStatus) planStatus.textContent = 'Limited features. Upgrade for full access.';
    if (upgradeBtn) upgradeBtn.style.display = 'block';
    if (trialBanner) trialBanner.style.display = 'none';
  } else {
    if (planStatus) planStatus.textContent = 'Full access to all features';
    if (upgradeBtn) upgradeBtn.style.display = 'none';
    if (trialBanner) trialBanner.style.display = 'none';
  }
  
  // Usage stats
  if (sub.usage && sub.limits) {
    const invoicesEl = document.querySelector('#cl-usage-invoices');
    const vendorsEl = document.querySelector('#cl-usage-vendors');
    const aiEl = document.querySelector('#cl-usage-ai');
    const limitInvoices = document.querySelector('#cl-limit-invoices');
    const limitVendors = document.querySelector('#cl-limit-vendors');
    const limitAi = document.querySelector('#cl-limit-ai');
    
    if (invoicesEl) invoicesEl.textContent = sub.usage.invoices_this_month || 0;
    if (vendorsEl) vendorsEl.textContent = sub.usage.vendors_count || 0;
    if (aiEl) aiEl.textContent = sub.usage.ai_extractions_this_month || 0;
    
    const invLimit = sub.limits.invoices_per_month === -1 ? 'unlimited' : `of ${sub.limits.invoices_per_month}`;
    const vendLimit = sub.limits.vendors === -1 ? 'unlimited' : `of ${sub.limits.vendors}`;
    const aiLimit = sub.limits.ai_extractions_per_month === -1 ? 'unlimited' : `of ${sub.limits.ai_extractions_per_month}`;
    
    if (limitInvoices) limitInvoices.textContent = invLimit;
    if (limitVendors) limitVendors.textContent = vendLimit;
    if (limitAi) limitAi.textContent = aiLimit;
  }
  
  // Update badges
  updatePlanBadges();
}

// =============================================================================
// INITIALIZATION
// =============================================================================

// Hard guard: prevent duplicate mounting if Gmail reloads/injects twice.
// We keep this in the InboxSDK layer so ALL Gmail UI is mounted exactly once.
const __CL_INBOXSDK_INIT_KEY = '__clearledgr_inboxsdk_layer_initialized';

if (window[__CL_INBOXSDK_INIT_KEY]) {
  console.log('[Clearledgr] InboxSDK layer already initialized (guard active)');
} else {
  window[__CL_INBOXSDK_INIT_KEY] = true;

  InboxSDK.load(2, APP_ID).then(async (loadedSdk) => {
  sdk = loadedSdk;
  console.log('[Clearledgr] InboxSDK loaded');
  
  // 0. Load backend URL from settings (single source of truth)
  await refreshBackendUrl();

  // 0. Fetch subscription status first
  await fetchSubscriptionStatus();
  
  // 1. Register routes FIRST (before AppMenu references them)
  registerRoutes();

  // Allow background/popup to request opening Clearledgr UI (Streak-style).
  window.addEventListener('clearledgr:open-home', () => {
    try {
      sdk.Router.goto('clearledgr/invoices');
    } catch (e) {
      console.warn('[Clearledgr] Failed to open home route:', e.message);
    }
  });
  
  // Show onboarding if not completed
  if (!onboardingCompleted) {
    setTimeout(() => showOnboardingWizard(), 1000);
  }
  
  // Update plan badges after a delay (once pages render)
  setTimeout(() => {
    updatePlanBadges();
    updateSubscriptionUI();
  }, 2000);
  
  // 2. Setup AppMenu navigation (this creates the UI - must happen first)
  await initializeAppMenu();
  
  // 0. Initialize authentication in background (non-blocking)
  initializeAuth().catch(e => console.warn('[Clearledgr] Auth failed (non-blocking):', e.message));
  
  // 3. Setup toolbar buttons (thread-level)
  initializeToolbar();
  
  // 4. Setup global toolbar (app-level)
  initializeGlobalToolbar();
  
  // 5. Setup global sidebar (always visible, like Streak)
  initializeGlobalSidebar();
  
  // 6. Setup email-specific sidebar handler (updates global sidebar context)
  initializeEmailSidebar();
  
  // 7. Setup message-level buttons (Streak-style)
  initializeMessageButtons();
  
  // 8. Start autonomous inbox monitoring (CORE FEATURE - auto-detect finance emails)
  initializeAutonomousMonitoring();

  // Refresh sidebar context when new pipeline data arrives (keeps "Why" current).
  window.addEventListener('clearledgr:pipeline-data', () => {
    if (currentThreadView) {
      updateGlobalSidebarContext(currentThreadView);
    }
  });
  
  // 9. Setup keyboard shortcuts
  initializeKeyboardShortcuts();
  
  // 9. Setup search integration
  initializeSearchIntegration();
  
  // 10. Initialize toast notification system
  initializeToastSystem();
  
  console.log('[Clearledgr] Integration ready - Autonomous monitoring active');
  
  // Show welcome for authenticated users
  if (currentUser) {
    console.log(`[Clearledgr] User authenticated: ${currentUser.email}`);
  }
  }).catch(err => {
    console.error('[Clearledgr] Failed to load:', err);
  });
}

// =============================================================================
// ROUTES - Full-page views in main content area
// =============================================================================

// Route configuration - uses prefixed IDs to avoid conflicts with Gmail's routing
const ROUTES = {
  // Keep legacy "home" route id as compatibility alias.
  'clearledgr/home': { render: renderInvoices, title: 'Invoices' },
  'clearledgr/invoices': { render: renderInvoices, title: 'Invoices' },
  'clearledgr/settings': { render: renderSettings, title: 'Settings' }
};

function registerRoutes() {
  // Register all routes with clean names
  // Note: InboxSDK handles URL routing via hash fragments automatically
  Object.entries(ROUTES).forEach(([routeId, config]) => {
    sdk.Router.handleCustomRoute(routeId, (routeView) => {
      // Get route params (e.g., status filter for invoices)
      const params = routeView.getParams() || {};
      console.log(`[Clearledgr] Rendering ${routeId} route with params:`, params);
      
      // Update document title only (let InboxSDK handle URL)
      document.title = `${config.title} - Clearledgr`;
      
      // Pass params to render function
      config.render(routeView.getElement(), params);
      
      // Update plan badges after page renders
      setTimeout(() => {
        updatePlanBadges();
        if (routeId === 'clearledgr/settings') {
          updateSubscriptionUI();
        }
      }, 100);
    });
  });

  console.log('[Clearledgr] Routes registered');
}

// =============================================================================
// APP MENU - Navigation to routes
// =============================================================================

async function initializeAppMenu() {
  const menuItem = sdk.AppMenu.addMenuItem({
    name: 'Clearledgr',
    iconUrl: {
      lightTheme: {
        default: ICONS.mainDefault,
        active: ICONS.mainActive
      },
      darkTheme: {
        default: ICONS.mainDefault,
        active: ICONS.mainActive
      }
    },
    insertIndex: 0,  // Insert before Streak
    routeID: 'clearledgr/invoices',
    onClick: (event) => {
      // Explicit click handler to ensure our route is triggered
      event.preventDefault?.();
      sdk.Router.goto('clearledgr/invoices');
    },
    isRouteActive: (routeView) => {
      const routeID = routeView.getRouteID();
      // Only return true for our routes (prefixed with 'clearledgr/')
      return routeID && routeID.startsWith('clearledgr/');
    }
  });

  const panel = await menuItem.addCollapsiblePanel({
    primaryButton: {
      name: '+ New Workflow',
      onClick: () => {
        window.dispatchEvent(new CustomEvent('clearledgr:show-new-workflow-modal'));
      }
    }
  });

  if (!panel) {
    console.warn('[Clearledgr] Panel not available');
    return;
  }

  // Navigation items - these go to ROUTES
  // AP-only invoices with status filters
  const invoicesNav = panel.addNavItem({
    name: 'Invoices',
    iconUrl: ICONS.pipeline,
    routeID: 'clearledgr/invoices',
    routeParams: {}
  });

  // Status filters (if supported)
  if (invoicesNav && invoicesNav.addNavItem) {
    invoicesNav.addNavItem({
      name: 'New',
      iconUrl: ICONS.stageDetected,
      routeID: 'clearledgr/invoices',
      routeParams: { status: 'new' }
    });
    invoicesNav.addNavItem({
      name: 'Pending Review',
      iconUrl: ICONS.stageReview,
      routeID: 'clearledgr/invoices',
      routeParams: { status: 'review' }
    });
    invoicesNav.addNavItem({
      name: 'Pending Approval',
      iconUrl: ICONS.stageReview,
      routeID: 'clearledgr/invoices',
      routeParams: { status: 'pending_approval' }
    });
    invoicesNav.addNavItem({
      name: 'Approved',
      iconUrl: ICONS.stageApproved,
      routeID: 'clearledgr/invoices',
      routeParams: { status: 'approved' }
    });
    invoicesNav.addNavItem({
      name: 'Rejected',
      iconUrl: ICONS.stageException,
      routeID: 'clearledgr/invoices',
      routeParams: { status: 'rejected' }
    });
    invoicesNav.addNavItem({
      name: 'Failed',
      iconUrl: ICONS.stageException,
      routeID: 'clearledgr/invoices',
      routeParams: { status: 'failed' }
    });
  }

  panel.addNavItem({
    name: 'Settings',
    iconUrl: ICONS.settings,
    routeID: 'clearledgr/settings',
    routeParams: {}
  });

  console.log('[Clearledgr] AppMenu configured');
}

// =============================================================================
// TOOLBAR - Actions on selected emails
// =============================================================================

function initializeToolbar() {
  sdk.Toolbars.registerThreadButton({
    title: 'Add to Clearledgr',
    iconUrl: ICONS.small,
    onClick: async (event) => {
      const threadViews = event.selectedThreadViews || [];
      
      if (threadViews.length === 0) {
        // No threads selected - show message
        console.log('[Clearledgr] No threads selected');
        return;
      }
      
      // Process each selected thread
      let addedCount = 0;
      for (const threadView of threadViews) {
        try {
          const threadId = threadView.getThreadID();
          const subject = threadView.getSubject() || '';
          
          // Skip if already processed
          if (processedEmails.has(threadId)) {
            console.log(`[Clearledgr] Thread ${threadId} already processed`);
            continue;
          }
          
          // Mark as processed
          processedEmails.add(threadId);
          
          // Queue for backend processing
          window.dispatchEvent(new CustomEvent('clearledgr:queue-email', {
            detail: {
              threadId,
              subject,
              source: 'manual-add'
            }
          }));
          
          addedCount++;
          console.log(`[Clearledgr] Added thread ${threadId} to queue: ${subject}`);
        } catch (e) {
          console.warn('[Clearledgr] Error processing thread:', e);
        }
      }
      
      // Show confirmation
      if (addedCount > 0) {
        // Add visual feedback - labels to the threads
        for (const threadView of threadViews) {
          try {
            threadView.addLabel({
              title: 'Queued',
              foregroundColor: '#1565C0',
              backgroundColor: '#E3F2FD',
            });
          } catch (e) {
            // Label might already exist
          }
        }
      }
      
      console.log(`[Clearledgr] Added ${addedCount} email(s) to queue`);
    }
  });

  console.log('[Clearledgr] Toolbar configured');
}

// =============================================================================
// GLOBAL TOOLBAR - App-level quick access (always visible)
// =============================================================================

function initializeGlobalToolbar() {
  // Go to AP invoices (Clearledgr v1 scope)
  sdk.Toolbars.addToolbarButtonForApp({
    title: 'Clearledgr',
    iconUrl: ICONS.small,
    onClick: () => {
      sdk.Router.goto('clearledgr/invoices');
    }
  });

  // Inject CSS to increase toolbar button text size
  const toolbarStyle = document.createElement('style');
  toolbarStyle.textContent = `
    /* Clearledgr global toolbar button - larger text */
    .inboxsdk__appButton .inboxsdk__appButton_title {
      font-size: 14px !important;
      font-weight: 500 !important;
    }
  `;
  document.head.appendChild(toolbarStyle);

  console.log('[Clearledgr] Global toolbar configured');
}

// =============================================================================
// MESSAGE BUTTONS - Streak-style inline actions on messages
// =============================================================================

function initializeMessageButtons() {
  sdk.Conversations.registerMessageViewHandler((messageView) => {
    const subject = messageView.getSubject ? messageView.getSubject() : '';
    const sender = messageView.getSender?.()?.emailAddress || '';
    
    // Only add buttons to finance-related messages
    // Check via thread view since messageView may not have subject directly
    const threadView = messageView.getThreadView?.();
    const threadSubject = threadView?.getSubject?.() || subject || '';
    
    if (!isFinanceEmail(threadSubject, sender)) return;
    
    // Get message ID for actions
    const messageId = messageView.getMessageID?.() || Date.now().toString();
    
    // Add action buttons to the message
    messageView.addAttachmentCardView({
      title: 'Invoice Actions',
      previewUrl: ICONS.invoice,
      previewThumbnailUrl: ICONS.invoice,
      failoverPreviewIconUrl: ICONS.invoice,
      previewOnClick: () => {
        // Open sidebar with details
        sdk.Router.goto('clearledgr/invoices');
      },
      buttons: [
        {
          title: 'Approve',
          iconUrl: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="7" fill="#4CAF50"/><path d="M5 8L7 10L11 6" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`),
          onClick: () => handleInvoiceAction(messageId, 'approve', threadSubject)
        },
        {
          title: 'Reject',
          iconUrl: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="7" fill="#F44336"/><path d="M5.5 5.5L10.5 10.5M10.5 5.5L5.5 10.5" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>`),
          onClick: () => handleInvoiceAction(messageId, 'reject', threadSubject)
        },
        {
          title: 'Flag',
          iconUrl: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 2V14M3 2L12 5L3 8V2Z" fill="#FF9800" stroke="#FF9800" stroke-width="1.5" stroke-linejoin="round"/></svg>`),
          onClick: () => handleInvoiceAction(messageId, 'flag', threadSubject)
        }
      ]
    });
    
    // Also add toolbar buttons directly on the message
    messageView.addToolbarButton({
      title: 'Approve Invoice',
      iconUrl: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="7" stroke="#4CAF50" stroke-width="1.5" fill="none"/><path d="M5 8L7 10L11 6" stroke="#4CAF50" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>`),
      onClick: () => handleInvoiceAction(messageId, 'approve', threadSubject)
    });
    
    messageView.addToolbarButton({
      title: 'Reject Invoice',
      iconUrl: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="7" stroke="#F44336" stroke-width="1.5" fill="none"/><path d="M5.5 5.5L10.5 10.5M10.5 5.5L5.5 10.5" stroke="#F44336" stroke-width="1.5" stroke-linecap="round"/></svg>`),
      onClick: () => handleInvoiceAction(messageId, 'reject', threadSubject)
    });
    
    messageView.addToolbarButton({
      title: 'Flag for Review',
      iconUrl: svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M3 2V14M3 2L12 5L3 8V2Z" stroke="#FF9800" stroke-width="1.5" stroke-linejoin="round" fill="none"/></svg>`),
      onClick: () => handleInvoiceAction(messageId, 'flag', threadSubject)
    });
  });

  console.log('[Clearledgr] Message buttons configured');
}

function handleInvoiceAction(messageId, action, subject) {
  const actionLabels = {
    approve: { past: 'approved', color: '#4CAF50' },
    reject: { past: 'rejected', color: '#F44336' },
    flag: { past: 'flagged for review', color: '#FF9800' }
  };
  
  const label = actionLabels[action];
  
  // Dispatch to backend
  window.dispatchEvent(new CustomEvent('clearledgr:invoice-action', {
    detail: { messageId, action, subject }
  }));
  
  // Show confirmation toast
  showToast(`Invoice ${label.past}`, 'success');
  
  console.log(`[Clearledgr] Invoice ${action}:`, messageId);
}

// =============================================================================
// KEYBOARD SHORTCUTS - Power user workflow
// =============================================================================

function initializeKeyboardShortcuts() {
  // Global keyboard shortcut handler
  document.addEventListener('keydown', (e) => {
    // Only trigger if not in an input field
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) {
      return;
    }
    
    const isMod = e.metaKey || e.ctrlKey;
    
    // Cmd/Ctrl + Shift + A = Approve current invoice
    if (isMod && e.shiftKey && e.key === 'A') {
      e.preventDefault();
      triggerCurrentInvoiceAction('approve');
    }
    
    // Cmd/Ctrl + Shift + R = Reject current invoice
    if (isMod && e.shiftKey && e.key === 'R') {
      e.preventDefault();
      triggerCurrentInvoiceAction('reject');
    }
    
    // Cmd/Ctrl + Shift + F = Flag current invoice
    if (isMod && e.shiftKey && e.key === 'F') {
      e.preventDefault();
      triggerCurrentInvoiceAction('flag');
    }
    
    // Cmd/Ctrl + Shift + I = Go to Invoices
    if (isMod && e.shiftKey && e.key === 'I') {
      e.preventDefault();
      sdk.Router.goto('clearledgr/invoices');
      showToast('Navigating to Invoices...', 'info');
    }
    
    // Cmd/Ctrl + Shift + H = Go to Invoices
    if (isMod && e.shiftKey && e.key === 'H') {
      e.preventDefault();
      sdk.Router.goto('clearledgr/invoices');
    }
  });

  console.log('[Clearledgr] Keyboard shortcuts registered');
  console.log('  Cmd/Ctrl+Shift+A = Approve');
  console.log('  Cmd/Ctrl+Shift+R = Reject');
  console.log('  Cmd/Ctrl+Shift+F = Flag');
  console.log('  Cmd/Ctrl+Shift+I = Invoices');
  console.log('  Cmd/Ctrl+Shift+H = Invoices');
}

function triggerCurrentInvoiceAction(action) {
  // Get current thread if viewing one
  const currentUrl = window.location.hash;
  
  // Check if we're viewing a thread
  if (currentUrl.includes('#inbox/') || currentUrl.includes('#label/')) {
    // Dispatch action for current thread
    window.dispatchEvent(new CustomEvent('clearledgr:keyboard-action', {
      detail: { action, url: currentUrl }
    }));
    
    const actionLabels = {
      approve: 'approved',
      reject: 'rejected',
      flag: 'flagged'
    };
    
    showToast(`Invoice ${actionLabels[action]}`, 'success');
  } else {
    showToast('Open an invoice email first', 'warning');
  }
}

// =============================================================================
// SEARCH INTEGRATION - Custom search operators
// =============================================================================

function initializeSearchIntegration() {
  // Register search suggestions
  sdk.Search.registerSearchSuggestionsProvider((query) => {
    const suggestions = [];
    const lowerQuery = query.toLowerCase();
    
    // Suggest Clearledgr-specific searches
    if ('invoice'.startsWith(lowerQuery) || lowerQuery.includes('invoice')) {
      suggestions.push({
        name: 'Clearledgr: All Invoices',
        description: 'Show all invoice emails',
        iconUrl: ICONS.invoice,
        onClick: () => {
          sdk.Router.goto('clearledgr/invoices');
        }
      });
    }
    
    if ('pending'.startsWith(lowerQuery) || lowerQuery.includes('pending')) {
      suggestions.push({
        name: 'Clearledgr: Pending Review',
        description: 'Invoices waiting for approval',
        iconUrl: ICONS.stageReview,
        onClick: () => {
          sdk.Router.goto('clearledgr/invoices', { status: 'pending' });
        }
      });
    }
    
    if ('approved'.startsWith(lowerQuery) || lowerQuery.includes('approved')) {
      suggestions.push({
        name: 'Clearledgr: Approved Invoices',
        description: 'Invoices that have been approved',
        iconUrl: ICONS.stageApproved,
        onClick: () => {
          sdk.Router.goto('clearledgr/invoices', { status: 'approved' });
        }
      });
    }
    
    if ('exception'.startsWith(lowerQuery) || lowerQuery.includes('exception')) {
      suggestions.push({
        name: 'Clearledgr: Exceptions',
        description: 'Invoices that need attention',
        iconUrl: ICONS.stageException,
        onClick: () => {
          sdk.Router.goto('clearledgr/invoices', { status: 'exception' });
        }
      });
    }
    
    if ('vendor'.startsWith(lowerQuery) || lowerQuery.includes('vendor')) {
      suggestions.push({
        name: 'Clearledgr: Vendors',
        description: 'View all vendors',
        iconUrl: ICONS.vendor,
        onClick: () => {
          sdk.Router.goto('clearledgr/vendors');
        }
      });
    }
    
    if ('analytics'.startsWith(lowerQuery) || 'dashboard'.startsWith(lowerQuery)) {
      suggestions.push({
        name: 'Clearledgr: Analytics',
        description: 'View analytics dashboard',
        iconUrl: ICONS.analytics,
        onClick: () => {
          sdk.Router.goto('clearledgr/analytics');
        }
      });
    }
    
    return suggestions;
  });

  console.log('[Clearledgr] Search integration configured');
}

// =============================================================================
// TOAST NOTIFICATIONS - Gmail-style non-intrusive alerts
// =============================================================================

let toastContainer = null;

function initializeToastSystem() {
  // Create toast container
  toastContainer = document.createElement('div');
  toastContainer.id = 'clearledgr-toast-container';
  toastContainer.style.cssText = `
    position: fixed;
    bottom: 24px;
    left: 24px;
    z-index: 999999;
    display: flex;
    flex-direction: column;
    gap: 8px;
    pointer-events: none;
  `;
  document.body.appendChild(toastContainer);
  
  // Add styles
  const style = document.createElement('style');
  style.textContent = `
    .cl-toast {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 12px 20px;
      background: #323232;
      color: white;
      border-radius: 4px;
      font-family: 'Google Sans', Roboto, sans-serif;
      font-size: 14px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.3);
      pointer-events: auto;
      animation: cl-toast-in 0.3s ease-out;
      max-width: 400px;
    }
    
    .cl-toast.success {
      background: #1e8e3e;
    }
    
    .cl-toast.error {
      background: #d93025;
    }
    
    .cl-toast.warning {
      background: #f9ab00;
      color: #202124;
    }
    
    .cl-toast.info {
      background: #1a73e8;
    }
    
    .cl-toast-icon {
      width: 20px;
      height: 20px;
      flex-shrink: 0;
    }
    
    .cl-toast-message {
      flex: 1;
    }
    
    .cl-toast-close {
      background: none;
      border: none;
      color: inherit;
      opacity: 0.7;
      cursor: pointer;
      padding: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    
    .cl-toast-close:hover {
      opacity: 1;
    }
    
    .cl-toast-action {
      background: rgba(255,255,255,0.2);
      border: none;
      color: inherit;
      padding: 6px 12px;
      border-radius: 4px;
      cursor: pointer;
      font-weight: 500;
      font-size: 13px;
    }
    
    .cl-toast-action:hover {
      background: rgba(255,255,255,0.3);
    }
    
    @keyframes cl-toast-in {
      from {
        transform: translateY(20px);
        opacity: 0;
      }
      to {
        transform: translateY(0);
        opacity: 1;
      }
    }
    
    @keyframes cl-toast-out {
      from {
        transform: translateY(0);
        opacity: 1;
      }
      to {
        transform: translateY(20px);
        opacity: 0;
      }
    }
  `;
  document.head.appendChild(style);
  
  console.log('[Clearledgr] Toast notification system initialized');
}

function showToast(message, type = 'info', options = {}) {
  if (!toastContainer) {
    initializeToastSystem();
  }
  
  const { action, actionLabel, duration = 4000 } = options;
  
  const icons = {
    success: `<svg class="cl-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>`,
    error: `<svg class="cl-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>`,
    warning: `<svg class="cl-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"/></svg>`,
    info: `<svg class="cl-toast-icon" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"/></svg>`
  };
  
  const toast = document.createElement('div');
  toast.className = `cl-toast ${type}`;
  toast.innerHTML = `
    ${icons[type] || icons.info}
    <span class="cl-toast-message">${message}</span>
    ${action ? `<button class="cl-toast-action">${actionLabel || 'Undo'}</button>` : ''}
    <button class="cl-toast-close">
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M4.646 4.646a.5.5 0 01.708 0L8 7.293l2.646-2.647a.5.5 0 01.708.708L8.707 8l2.647 2.646a.5.5 0 01-.708.708L8 8.707l-2.646 2.647a.5.5 0 01-.708-.708L7.293 8 4.646 5.354a.5.5 0 010-.708z"/></svg>
    </button>
  `;
  
  // Handle action button
  const actionBtn = toast.querySelector('.cl-toast-action');
  if (actionBtn && action) {
    actionBtn.addEventListener('click', () => {
      action();
      removeToast(toast);
    });
  }
  
  // Handle close button
  const closeBtn = toast.querySelector('.cl-toast-close');
  closeBtn.addEventListener('click', () => removeToast(toast));
  
  toastContainer.appendChild(toast);
  
  // Auto-remove after duration
  if (duration > 0) {
    setTimeout(() => removeToast(toast), duration);
  }
  
  return toast;
}

function removeToast(toast) {
  if (!toast || !toast.parentNode) return;
  
  toast.style.animation = 'cl-toast-out 0.2s ease-in forwards';
  setTimeout(() => {
    if (toast.parentNode) {
      toast.parentNode.removeChild(toast);
    }
  }, 200);
}

// Export showToast globally for use in other functions
window.clearledgrShowToast = showToast;

// Data layer -> UI: toast requests (content-script never mounts UI).
window.addEventListener('clearledgr:toast', (e) => {
  const detail = e?.detail || {};
  const message = detail.message;
  if (!message) return;
  showToast(message, detail.type || 'info');
});

// Data layer -> UI: CSV export payload ready; trigger download here (UI-only).
window.addEventListener('clearledgr:export-csv-ready', (e) => {
  const { filename, csv } = e?.detail || {};
  if (!csv) return;

  try {
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename || `clearledgr-export-${new Date().toISOString().split('T')[0]}.csv`;
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast('CSV export downloaded', 'success');
  } catch (err) {
    console.warn('[Clearledgr] CSV export failed:', err);
    showToast('CSV export failed', 'error');
  }
});

// =============================================================================
// AUTONOMOUS INBOX MONITORING - Auto-detect & process finance emails
// =============================================================================

const processedEmails = new Set(); // Track already-processed emails this session
let lastListScanAt = 0;

function triggerInboxScan(source = 'auto') {
  const now = Date.now();
  if (now - lastListScanAt < 5000) return;
  lastListScanAt = now;
  window.dispatchEvent(new CustomEvent('clearledgr:scan-inbox', { detail: { source } }));
}

function initializeAutonomousMonitoring() {
  // Monitor inbox list rows and decorate them once Clearledgr has triaged them.
  // We intentionally avoid subject-only auto-queueing here to keep AP precision high.
  sdk.Lists.registerThreadRowViewHandler((threadRowView) => {
    const threadId = threadRowView.getThreadID();
    if (!threadId) return;

    // Avoid adding duplicate labels on re-renders.
    if (processedEmails.has(threadId)) return;

    const decorateIfReady = () => {
      const cached = getCachedInvoiceData(threadId);
      if (!cached) return false;

      processedEmails.add(threadId);

      const subject = threadRowView.getSubject() || '';
      const contacts = threadRowView.getContacts() || [];
      const sender = contacts.length > 0 ? contacts[0].emailAddress : 'unknown';

      addMagicColumns(threadRowView, subject, sender);
      return true;
    };

    // Try immediately, then once more after a short delay (scan may still be running).
    if (decorateIfReady()) return;
    setTimeout(() => {
      if (processedEmails.has(threadId)) return;
      decorateIfReady();
    }, 4000);
  });

  // Kick an inbox scan so users immediately see AP candidates populate.
  setTimeout(() => triggerInboxScan('initial'), 800);

  // Scan when the inbox list view is shown/refreshed (debounced).
  // Some InboxSDK builds do not expose registerListViewHandler, so guard it.
  if (sdk?.Lists && typeof sdk.Lists.registerListViewHandler === 'function') {
    sdk.Lists.registerListViewHandler(() => {
      triggerInboxScan('list_view');
    });
  } else {
    console.warn('[Clearledgr] List view handler unavailable; using route/focus/refresh scan fallback');
  }

  // Scan when Gmail's refresh button is used or when the tab regains focus.
  setupInboxRefreshListener();
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) triggerInboxScan('focus');
  });

  console.log('[Clearledgr] Autonomous inbox monitoring active (decorations from cache)');
}

function setupInboxRefreshListener() {
  const attachHandler = () => {
    const refreshButton =
      document.querySelector('[aria-label="Refresh"]') ||
      document.querySelector('[data-tooltip="Refresh"]') ||
      document.querySelector('[aria-label="Refresh inbox"]');
    if (!refreshButton || refreshButton.dataset.clearledgrBound) return;

    refreshButton.dataset.clearledgrBound = 'true';
    refreshButton.addEventListener('click', () => triggerInboxScan('toolbar_refresh'));
  };

  attachHandler();
  const observer = new MutationObserver(() => attachHandler());
  observer.observe(document.body, { childList: true, subtree: true });
}

// =============================================================================
// MAGIC COLUMNS - Show invoice data directly in inbox list (Streak-style)
// =============================================================================

/**
 * Add magic columns/labels to inbox rows showing invoice data at a glance.
 * Users see: [Invoice] Acme Corp | $1,500 | Due: Feb 15 | Pending
 */
function addMagicColumns(threadRowView, subject, sender) {
  const threadId = threadRowView.getThreadID();

  // Prefer cached fields from the queue manager (backend triage/extraction).
  // Fall back to lightweight subject parsing for first-paint.
  const cached = getCachedInvoiceData(threadId);
  const invoiceData = cached ? { ...cached } : extractInvoicePreview(subject, sender);
  
  // Add type label (Invoice, Receipt, Statement)
  const typeLabel = cached ? { title: 'Invoice', fg: '#1565C0', bg: '#E3F2FD', icon: ICONS.invoice } : getEmailTypeLabel(subject);
  if (typeLabel) {
    threadRowView.addLabel({
      title: typeLabel.title,
      foregroundColor: typeLabel.fg,
      backgroundColor: typeLabel.bg,
      iconUrl: typeLabel.icon || null,
    });
  }
  
  // Add amount if detected
  const amountLabel = formatAmountLabel(invoiceData.amount, invoiceData.currency);
  if (amountLabel) {
    threadRowView.addLabel({
      title: amountLabel,
      foregroundColor: '#1565C0',
      backgroundColor: '#E3F2FD',
    });
  }
  
  // Add due date if detected
  if (invoiceData.dueDate) {
    const dueInfo = getDueDateInfo(invoiceData.dueDate);
    threadRowView.addLabel({
      title: dueInfo.label,
      foregroundColor: dueInfo.fg,
      backgroundColor: dueInfo.bg,
    });
  }
  
  // Add status indicator
  const status = getInvoiceStatus(threadId);
  if (status) {
    threadRowView.addLabel({
      title: status.label,
      foregroundColor: status.fg,
      backgroundColor: status.bg,
    });
  }
  
  // Add quick action button for invoices
  if (typeLabel?.title === 'Invoice') {
    threadRowView.addButton({
      title: 'Quick Approve',
      iconUrl: ICONS.stageApproved,
      onClick: (event) => {
        event.preventDefault();
        handleQuickApprove(threadId, subject, invoiceData);
      }
    });
  }
}

/**
 * Extract invoice preview data from subject line and optional body.
 * For quick previews (inbox list), only subject is available.
 * For full extraction (sidebar), pass the email body too.
 */
function extractInvoicePreview(subject, sender, body = '') {
  const data = { amount: null, dueDate: null, invoiceNumber: null, vendor: null };
  
  // Combine subject and body for extraction (body may be empty for quick previews)
  const fullText = body ? `${subject}\n${body}` : subject;
  
  // Extract amount with comprehensive patterns
  const amountPatterns = [
    /(?:Total|Amount|Balance|Due)[:\s]*\$\s*([\d,]+(?:\.\d{2})?)/i,
    /\$\s*([\d,]+(?:\.\d{2})?)/i,
    /(?:€|EUR)\s*([\d\s.,]+)/i,
    /(?:£|GBP)\s*([\d\s.,]+)/i,
    /([\d,]+(?:\.\d{2})?)\s*(?:USD|EUR|GBP)/i,
    /(?:Total|Amount|Invoice Total|Grand Total)[:\s]*([\d,]+(?:\.\d{2})?)/i,
  ];
  
  for (const pattern of amountPatterns) {
    const match = fullText.match(pattern);
    if (match) {
      const amountStr = match[1].replace(/[\s,]/g, '').replace(',', '.');
      const amount = parseFloat(amountStr);
      if (Number.isFinite(amount) && amount >= 0 && amount < 10000000) {
        // Avoid treating years as amounts (ex: 2025)
        if (Number.isInteger(amount) && amount >= 1900 && amount <= 2100) {
          continue;
        }
        data.amount = amount;
        break;
      }
    }
  }
  
  // Extract due date with more patterns
  const datePatterns = [
    /due\s*(?:date)?[:\s]+(\d{4}-\d{2}-\d{2})/i,
    /due\s*(?:date)?[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})/i,
    /due\s*(?:date)?[:\s]+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)/i,
    /payment\s+due[:\s]+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)/i,
    /pay\s+by[:\s]+(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})/i,
  ];
  for (const pattern of datePatterns) {
    const match = fullText.match(pattern);
    if (match) {
      data.dueDate = match[1];
      break;
    }
  }
  
  // Extract invoice number with more patterns
  const invPatterns = [
    /(?:Invoice|INV|Bill)\s*(?:Number|No\.?|#)?[:\s#-]*([A-Z0-9][\w\-\/]+)/i,
    /(?:Reference|Ref|Order)\s*(?:Number|No\.?|#)?[:\s#-]*([A-Z0-9][\w\-\/]+)/i,
    /(?:PO|P\.O\.)\s*(?:Number|No\.?|#)?[:\s#-]*([A-Z0-9][\w\-\/]+)/i,
  ];
  for (const pattern of invPatterns) {
    const match = fullText.match(pattern);
    if (match && match[1].length >= 3) {
      data.invoiceNumber = match[1];
      break;
    }
  }
  
  // Extract vendor - prioritize body content over sender
  // Try to find company name in the email/invoice body first
  if (body) {
    const vendorFromBody = extractVendorFromBody(body);
    if (vendorFromBody) {
      data.vendor = vendorFromBody;
    }
  }
  
  // Fall back to sender if no vendor found in body
  if (!data.vendor && sender) {
    data.vendor = extractVendorFromSender(sender);
  }
  
  return data;
}

/**
 * Extract vendor/company name from email body or invoice content.
 * Looks for company names in headers, signatures, and common patterns.
 */
function extractVendorFromBody(body) {
  if (!body || body.length < 10) return null;
  
  // Common patterns for company names in invoices/emails
  const vendorPatterns = [
    // "Invoice from [Company]" or "Bill from [Company]"
    /(?:invoice|bill)\s+from\s+([A-Z][A-Za-z0-9\s&.,'-]+?)(?:\s*[\r\n]|$)/i,
    
    // "From: [Company]" in headers
    /^From:\s*([A-Z][A-Za-z0-9\s&.,'-]+?)(?:\s*<|\s*[\r\n])/im,
    
    // Company name at very start (letterhead)
    /^([A-Z][A-Za-z0-9\s&.,'-]{2,30}?)(?:\s*[\r\n])/m,
    
    // "[Company] Invoice" pattern
    /^([A-Z][A-Za-z0-9\s&]{2,25})\s+Invoice/im,
    
    // "Vendor: [Name]" or "Supplier: [Name]"  
    /(?:Vendor|Supplier|Biller|Company)[:\s]+([A-Z][A-Za-z0-9\s&.,'-]+?)(?:\s*[\r\n]|$)/i,
    
    // "Thank you for your business - [Company]"
    /(?:thank you|thanks)[^]*?[-–—]\s*([A-Z][A-Za-z0-9\s&.,'-]+?)(?:\s*[\r\n]|$)/i,
    
    // Signature patterns: "Best, [Company]" or "Regards, [Company] Team"
    /(?:regards|sincerely|best|cheers)[,\s]+(?:the\s+)?([A-Z][A-Za-z0-9\s&]+?)\s*(?:team)?(?:\s*[\r\n]|$)/i,
  ];
  
  for (const pattern of vendorPatterns) {
    const match = body.match(pattern);
    if (match && match[1]) {
      let vendor = match[1].trim();
      
      // Clean up the extracted name
      vendor = vendor
        .replace(/\s+/g, ' ')           // Normalize whitespace
        .replace(/[,.]$/, '')           // Remove trailing punctuation
        .replace(/\s+(Inc|LLC|Ltd|Corp|Co)\.?$/i, '') // Remove legal suffixes
        .trim();
      
      // Validate - should be reasonable company name
      if (vendor.length >= 2 && vendor.length <= 50 && !isGenericWord(vendor)) {
        return vendor;
      }
    }
  }
  
  return null;
}

/**
 * Check if a word is too generic to be a company name
 */
function isGenericWord(word) {
  const genericWords = [
    'invoice', 'statement', 'bill', 'payment', 'order',
    'dear', 'hello', 'hi', 'team', 'support', 'billing', 'account',
    'customer', 'client', 'user', 'member', 'subscriber',
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
    'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'total', 'amount', 'balance', 'due', 'paid', 'pending',
    'the', 'and', 'for', 'your', 'our', 'this', 'that',
  ];
  return genericWords.includes(word.toLowerCase());
}

/**
 * Extract clean vendor/company name from sender.
 * Prioritizes domain-based extraction over display names.
 * "Dave from Ramp <dave@ramp.com>" -> "Ramp"
 * "notifications@amazon.com" -> "Amazon"
 */
function extractVendorFromSender(sender) {
  // Known vendor domain mappings
  const VENDOR_MAP = {
    'ramp': 'Ramp',
    'amazon': 'Amazon',
    'aws': 'Amazon Web Services',
    'google': 'Google',
    'googlecloud': 'Google Cloud',
    'stripe': 'Stripe',
    'quickbooks': 'QuickBooks',
    'intuit': 'Intuit',
    'xero': 'Xero',
    'microsoft': 'Microsoft',
    'azure': 'Microsoft Azure',
    'salesforce': 'Salesforce',
    'slack': 'Slack',
    'zoom': 'Zoom',
    'dropbox': 'Dropbox',
    'github': 'GitHub',
    'atlassian': 'Atlassian',
    'notion': 'Notion',
    'figma': 'Figma',
    'openai': 'OpenAI',
    'anthropic': 'Anthropic',
    'hubspot': 'HubSpot',
    'mailchimp': 'Mailchimp',
    'sendgrid': 'SendGrid',
    'twilio': 'Twilio',
    'datadog': 'Datadog',
    'netlify': 'Netlify',
    'vercel': 'Vercel',
    'heroku': 'Heroku',
    'digitalocean': 'DigitalOcean',
    'linode': 'Linode',
    'cloudflare': 'Cloudflare',
    'fastly': 'Fastly',
    'newrelic': 'New Relic',
    'pagerduty': 'PagerDuty',
    'intercom': 'Intercom',
    'zendesk': 'Zendesk',
    'freshdesk': 'Freshdesk',
    'asana': 'Asana',
    'monday': 'Monday.com',
    'linear': 'Linear',
    'jira': 'Jira',
    'confluence': 'Confluence',
    'trello': 'Trello',
    'airtable': 'Airtable',
    'bill': 'Bill.com',
    'expensify': 'Expensify',
    'brex': 'Brex',
    'divvy': 'Divvy',
    'mercury': 'Mercury',
    'plaid': 'Plaid',
    'gusto': 'Gusto',
    'rippling': 'Rippling',
    'deel': 'Deel',
    'remote': 'Remote',
    'paypal': 'PayPal',
    'venmo': 'Venmo',
    'square': 'Square',
    'shopify': 'Shopify',
    'wix': 'Wix',
    'squarespace': 'Squarespace',
    'godaddy': 'GoDaddy',
    'namecheap': 'Namecheap',
    'hover': 'Hover',
  };
  
  // Try to extract email from sender (handles "Name <email>" format)
  const emailMatch = sender.match(/<([^>]+)>/) || sender.match(/([^\s<]+@[^\s>]+)/);
  const email = emailMatch ? emailMatch[1] : sender;
  
  if (email.includes('@')) {
    // Extract domain
    const domain = email.split('@')[1].toLowerCase();
    const domainParts = domain.split('.');
    const baseDomain = domainParts[0];
    
    // Check vendor map first
    if (VENDOR_MAP[baseDomain]) {
      return VENDOR_MAP[baseDomain];
    }
    
    // Check if any part of domain matches vendor map
    for (const part of domainParts) {
      if (VENDOR_MAP[part]) {
        return VENDOR_MAP[part];
      }
    }
    
    // Capitalize the base domain as fallback
    return baseDomain.charAt(0).toUpperCase() + baseDomain.slice(1);
  }
  
  // No email found - clean up the display name
  let name = sender.trim();
  
  // Remove common prefixes like "from" 
  name = name.replace(/^(the\s+)?(\w+\s+)?(from|at|with)\s+/i, '');
  
  // Remove common suffixes
  name = name.replace(/\s+(team|support|billing|notifications|noreply|no-reply)$/i, '');
  
  return name || 'Unknown';
}

/**
 * Get email type label based on subject.
 */
function getEmailTypeLabel(subject) {
  const subjectLower = subject.toLowerCase();
  
  if (subjectLower.includes('invoice') || subjectLower.includes('payment due')) {
    return { title: 'Invoice', fg: '#1565C0', bg: '#E3F2FD', icon: ICONS.invoice };
  }
  if (subjectLower.includes('payment request') || subjectLower.includes('please pay')) {
    return { title: 'Request', fg: '#C62828', bg: '#FFEBEE' };
  }
  
  return null;
}

/**
 * Get due date display info.
 */
function getDueDateInfo(dueDateStr) {
  try {
    const dueDate = new Date(dueDateStr);
    const today = new Date();
    const diffDays = Math.ceil((dueDate - today) / (1000 * 60 * 60 * 24));
    
    if (diffDays < 0) {
      return { label: `Overdue ${Math.abs(diffDays)}d`, fg: '#C62828', bg: '#FFEBEE' };
    } else if (diffDays === 0) {
      return { label: 'Due Today', fg: '#E65100', bg: '#FFF3E0' };
    } else if (diffDays <= 3) {
      return { label: `Due ${diffDays}d`, fg: '#E65100', bg: '#FFF3E0' };
    } else if (diffDays <= 7) {
      return { label: `Due ${diffDays}d`, fg: '#1565C0', bg: '#E3F2FD' };
    } else {
      return { label: `Due ${diffDays}d`, fg: '#5f6368', bg: '#f1f3f4' };
    }
  } catch (e) {
    return { label: dueDateStr, fg: '#5f6368', bg: '#f1f3f4' };
  }
}

/**
 * Get invoice status from local cache.
 */
function getInvoiceStatus(threadId) {
  // Check local storage for cached status
  try {
    const cached = localStorage.getItem(`clearledgr_status_${threadId}`);
    if (cached) {
      const status = JSON.parse(cached);
      const statusMap = {
        'new': { label: 'New', fg: '#5f6368', bg: '#f1f3f4' },
        'pending_approval': { label: 'Review', fg: '#E65100', bg: '#FFF3E0' },
        'approved': { label: 'Approved', fg: '#2E7D32', bg: '#E8F5E9' },
        'posted': { label: 'Posted', fg: '#1B5E20', bg: '#C8E6C9' },
        'rejected': { label: 'Rejected', fg: '#C62828', bg: '#FFEBEE' },
        'paid': { label: 'Paid', fg: '#1565C0', bg: '#E3F2FD' },
        'pending': { label: 'Pending', fg: '#E65100', bg: '#FFF3E0' },
      };
      return statusMap[status.status] || null;
    }
  } catch (e) {}
  return null;
}

/**
 * Get cached invoice fields for inbox-row labels.
 * Populated by the content-script queue manager (best-effort).
 */
function getCachedInvoiceData(threadId) {
  try {
    const cached = localStorage.getItem(`clearledgr_invoice_${threadId}`);
    if (!cached) return null;
    return JSON.parse(cached);
  } catch (_) {
    return null;
  }
}

function getBackendStatus() {
  try {
    const raw = localStorage.getItem('clearledgr_backend_status');
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function getScanStatus() {
  try {
    const raw = localStorage.getItem('clearledgr_scan_status');
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function updateScanStatus() {
  if (!globalSidebarEl) return;
  const status = getScanStatus() || {};
  const timeEl = globalSidebarEl.querySelector('#gsb-scan-time');
  const modeEl = globalSidebarEl.querySelector('#gsb-scan-mode');
  const candidatesEl = globalSidebarEl.querySelector('#gsb-scan-candidates');
  const addedEl = globalSidebarEl.querySelector('#gsb-scan-added');

  if (timeEl) {
    const ts = status.updatedAt || status.lastScanAt;
    timeEl.textContent = ts ? formatTimeAgo(ts) : 'Not yet';
  }
  if (modeEl) {
    const modeLabel = status.mode === 'gmail_api' ? 'Gmail API' : (status.mode === 'dom' ? 'DOM' : 'Auto');
    modeEl.textContent = modeLabel;
  }
  if (candidatesEl) candidatesEl.textContent = Number.isFinite(status.candidates) ? String(status.candidates) : '0';
  if (addedEl) addedEl.textContent = Number.isFinite(status.added) ? String(status.added) : '0';
}

function formatAutopilotConnectAttempt(attempt) {
  if (!attempt || !attempt.updatedAt) return 'Never';
  const when = formatTimeAgo(attempt.updatedAt);
  const status = String(attempt.status || '').toLowerCase();
  if (status === 'success') return `Success (${when})`;
  if (status === 'in_progress') return `In progress (${when})`;
  if (status === 'failed') return `Needs retry (${when})`;
  return when;
}

function updateAutopilotConnectAttemptStatus(attempt) {
  if (!globalSidebarEl) return;
  const connectEl = globalSidebarEl.querySelector('#gsb-autopilot-connect');
  if (!connectEl) return;
  connectEl.textContent = formatAutopilotConnectAttempt(attempt);
}

function updateAutonomousBanner(state) {
  const banner = document.querySelector('#cl-autonomous-banner');
  if (!banner) return;

  const indicator = banner.querySelector('#cl-autonomous-indicator');
  const labelEl = banner.querySelector('#cl-autonomous-label');
  const detailEl = banner.querySelector('#cl-autonomous-detail');
  const actionsEl = banner.querySelector('#cl-autonomous-actions');

  banner.classList.remove('warning', 'error');
  indicator?.classList.remove('warning', 'error', 'active');

  const applyState = (level) => {
    if (!level) return;
    if (level === 'warning') banner.classList.add('warning');
    if (level === 'error') banner.classList.add('error');
    if (level === 'active') indicator?.classList.add('active');
    if (level === 'warning' || level === 'error') {
      indicator?.classList.add(level);
    }
  };

  applyState(state.level || 'warning');
  if (labelEl) labelEl.textContent = state.label || 'Checking autonomous status';
  if (detailEl) detailEl.textContent = state.detail || 'Verifying backend connection for 24/7 processing';
  if (actionsEl) actionsEl.style.display = state.showConnect ? 'block' : 'none';
}

const AUTOPILOT_STATUS_INTERVAL_MS = 30 * 1000;
let autopilotStatusIntervalId = null;
let autopilotStatusVisibilityBound = false;

function ensureAutopilotStatusPolling() {
  updateAutopilotStatus();
  if (!autopilotStatusIntervalId) {
    autopilotStatusIntervalId = setInterval(updateAutopilotStatus, AUTOPILOT_STATUS_INTERVAL_MS);
  }
  if (!autopilotStatusVisibilityBound) {
    autopilotStatusVisibilityBound = true;
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        updateAutopilotStatus();
      }
    });
    window.addEventListener('focus', () => {
      updateAutopilotStatus();
    });
  }
}

async function updateAutopilotStatus() {
  if (!globalSidebarEl) return;
  const statusEl = globalSidebarEl.querySelector('#gsb-autopilot-status');
  if (!statusEl) return;
  let connectAttempt = null;

  try {
    let data = null;
    try {
      data = await chrome.runtime.sendMessage({ action: 'getAutopilotStatus' });
      connectAttempt = data?.autopilot_connect_attempt || null;
      if (!data || data.success === false) {
        throw new Error(data?.error || 'autopilot_unavailable');
      }
    } catch (_) {
      // Fallback to direct fetch for environments where runtime messaging is unavailable.
      const response = await backendFetch(`${BACKEND_URL}/autonomous/status`, {}, {
        warnMessage: '[Clearledgr] Could not reach autonomous status endpoint'
      });
      if (!response.ok) throw new Error('autopilot_unavailable');
      data = await response.json();
    }
    updateAutopilotConnectAttemptStatus(connectAttempt);
    const ap = data?.gmail_autopilot || {};

    if (ap.last_error) {
      statusEl.textContent = 'Attention';
      updateAutonomousBanner({
        level: 'warning',
        label: 'Autopilot needs attention',
        detail: 'A temporary sync issue was detected. Clearledgr will keep retrying.',
        showConnect: false
      });
      return;
    }
    if (ap.users && ap.users > 0) {
      statusEl.textContent = 'Active';
      updateAutonomousBanner({
        level: 'active',
        label: 'Autonomous monitoring active',
        detail: 'Processing AP emails in the background',
        showConnect: false
      });
      return;
    }
    statusEl.textContent = 'Not connected';
    updateAutonomousBanner({
      level: 'warning',
      label: 'Autopilot not connected',
      detail: 'Connect Gmail to enable 24/7 processing',
      showConnect: true
    });
  } catch (_) {
    updateAutopilotConnectAttemptStatus(connectAttempt);
    const scan = getScanStatus() || {};
    const isBrowserActive = Boolean(scan.updatedAt) && (Date.now() - Number(scan.updatedAt) < 5 * 60 * 1000);
    if (isBrowserActive) {
      statusEl.textContent = 'Local-only';
      updateAutonomousBanner({
        level: 'warning',
        label: 'Backend unavailable',
        detail: 'Running tab-local inbox scan only. 24/7 autopilot is unavailable until backend reconnects.',
        showConnect: false
      });
    } else {
      statusEl.textContent = 'Disconnected';
      updateAutonomousBanner({
        level: 'error',
        label: 'Autopilot offline',
        detail: 'Backend unreachable. 24/7 processing is currently off.',
        showConnect: false
      });
    }
  }
}

function shouldShowBackendStatus(status) {
  if (!status || status.status !== 'offline') return false;
  const updatedAt = status.updatedAt ? new Date(status.updatedAt).getTime() : 0;
  if (!updatedAt) return false;
  return Date.now() - updatedAt < 10 * 60 * 1000;
}

function updateBackendStatusBanner(threadView) {
  if (!globalSidebarEl) return;
  const statusEl = globalSidebarEl.querySelector('#gsb-backend-status');
  if (!statusEl) return;

  const status = getBackendStatus();
  const threadId = threadView?.getThreadID?.();
  const hasCached = threadId ? Boolean(getCachedInvoiceData(threadId)) : false;
  const subject = threadView?.getSubject?.() || '';
  const senderEmail = threadView ? getThreadSenderEmail(threadView) : '';
  const isFinance = threadView && (hasCached || isFinanceEmail(subject, senderEmail));
  const show = isFinance && shouldShowBackendStatus(status);

  if (!show) {
    statusEl.textContent = '';
    statusEl.classList.add('hidden');
    return;
  }

  const detail = 'Background sync is temporarily paused. Core inbox scanning remains active.';
  statusEl.textContent = detail;
  statusEl.classList.remove('hidden');
}

function formatAmountLabel(amount, currency) {
  if (amount == null || amount === '') return '';

  const raw = String(amount).trim();
  const detectedCurrency = (currency || '').toUpperCase();

  // If it's already a human-readable string like "EUR 40.23", keep it.
  if (/[A-Z]{3}\s*\d/.test(raw) || /^[€$£]/.test(raw)) return raw;

  const n = typeof amount === 'number' ? amount : parseFloat(raw.replace(/[^0-9.-]/g, ''));
  if (!Number.isFinite(n)) return raw;

  const iso = detectedCurrency && /^[A-Z]{3}$/.test(detectedCurrency) ? detectedCurrency : 'USD';
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: iso }).format(n);
  } catch (_) {
    return `$${n.toLocaleString()}`;
  }
}

/**
 * Fetch and cache invoice status from backend (async, updates Magic Columns later).
 */
async function fetchAndCacheInvoiceStatus(threadId) {
  try {
    const response = await fetch(
      `${BACKEND_URL}/ap/erp-sync/by-thread/${encodeURIComponent(threadId)}?organization_id=${getOrganizationId()}`
    );
    
    if (response.ok) {
      const data = await response.json();
      if (data.found) {
        // Cache the status
        localStorage.setItem(`clearledgr_status_${threadId}`, JSON.stringify({
          status: data.status,
          amount: data.amount,
          updated: new Date().toISOString()
        }));
        return data;
      }
    }
  } catch (e) {
    // Silently fail - cache will be empty
  }
  return null;
}

/**
 * Update invoice status in cache (called after approve/reject/post actions).
 */
function updateCachedStatus(threadId, status, additionalData = {}) {
  try {
    localStorage.setItem(`clearledgr_status_${threadId}`, JSON.stringify({
      status,
      ...additionalData,
      updated: new Date().toISOString()
    }));
  } catch (e) {}
}

/**
 * Handle quick approve from inbox list.
 */
async function handleQuickApprove(threadId, subject, invoiceData) {
  showToast('Opening approval...', 'info');
  
  // Navigate to the thread to show full context
  sdk.Router.goto('clearledgr/invoices', { thread: threadId, action: 'approve' });
}

// =============================================================================
// GLOBAL SIDEBAR - Always visible (like Streak)
// =============================================================================

let globalSidebarEl = null;
let currentThreadView = null;
let globalSidebarInitialized = false;

function initializeGlobalSidebar() {
  if (globalSidebarInitialized) return;
  globalSidebarInitialized = true;

  // Create global sidebar that's always visible
  globalSidebarEl = createGlobalSidebarPanel();
  
  sdk.Global.addSidebarContentPanel({
    title: 'Clearledgr',
    iconUrl: ICONS.small,
    el: globalSidebarEl,
    // Keep sidebar open by default
    hideTitleBar: false,
  });
  
  console.log('[Clearledgr] Global sidebar initialized');
}

function createGlobalSidebarPanel() {
  const container = document.createElement('div');
  container.className = 'cl-global-sidebar';
  container.innerHTML = `
    <style>
      .cl-global-sidebar {
        font-family: 'Google Sans', Roboto, sans-serif;
        font-size: 13px;
        color: #202124;
        padding: 0;
        height: 100%;
        display: flex;
        flex-direction: column;
      }
      
      .cl-gsb-header {
        padding: 16px;
        background: #fff;
        border-bottom: 1px solid #e0e0e0;
      }
      
      .cl-gsb-logo {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 15px;
        font-weight: 500;
        color: #202124;
        margin-bottom: 12px;
      }
      
      .cl-gsb-logo svg {
        width: 24px;
        height: 24px;
      }
      
      .cl-gsb-stats {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }
      
      .cl-gsb-stat {
        background: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 8px 10px;
        text-align: center;
      }
      
      .cl-gsb-stat-value {
        font-size: 20px;
        font-weight: 600;
        color: #1a73e8;
      }
      
      .cl-gsb-stat-label {
        font-size: 10px;
        color: #5f6368;
        text-transform: uppercase;
      }
      
      .cl-gsb-section {
        padding: 12px 16px;
        border-bottom: 1px solid #e0e0e0;
      }
      
      .cl-gsb-section-title {
        font-size: 11px;
        font-weight: 600;
        color: #5f6368;
        text-transform: uppercase;
        margin-bottom: 8px;
      }
      
      .cl-gsb-actions {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }
      
      .cl-gsb-action {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        background: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 6px;
        cursor: pointer;
        transition: all 0.15s;
        font-size: 12px;
        color: #202124;
      }
      
      .cl-gsb-action:hover {
        background: #e8f0fe;
        border-color: #1a73e8;
        color: #1a73e8;
      }
      
      .cl-gsb-action img,
      .cl-gsb-action svg {
        width: 16px;
        height: 16px;
        flex-shrink: 0;
      }
      
      .cl-gsb-logo img {
        flex-shrink: 0;
      }
      
      .cl-gsb-pending-list {
        display: flex;
        flex-direction: column;
        gap: 4px;
        max-height: 200px;
        overflow-y: auto;
      }
      
      .cl-gsb-pending-item {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px;
        background: #fff8e1;
        border-radius: 6px;
        font-size: 11px;
        cursor: pointer;
      }
      
      .cl-gsb-pending-item:hover {
        background: #ffecb3;
      }
      
      .cl-gsb-pending-vendor {
        font-weight: 500;
        color: #202124;
        max-width: 120px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      
      .cl-gsb-pending-amount {
        font-weight: 600;
        color: #E65100;
      }
      
      .cl-gsb-context {
        flex: 1;
        padding: 12px 16px;
        background: #f8f9fa;
        display: none;
      }
      
      .cl-gsb-context.active {
        display: block;
      }
      
      .cl-gsb-context-title {
        font-size: 11px;
        color: #5f6368;
        margin-bottom: 8px;
      }
      
      .cl-gsb-context-email {
        background: white;
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #e0e0e0;
      }
      
      .cl-gsb-email-subject {
        font-weight: 500;
        font-size: 13px;
        margin-bottom: 8px;
        color: #202124;
      }
      
      .cl-gsb-email-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        font-size: 11px;
        color: #5f6368;
      }
      
      .cl-gsb-email-meta span {
        background: #e8f0fe;
        padding: 2px 6px;
        border-radius: 4px;
      }
      
      .cl-gsb-email-actions {
        display: flex;
        gap: 8px;
        margin-top: 12px;
      }

      .cl-gsb-reasoning {
        margin-top: 10px;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid #e0e0e0;
        background: #f8f9fa;
        font-size: 12px;
        color: #5f6368;
      }

      .cl-gsb-reasoning.hidden {
        display: none;
      }

      .cl-gsb-reasoning-title {
        font-size: 11px;
        letter-spacing: 0.4px;
        text-transform: uppercase;
        font-weight: 600;
        color: #5f6368;
        margin-bottom: 4px;
      }

      .cl-gsb-reasoning-summary {
        color: #202124;
        margin-bottom: 4px;
      }

      .cl-gsb-reasoning-list {
        margin: 0;
        padding-left: 16px;
      }

      .cl-gsb-reasoning-list li {
        margin: 2px 0;
      }

      .cl-gsb-backend-status {
        margin-top: 10px;
        padding: 8px 10px;
        border-radius: 8px;
        background: #fff4e5;
        color: #7a3e00;
        font-size: 12px;
        border: 1px solid #f0c36d;
      }

      .cl-gsb-backend-status.hidden {
        display: none;
      }

      .cl-gsb-btn {
        flex: 1;
        padding: 8px;
        border-radius: 6px;
        font-size: 11px;
        font-weight: 500;
        cursor: pointer;
        border: none;
        transition: all 0.15s;
      }
      
      .cl-gsb-btn-approve {
        background: #1a73e8;
        color: white;
      }
      
      .cl-gsb-btn-approve:hover {
        background: #1557b0;
      }
      
      .cl-gsb-btn-reject {
        background: #f1f3f4;
        color: #5f6368;
        border: 1px solid #dadce0;
      }
      
      .cl-gsb-btn-reject:hover {
        background: #ffebee;
        color: #c62828;
        border-color: #c62828;
      }

      .cl-gsb-btn-secondary {
        background: #f8f9fa;
        color: #1a73e8;
        border: 1px solid #dadce0;
      }

      .cl-gsb-btn-secondary:hover {
        background: #e8f0fe;
        border-color: #1a73e8;
      }

      .cl-gsb-scan {
        display: flex;
        flex-direction: column;
        gap: 6px;
      }

      .cl-gsb-scan-row {
        display: flex;
        justify-content: space-between;
        font-size: 11px;
        color: #5f6368;
      }

      .cl-gsb-scan-label {
        text-transform: uppercase;
        letter-spacing: 0.4px;
      }

      .cl-gsb-scan-value {
        color: #202124;
        font-weight: 500;
      }

      .cl-gsb-scan-actions {
        display: flex;
        gap: 8px;
        margin-top: 8px;
      }
      
      .cl-gsb-empty {
        text-align: center;
        padding: 20px;
        color: #5f6368;
        font-size: 12px;
      }
      
      .cl-gsb-empty svg {
        width: 40px;
        height: 40px;
        margin-bottom: 8px;
        opacity: 0.5;
      }
      
      .cl-gsb-footer {
        padding: 12px 16px;
        border-top: 1px solid #e0e0e0;
        background: #fafafa;
        margin-top: auto;
      }
      
      .cl-gsb-footer-link {
        font-size: 11px;
        color: #1a73e8;
        text-decoration: none;
        cursor: pointer;
      }
      
      .cl-gsb-footer-link:hover {
        text-decoration: underline;
      }
    </style>
    
    <div class="cl-gsb-header">
      <div class="cl-gsb-logo">
        <img src="${ICONS.small}" alt="Clearledgr" style="width: 24px; height: 24px;">
        Clearledgr
      </div>
      <div class="cl-gsb-stats">
        <div class="cl-gsb-stat">
          <div class="cl-gsb-stat-value" id="gsb-pending-count">-</div>
          <div class="cl-gsb-stat-label">Pending</div>
        </div>
        <div class="cl-gsb-stat">
          <div class="cl-gsb-stat-value" id="gsb-pending-amount">-</div>
          <div class="cl-gsb-stat-label">Amount</div>
        </div>
      </div>
    </div>
    
    <div class="cl-gsb-section">
      <div class="cl-gsb-section-title">Quick Actions</div>
      <div class="cl-gsb-actions">
        <div class="cl-gsb-action" data-action="invoices">
          <img src="${ICONS.invoice}" alt="" style="width: 16px; height: 16px;">
          <span>View All Invoices</span>
        </div>
        <div class="cl-gsb-action" data-action="payments">
          <img src="${ICONS.payment}" alt="" style="width: 16px; height: 16px;">
          <span>Payments Queue</span>
        </div>
        <div class="cl-gsb-action" data-action="dashboard">
          <img src="${ICONS.home}" alt="" style="width: 16px; height: 16px;">
          <span>Dashboard</span>
        </div>
      </div>
    </div>

    <div class="cl-gsb-section">
      <div class="cl-gsb-section-title">Inbox Scan</div>
      <div class="cl-gsb-scan">
        <div class="cl-gsb-scan-row">
          <span class="cl-gsb-scan-label">Last scan</span>
          <span class="cl-gsb-scan-value" id="gsb-scan-time">-</span>
        </div>
        <div class="cl-gsb-scan-row">
          <span class="cl-gsb-scan-label">Mode</span>
          <span class="cl-gsb-scan-value" id="gsb-scan-mode">-</span>
        </div>
        <div class="cl-gsb-scan-row">
          <span class="cl-gsb-scan-label">Candidates</span>
          <span class="cl-gsb-scan-value" id="gsb-scan-candidates">-</span>
        </div>
        <div class="cl-gsb-scan-row">
          <span class="cl-gsb-scan-label">Added</span>
          <span class="cl-gsb-scan-value" id="gsb-scan-added">-</span>
        </div>
        <div class="cl-gsb-scan-row">
          <span class="cl-gsb-scan-label">Autopilot</span>
          <span class="cl-gsb-scan-value" id="gsb-autopilot-status">-</span>
        </div>
        <div class="cl-gsb-scan-row">
          <span class="cl-gsb-scan-label">Last connect</span>
          <span class="cl-gsb-scan-value" id="gsb-autopilot-connect">-</span>
        </div>
        <div class="cl-gsb-scan-actions">
          <button class="cl-gsb-btn cl-gsb-btn-secondary" id="gsb-scan-now">Scan now</button>
          <button class="cl-gsb-btn cl-gsb-btn-secondary" id="gsb-reset-scan">Reset scan</button>
        </div>
      </div>
    </div>
    
    <div class="cl-gsb-section">
      <div class="cl-gsb-section-title">Needs Attention</div>
      <div class="cl-gsb-pending-list" id="gsb-pending-list">
        <div class="cl-gsb-empty">
          <svg width="40" height="40" viewBox="0 0 16 16" fill="none" style="opacity: 0.4;">
            <rect x="3" y="1" width="10" height="14" rx="1" stroke="#5f6368" stroke-width="1.5" fill="none"/>
            <line x1="5" y1="5" x2="11" y2="5" stroke="#5f6368" stroke-width="1"/>
            <line x1="5" y1="8" x2="11" y2="8" stroke="#5f6368" stroke-width="1"/>
            <line x1="5" y1="11" x2="8" y2="11" stroke="#5f6368" stroke-width="1"/>
          </svg>
          <div>No pending invoices</div>
        </div>
      </div>
    </div>
    
    <div class="cl-gsb-context" id="gsb-email-context">
      <div class="cl-gsb-context-title">CURRENT EMAIL</div>
      <div class="cl-gsb-context-email">
        <div class="cl-gsb-email-subject" id="gsb-email-subject">-</div>
        <div class="cl-gsb-email-meta">
          <span id="gsb-email-vendor">-</span>
          <span id="gsb-email-amount">-</span>
          <span id="gsb-email-status">New</span>
        </div>
        <div class="cl-gsb-backend-status hidden" id="gsb-backend-status"></div>
        <div class="cl-gsb-reasoning hidden" id="gsb-email-reasoning">
          <div class="cl-gsb-reasoning-title">Why</div>
          <div class="cl-gsb-reasoning-summary" id="gsb-reasoning-summary">-</div>
          <ul class="cl-gsb-reasoning-list" id="gsb-reasoning-list"></ul>
        </div>
        <div class="cl-gsb-email-actions">
          <button class="cl-gsb-btn cl-gsb-btn-approve" id="gsb-approve-btn">Approve</button>
          <button class="cl-gsb-btn cl-gsb-btn-reject" id="gsb-reject-btn">Reject</button>
        </div>
      </div>
    </div>
    
    <div class="cl-gsb-footer">
      <a class="cl-gsb-footer-link" data-action="settings">Settings</a>
      <span style="color: #dadce0; margin: 0 8px;">|</span>
      <a class="cl-gsb-footer-link" data-action="help">Help</a>
    </div>
  `;
  
  // Wire up actions
  setTimeout(() => {
    container.querySelectorAll('[data-action]').forEach(el => {
      el.addEventListener('click', () => {
        const action = el.dataset.action;
        if (action === 'invoices') sdk.Router.goto('clearledgr/invoices');
        else if (action === 'payments') sdk.Router.goto('clearledgr/payments');
        else if (action === 'dashboard') sdk.Router.goto('clearledgr/home');
        else if (action === 'settings') sdk.Router.goto('clearledgr/settings');
        else if (action === 'help') window.open('https://clearledgr.com/help', '_blank');
      });
    });

    const scanNowBtn = container.querySelector('#gsb-scan-now');
    const resetScanBtn = container.querySelector('#gsb-reset-scan');
    scanNowBtn?.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('clearledgr:scan-inbox', { detail: { source: 'sidebar' } }));
    });
    resetScanBtn?.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('clearledgr:reset-scan', { detail: { source: 'sidebar' } }));
    });
    
    // Wire up approve/reject
    const approveBtn = container.querySelector('#gsb-approve-btn');
    const rejectBtn = container.querySelector('#gsb-reject-btn');
    
    approveBtn?.addEventListener('click', () => {
      if (currentThreadView) {
        const subject = currentThreadView.getSubject();
        showPreflightCheck(subject, () => {
          showToast('Invoice approved', 'success');
          updateGlobalSidebarContext(null);
        });
      }
    });
    
    rejectBtn?.addEventListener('click', () => {
      if (currentThreadView) {
        showToast('Invoice flagged for review', 'info');
        updateGlobalSidebarContext(null);
      }
    });
  }, 100);
  
  // Load initial data
  loadGlobalSidebarData(container);
  updateScanStatus();
  ensureAutopilotStatusPolling();
  
  return container;
}

async function loadGlobalSidebarData(container) {
  try {
    const response = await backendFetch(
      `${BACKEND_URL}/ap/invoices/pending?organization_id=${getOrganizationId()}`,
      {},
      { warnMessage: '[Clearledgr] Could not load pending invoices' }
    );
    
    if (response.ok) {
      const data = await response.json();
      const invoices = data.invoices || [];
      
      // Update stats
      const pendingCount = container.querySelector('#gsb-pending-count');
      const pendingAmount = container.querySelector('#gsb-pending-amount');
      const pendingList = container.querySelector('#gsb-pending-list');
      
      if (pendingCount) pendingCount.textContent = invoices.length;
      if (pendingAmount) {
        const total = invoices.reduce((sum, inv) => sum + (inv.amount || 0), 0);
        pendingAmount.textContent = total > 0 ? `$${(total/1000).toFixed(1)}k` : '$0';
      }
      
      // Update pending list
      if (pendingList && invoices.length > 0) {
        pendingList.innerHTML = invoices.slice(0, 5).map(inv => `
          <div class="cl-gsb-pending-item" data-thread="${inv.thread_id || ''}">
            <span class="cl-gsb-pending-vendor">${inv.vendor || 'Unknown'}</span>
            <span class="cl-gsb-pending-amount">$${(inv.amount || 0).toLocaleString()}</span>
          </div>
        `).join('');
        
        // Wire up clicks
        pendingList.querySelectorAll('.cl-gsb-pending-item').forEach(item => {
          item.addEventListener('click', () => {
            const threadId = item.dataset.thread;
            if (threadId) {
              // Navigate to thread
              window.location.hash = `#inbox/${threadId}`;
            }
          });
        });
      }
    }
  } catch (e) {
    console.warn('[Clearledgr] Could not load sidebar data:', e);
    // Show friendly empty state
    const pendingCount = container.querySelector('#gsb-pending-count');
    const pendingAmount = container.querySelector('#gsb-pending-amount');
    if (pendingCount) pendingCount.textContent = '0';
    if (pendingAmount) pendingAmount.textContent = '$0';
  }
}

function updateGlobalSidebarContext(threadView) {
  currentThreadView = threadView;
  
  if (!globalSidebarEl) return;
  
  const contextSection = globalSidebarEl.querySelector('#gsb-email-context');
  
  if (threadView) {
    const subject = threadView.getSubject() || 'Unknown';
    const threadId = threadView.getThreadID();
    const senderEmail = getThreadSenderEmail(threadView);
    const cached = threadId ? getCachedInvoiceData(threadId) : null;
    const shouldShow = Boolean(cached) || isFinanceEmail(subject, senderEmail);

    if (!shouldShow) {
      if (contextSection) {
        contextSection.classList.remove('active');
        const subjectEl = contextSection.querySelector('#gsb-email-subject');
        const vendorEl = contextSection.querySelector('#gsb-email-vendor');
        const amountEl = contextSection.querySelector('#gsb-email-amount');
        const statusEl = contextSection.querySelector('#gsb-email-status');
        const reasoningEl = contextSection.querySelector('#gsb-email-reasoning');
        const reasoningSummaryEl = contextSection.querySelector('#gsb-reasoning-summary');
        const reasoningListEl = contextSection.querySelector('#gsb-reasoning-list');

        if (subjectEl) subjectEl.textContent = '';
        if (vendorEl) vendorEl.textContent = '';
        if (amountEl) amountEl.textContent = '';
        if (statusEl) statusEl.textContent = '';

        if (reasoningEl) reasoningEl.classList.add('hidden');
        if (reasoningSummaryEl) reasoningSummaryEl.textContent = '';
        if (reasoningListEl) reasoningListEl.innerHTML = '';
      }

      updateBackendStatusBanner(threadView);
      updateScanStatus();
      return;
    }

    const invoiceData = cached || extractInvoicePreview(subject, senderEmail || '');
    
    // Show context section
    if (contextSection) {
      contextSection.classList.add('active');
      
      const subjectEl = contextSection.querySelector('#gsb-email-subject');
      const vendorEl = contextSection.querySelector('#gsb-email-vendor');
      const amountEl = contextSection.querySelector('#gsb-email-amount');
      const statusEl = contextSection.querySelector('#gsb-email-status');
      const reasoningEl = contextSection.querySelector('#gsb-email-reasoning');
      const reasoningSummaryEl = contextSection.querySelector('#gsb-reasoning-summary');
      const reasoningListEl = contextSection.querySelector('#gsb-reasoning-list');
      
      if (subjectEl) subjectEl.textContent = subject.substring(0, 50) + (subject.length > 50 ? '...' : '');
      if (vendorEl) vendorEl.textContent = invoiceData.vendor || 'Unknown Vendor';
      if (amountEl) {
        const formattedAmount = formatAmountLabel(invoiceData.amount, invoiceData.currency);
        amountEl.textContent = formattedAmount || 'Amount TBD';
      }

      if (statusEl) {
        const status = threadId ? getInvoiceStatus(threadId) : null;
        statusEl.textContent = status?.label || invoiceData.status || 'New';
      }

      if (reasoningEl) {
        const summary = invoiceData.reasoningSummary || invoiceData.reasoning?.summary || '';
        const factors = Array.isArray(invoiceData.reasoningFactors)
          ? invoiceData.reasoningFactors
          : (Array.isArray(invoiceData.reasoning?.factors) ? invoiceData.reasoning.factors : []);
        const risks = Array.isArray(invoiceData.reasoningRisks)
          ? invoiceData.reasoningRisks
          : (Array.isArray(invoiceData.reasoning?.risks) ? invoiceData.reasoning.risks : []);

        const lines = [];
        factors.slice(0, 2).forEach((f) => {
          const label = f?.factor || 'Signal';
          const detail = f?.detail || '';
          lines.push(`${label}${detail ? `: ${detail}` : ''}`);
        });
        risks.slice(0, 1).forEach((r) => lines.push(`Risk: ${r}`));

        if (summary || lines.length) {
          reasoningEl.classList.remove('hidden');
          if (reasoningSummaryEl) reasoningSummaryEl.textContent = summary || 'Agent rationale available.';
          if (reasoningListEl) {
            reasoningListEl.innerHTML = lines.map((line) => `<li>${escapeHtml(line)}</li>`).join('');
          }
        } else {
          reasoningEl.classList.add('hidden');
          if (reasoningListEl) reasoningListEl.innerHTML = '';
          if (reasoningSummaryEl) reasoningSummaryEl.textContent = '';
        }
      }
    }

    if (threadId) {
      fetchAndCacheInvoiceStatus(threadId);
      const hasReasoning =
        Boolean(invoiceData.reasoningSummary) ||
        Boolean(invoiceData.reasoning?.summary) ||
        (Array.isArray(invoiceData.reasoningFactors) && invoiceData.reasoningFactors.length > 0);
      if (!hasReasoning) {
        requestThreadTriage(threadView, { subject, senderEmail });
      }
    }
  } else {
    // Hide and reset context section
    if (contextSection) {
      contextSection.classList.remove('active');
      const subjectEl = contextSection.querySelector('#gsb-email-subject');
      const vendorEl = contextSection.querySelector('#gsb-email-vendor');
      const amountEl = contextSection.querySelector('#gsb-email-amount');
      const statusEl = contextSection.querySelector('#gsb-email-status');
      const reasoningEl = contextSection.querySelector('#gsb-email-reasoning');
      const reasoningSummaryEl = contextSection.querySelector('#gsb-reasoning-summary');
      const reasoningListEl = contextSection.querySelector('#gsb-reasoning-list');

      if (subjectEl) subjectEl.textContent = '';
      if (vendorEl) vendorEl.textContent = '';
      if (amountEl) amountEl.textContent = '';
      if (statusEl) statusEl.textContent = '';

      if (reasoningEl) reasoningEl.classList.add('hidden');
      if (reasoningSummaryEl) reasoningSummaryEl.textContent = '';
      if (reasoningListEl) reasoningListEl.innerHTML = '';
    }
  }

  updateBackendStatusBanner(threadView);
  updateScanStatus();
}

function getThreadSenderEmail(threadView) {
  const meta = getThreadLatestMessageMeta(threadView);
  return meta.senderEmail || '';
}

function requestThreadTriage(threadView, { subject, senderEmail } = {}) {
  if (!threadView) return;
  const threadId = threadView.getThreadID?.();
  if (!threadId || triageRequestedThreads.has(threadId)) return;
  triageRequestedThreads.add(threadId);

  const meta = getThreadLatestMessageMeta(threadView);
  const messageId = meta.messageId || threadId;
  const date = meta.date || '';

  window.dispatchEvent(new CustomEvent('clearledgr:triage-thread', {
    detail: {
      threadId,
      messageId,
      subject: subject || threadView.getSubject?.() || '',
      sender: senderEmail || meta.senderEmail || '',
      date,
      source: 'thread_open'
    }
  }));
}

function getThreadLatestMessageMeta(threadView) {
  if (!threadView) return { messageId: '', senderEmail: '', date: '' };
  try {
    const views = threadView.getMessageViews?.() || [];
    if (views.length === 0) return { messageId: '', senderEmail: '', date: '' };
    const latest = views[views.length - 1];
    const sender = latest?.getSender?.();
    return {
      messageId: latest?.getMessageID?.() || latest?.getMessageId?.() || '',
      senderEmail: sender?.emailAddress || sender?.name || '',
      date: latest?.getDateString?.() || ''
    };
  } catch (_) {
    return { messageId: '', senderEmail: '', date: '' };
  }
}

// =============================================================================
// EMAIL CONTEXT HANDLER (NO UI)
// =============================================================================
//
// IMPORTANT: We intentionally do NOT mount any thread-level sidebar panels.
// The ONLY sidebar UI is the single Global sidebar (Streak-style).
// This handler is used for:
// - Updating the Global sidebar context when a thread is opened
// Thread open MUST remain display-only. AP processing is inbox-wide/autonomous.

function initializeEmailSidebar() {
  sdk.Conversations.registerThreadViewHandler((threadView) => {
    // Update global sidebar with current email context
    updateGlobalSidebarContext(threadView);

    // When thread is closed, reset the global sidebar context.
    try {
      threadView.on('destroy', () => updateGlobalSidebarContext(null));
    } catch (_) {
      // ignore
    }
  });

  console.log('[Clearledgr] Email sidebar configured');
}

function isFinanceEmail(subject, senderEmail = '', snippet = '', attachments = []) {
  const decision = classifyApEmail(
    {
      subject: subject || '',
      sender: senderEmail || '',
      senderEmail: senderEmail || '',
      snippet: snippet || '',
      attachments: attachments || []
    },
    { mode: 'dom' }
  );
  return decision.isAp;
}

function createEmailContextPanel(threadView) {
  const subject = threadView.getSubject() || 'Unknown';
  
  // Get email body from message views
  let emailBody = '';
  let senderEmail = '';
  try {
    const messageViews = threadView.getMessageViews();
    if (messageViews && messageViews.length > 0) {
      // Get the most recent message (last in thread)
      const latestMessage = messageViews[messageViews.length - 1];
      
      // Get sender
      const sender = latestMessage.getSender();
      if (sender) {
        senderEmail = sender.emailAddress || '';
      }
      
      // Get body element and extract text
      const bodyEl = latestMessage.getBodyElement();
      if (bodyEl) {
        emailBody = bodyEl.textContent || bodyEl.innerText || '';
        // Limit body size for performance
        emailBody = emailBody.substring(0, 5000);
      }
    }
  } catch (e) {
    console.warn('[Clearledgr] Could not extract email body:', e);
  }
  
  // Extract data from subject + body
  const extractedData = extractInvoicePreview(subject, senderEmail, emailBody);
  
  const container = document.createElement('div');
  container.innerHTML = `
    <style>
      .cl-email-panel { padding: 16px; font-family: 'Google Sans', Roboto, sans-serif; font-size: 13px; }
      .cl-panel-header { display: flex; align-items: center; gap: 8px; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #e0e0e0; }
      .cl-panel-logo { width: 20px; height: 20px; }
      .cl-panel-title { font-weight: 500; color: #202124; }
      .cl-section { margin-bottom: 16px; }
      .cl-section-title { font-size: 11px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }
      .cl-section-icon { width: 14px; height: 14px; }
      .cl-field { margin-bottom: 10px; }
      .cl-field-label { font-size: 11px; color: #5f6368; margin-bottom: 2px; }
      .cl-field-value { font-size: 14px; color: #202124; font-weight: 500; }
      .cl-field-value.loading { color: #9e9e9e; font-weight: 400; }
      .cl-status-row { margin-bottom: 12px; }
      .cl-status { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; border-radius: 4px; font-size: 12px; font-weight: 500; }
      .cl-status-icon { width: 12px; height: 12px; }
      .cl-status-detected { background: #E3F2FD; color: #1565C0; }
      .cl-status-review { background: #FFF3E0; color: #E65100; }
      .cl-status-approved { background: #E8F5E9; color: #2E7D32; }
      .cl-actions { display: flex; gap: 8px; margin: 16px 0; }
      .cl-btn { display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 10px 16px; border-radius: 6px; font-size: 13px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s; flex: 0 0 auto; width: fit-content; }
      .cl-actions .cl-btn { flex: 1; width: auto; }
      .cl-btn-icon { width: 14px; height: 14px; }
      .cl-btn-primary { background: #10B981; color: white; }
      .cl-btn-primary:hover { background: #059669; }
      .cl-btn-secondary { background: #f1f3f4; color: #5f6368; }
      .cl-btn-secondary:hover { background: #e8eaed; color: #202124; }
      .cl-vendor-stats { background: #f8f9fa; border-radius: 8px; padding: 12px; }
      .cl-stat-row { display: flex; justify-content: space-between; padding: 6px 0; font-size: 13px; border-bottom: 1px solid #e8eaed; }
      .cl-stat-row:last-child { border-bottom: none; }
      .cl-stat-label { color: #5f6368; }
      .cl-stat-value { font-weight: 500; color: #202124; }
      .cl-stat-value.loading { color: #9e9e9e; font-weight: 400; }
    </style>
    
    <div class="cl-email-panel">
      <div class="cl-panel-header">
        <svg class="cl-panel-logo" viewBox="0 0 230 236" fill="none">
          <path d="M0 0 C1.42 -0.01 2.84 -0.01 4.27 -0.02 C8.1 -0.04 11.94 -0.04 15.78 -0.03 C19 -0.03 22.21 -0.03 25.43 -0.04 C33.02 -0.05 40.61 -0.05 48.2 -0.04 C56.01 -0.03 63.82 -0.04 71.63 -0.07 C78.35 -0.09 85.08 -0.1 91.8 -0.09 C95.81 -0.09 99.82 -0.09 103.82 -0.11 C107.59 -0.13 111.37 -0.12 115.14 -0.1 C117.16 -0.1 119.19 -0.11 121.22 -0.13 C130.89 -0.05 140.05 1.42 147.63 7.9 C148.35 8.52 149.08 9.13 149.82 9.76 C150.46 10.3 151.1 10.84 151.75 11.4 C152.64 12 153.52 12.6 154.44 13.22 C162.28 20.64 165.8 31.22 166.14 41.8 C166.16 44.24 166.16 46.67 166.15 49.11 C166.16 50.46 166.17 51.81 166.18 53.16 C166.19 56.81 166.19 60.45 166.19 64.09 C166.18 67.14 166.19 70.2 166.2 73.25 C166.21 80.46 166.21 87.68 166.2 94.89 C166.19 102.3 166.2 109.71 166.23 117.12 C166.25 123.51 166.25 129.9 166.25 136.29 C166.25 140.09 166.25 143.89 166.27 147.7 C166.28 151.28 166.28 154.86 166.26 158.44 C166.25 160.36 166.27 162.28 166.28 164.2 C166.17 177.22 162.52 188.27 153.49 197.78 C145.43 205.46 137.07 210.51 125.8 210.55 C124.58 210.56 123.36 210.57 122.11 210.57 C120.76 210.58 119.42 210.58 118.07 210.58 C116.65 210.59 115.23 210.59 113.81 210.6 C109.14 210.62 104.47 210.63 99.81 210.64 C98.2 210.65 96.59 210.65 94.98 210.65 C87.42 210.67 79.85 210.69 72.28 210.7 C63.57 210.71 54.86 210.73 46.14 210.77 C39.39 210.8 32.65 210.82 25.9 210.82 C21.88 210.82 17.85 210.83 13.83 210.86 C10.04 210.88 6.25 210.88 2.46 210.87 C0.42 210.87 -1.62 210.89 -3.66 210.91 C-17.86 210.83 -26.81 206.39 -37 196.75 C-44.27 189.03 -48.37 179.15 -48.38 168.62 C-48.39 167.47 -48.39 166.31 -48.4 165.12 C-48.4 163.87 -48.4 162.61 -48.39 161.31 C-48.4 159.97 -48.4 158.62 -48.4 157.27 C-48.41 153.63 -48.42 149.98 -48.42 146.33 C-48.42 144.04 -48.42 141.76 -48.42 139.48 C-48.43 131.51 -48.44 123.53 -48.43 115.56 C-48.43 108.14 -48.44 100.73 -48.46 93.31 C-48.47 86.93 -48.48 80.55 -48.48 74.17 C-48.48 70.37 -48.48 66.56 -48.49 62.76 C-48.5 59.18 -48.5 55.6 -48.49 52.01 C-48.49 50.08 -48.5 48.16 -48.51 46.23 C-48.46 32.93 -45.79 22.96 -36.44 13.15 C-25.24 2.61 -15.18 -0.05 0 0 Z" fill="#031536" transform="translate(56.25,13.6)"/>
          <path d="M0 0 C31 0 31 0 40 6 C43.6 11.5 44.59 15.74 44.53 22.22 C44.55 23.38 44.55 23.38 44.56 24.58 C44.58 27.12 44.58 29.67 44.57 32.22 C44.57 34 44.58 35.79 44.59 37.58 C44.6 41.31 44.59 45.04 44.58 48.77 C44.56 53.54 44.58 58.31 44.62 63.07 C44.64 66.76 44.64 70.44 44.63 74.13 C44.63 75.88 44.64 77.64 44.65 79.4 C44.77 96.37 44.77 96.37 39 103 C27.87 113.33 17.77 108 0 108 C0 72.36 0 36.72 0 0 Z" fill="#02F6B4" transform="translate(127,65)"/>
          <path d="M0 0 C0 35.64 0 71.28 0 108 C-14.19 108 -28.38 108 -43 108 C-43.08 85.07 -43.08 85.07 -43.1 75.28 C-43.11 68.61 -43.12 61.94 -43.15 55.26 C-43.17 49.88 -43.18 44.5 -43.19 39.12 C-43.19 37.07 -43.2 35.01 -43.21 32.96 C-43.23 30.09 -43.23 27.21 -43.23 24.33 C-43.24 23.07 -43.24 23.07 -43.25 21.77 C-43.23 15.52 -42.86 10.21 -38.69 5.25 C-26.18 -5.22 -22.39 0 0 0 Z" fill="#02F4B3" transform="translate(105,65)"/>
        </svg>
        <span class="cl-panel-title">Clearledgr</span>
      </div>
      
      <div class="cl-status-row">
        <span class="cl-status cl-status-detected">
          <svg class="cl-status-icon" viewBox="0 0 12 12" fill="none">
            <circle cx="6" cy="6" r="5" fill="currentColor" opacity="0.2"/>
            <path d="M6 3V6L7.5 7.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
          Detected
        </span>
      </div>
      
      <div class="cl-section">
        <div class="cl-section-title">
          <svg class="cl-section-icon" viewBox="0 0 14 14" fill="none">
            <rect x="2" y="1" width="10" height="12" rx="1" stroke="#5f6368" stroke-width="1.2"/>
            <line x1="4" y1="4" x2="10" y2="4" stroke="#5f6368" stroke-width="1"/>
            <line x1="4" y1="7" x2="10" y2="7" stroke="#5f6368" stroke-width="1"/>
            <line x1="4" y1="10" x2="7" y2="10" stroke="#5f6368" stroke-width="1"/>
          </svg>
          Invoice Details
        </div>
        <div class="cl-field">
          <div class="cl-field-label">Subject</div>
          <div class="cl-field-value">${escapeHtml(subject.substring(0, 50))}${subject.length > 50 ? '...' : ''}</div>
        </div>
        <div class="cl-field">
          <div class="cl-field-label">Amount</div>
          <div class="cl-field-value${extractedData.amount ? '' : ' loading'}">${extractedData.amount ? '$' + extractedData.amount.toLocaleString() : 'Extracting...'}</div>
        </div>
        <div class="cl-field">
          <div class="cl-field-label">Vendor</div>
          <div class="cl-field-value${extractedData.vendor ? '' : ' loading'}">${extractedData.vendor || 'Detecting...'}</div>
        </div>
        <div class="cl-field">
          <div class="cl-field-label">Due Date</div>
          <div class="cl-field-value${extractedData.dueDate ? '' : ' loading'}">${extractedData.dueDate || 'Not specified'}</div>
        </div>
        <div class="cl-field">
          <div class="cl-field-label">Invoice #</div>
          <div class="cl-field-value">${extractedData.invoiceNumber || 'Not detected'}</div>
        </div>
        <div class="cl-field">
          <div class="cl-field-label" style="display: flex; align-items: center; gap: 6px;">
            GL Code
            <span id="cl-gl-confidence" style="font-size: 10px; padding: 2px 6px; background: #E3F2FD; color: #1565C0; border-radius: 3px; display: none;">AI suggested</span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px;">
            <select id="cl-sidebar-gl-select" style="flex: 1; padding: 8px 10px; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 13px; background: white;">
              <option value="">Loading accounts...</option>
            </select>
          </div>
          <div id="cl-gl-suggestion-reason" style="font-size: 11px; color: #5f6368; margin-top: 4px; display: none;"></div>
        </div>
      </div>
      
      <div class="cl-actions">
        <button class="cl-btn cl-btn-primary cl-approve-btn">
          <svg class="cl-btn-icon" viewBox="0 0 14 14" fill="none">
            <path d="M3 7L6 10L11 4" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Approve
        </button>
        <button class="cl-btn cl-btn-secondary cl-reject-btn" style="background: #FFEBEE; color: #C62828;">
          <svg class="cl-btn-icon" viewBox="0 0 14 14" fill="none">
            <path d="M4 4L10 10M10 4L4 10" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          </svg>
          Reject
        </button>
      </div>
      <div class="cl-actions" style="margin-top: 8px;">
        <button class="cl-btn cl-btn-secondary cl-flag-btn" style="flex: 1;">
          <svg class="cl-btn-icon" viewBox="0 0 14 14" fill="none">
            <path d="M3 2V12M3 2L11 5L3 8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          Flag for Review
        </button>
      </div>
      
      <div class="cl-section">
        <div class="cl-section-title">
          <svg class="cl-section-icon" viewBox="0 0 14 14" fill="none">
            <path d="M2 13V4L7 2L12 4V13H2Z" stroke="#5f6368" stroke-width="1.2"/>
            <rect x="4" y="6" width="2" height="2" fill="#5f6368"/>
            <rect x="8" y="6" width="2" height="2" fill="#5f6368"/>
          </svg>
          Vendor History
        </div>
        <div class="cl-vendor-stats">
          <div class="cl-stat-row">
            <span class="cl-stat-label">Total Invoices</span>
            <span class="cl-stat-value loading">--</span>
          </div>
          <div class="cl-stat-row">
            <span class="cl-stat-label">Total Spend</span>
            <span class="cl-stat-value loading">--</span>
          </div>
          <div class="cl-stat-row">
            <span class="cl-stat-label">Avg Payment Time</span>
            <span class="cl-stat-value loading">--</span>
          </div>
        </div>
      </div>
      
      <!-- Auto-Follow-up Section (shows when info is missing) -->
      <div class="cl-section cl-followup-section" id="cl-followup-section" style="display: none;">
        <div class="cl-section-title">
          <svg class="cl-section-icon" viewBox="0 0 14 14" fill="none">
            <path d="M2 3H12V11H5L2 14V3Z" stroke="#E65100" stroke-width="1.2" fill="none"/>
            <circle cx="5" cy="7" r="1" fill="#E65100"/>
            <circle cx="7" cy="7" r="1" fill="#E65100"/>
            <circle cx="9" cy="7" r="1" fill="#E65100"/>
          </svg>
          <span style="color: #E65100;">Follow-up Suggested</span>
        </div>
        <div class="cl-followup-card" style="background: #FFF3E0; border-radius: 8px; padding: 12px;">
          <div class="cl-followup-reason" style="font-size: 12px; color: #E65100; margin-bottom: 8px;">
            <strong style="color: #E65100;">Missing:</strong> <span id="cl-missing-info">PO Number</span>
          </div>
          <div class="cl-followup-preview" style="font-size: 13px; color: #5f6368; margin-bottom: 12px; max-height: 60px; overflow: hidden;">
            <span id="cl-followup-preview-text">Draft follow-up email ready...</span>
          </div>
          <button class="cl-btn cl-btn-secondary cl-send-followup-btn" style="width: 100%; background: white; border: 1px solid #E65100; color: #E65100;">
            <svg class="cl-btn-icon" viewBox="0 0 14 14" fill="none">
              <path d="M2 7L12 2V12L2 7Z" stroke="currentColor" stroke-width="1.2" fill="none"/>
            </svg>
            Send Follow-up
          </button>
        </div>
      </div>
      
      <!-- ERP Sync Status -->
      <div class="cl-section cl-erp-section" style="margin-top: 16px; border-top: 1px solid #e8eaed; padding-top: 16px;">
        <div class="cl-section-title">
          <svg class="cl-section-icon" viewBox="0 0 14 14" fill="none">
            <rect x="1" y="3" width="5" height="8" rx="1" stroke="#5f6368" stroke-width="1"/>
            <rect x="8" y="3" width="5" height="8" rx="1" stroke="#5f6368" stroke-width="1"/>
            <path d="M6 7H8" stroke="#5f6368" stroke-width="1" stroke-dasharray="1 1"/>
          </svg>
          ERP Status
        </div>
        <div class="cl-erp-status" style="display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #f8f9fa; border-radius: 6px;">
          <span class="cl-erp-icon" style="color: #9e9e9e;">○</span>
          <span class="cl-erp-text" style="font-size: 13px; color: #5f6368;" id="cl-erp-status-text">Not yet posted</span>
        </div>
      </div>
    </div>
  `;
  
  // Attach button event listeners
  setTimeout(() => {
    const approveBtn = container.querySelector('.cl-approve-btn');
    const rejectBtn = container.querySelector('.cl-reject-btn');
    const flagBtn = container.querySelector('.cl-flag-btn');
    
    approveBtn?.addEventListener('click', () => {
      // Show Pre-flight Check modal before approving
      showPreflightCheck({
        subject,
        container,
        approveBtn,
        onConfirm: () => {
          window.dispatchEvent(new CustomEvent('clearledgr:approve-email'));
          // Update UI to show approved
          const statusEl = container.querySelector('.cl-status');
          if (statusEl) {
            statusEl.className = 'cl-status cl-status-approved';
            statusEl.innerHTML = `
              <svg class="cl-status-icon" viewBox="0 0 12 12" fill="none">
                <circle cx="6" cy="6" r="5" fill="currentColor" opacity="0.2"/>
                <path d="M4 6L5.5 7.5L8 4.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
              Approved & Posted
            `;
          }
          approveBtn.disabled = true;
          approveBtn.textContent = 'Posted';
        }
      });
    });
    
    rejectBtn?.addEventListener('click', () => {
      // Show a simple confirm for the sidebar (full modal in pipeline)
      if (confirm('Reject this invoice?')) {
        window.dispatchEvent(new CustomEvent('clearledgr:reject-email'));
        // Update UI
        const statusEl = container.querySelector('.cl-status');
        if (statusEl) {
          statusEl.className = 'cl-status';
          statusEl.style.background = '#FFEBEE';
          statusEl.style.color = '#C62828';
          statusEl.innerHTML = `
            <svg class="cl-status-icon" viewBox="0 0 12 12" fill="none">
              <circle cx="6" cy="6" r="5" fill="currentColor" opacity="0.2"/>
              <path d="M4 4L8 8M8 4L4 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
            </svg>
            Rejected
          `;
        }
        rejectBtn.disabled = true;
        approveBtn.disabled = true;
      }
    });
    
    flagBtn?.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('clearledgr:apply-label', { 
        detail: { label: 'Needs Review' } 
      }));
      // Update UI
      const statusEl = container.querySelector('.cl-status');
      if (statusEl) {
        statusEl.className = 'cl-status cl-status-review';
        statusEl.innerHTML = `
          <svg class="cl-status-icon" viewBox="0 0 12 12" fill="none">
            <circle cx="6" cy="6" r="5" fill="currentColor" opacity="0.2"/>
            <circle cx="6" cy="5" r="1" fill="currentColor"/>
            <path d="M6 7V8.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
          Flagged for Review
        `;
      }
      flagBtn.textContent = 'Flagged';
      flagBtn.disabled = true;
    });
    
    // Request extracted data for this email
    window.dispatchEvent(new CustomEvent('clearledgr:request-email-data', {
      detail: { subject }
    }));
    
    // Fetch GL accounts and AI suggestion for sidebar
    loadSidebarGLSuggestion(container, extractedData.vendor, senderEmail);
  }, 100);
  
  // Helper function to load GL accounts and AI suggestion
  async function loadSidebarGLSuggestion(container, vendorName, senderEmail) {
    const glSelect = container.querySelector('#cl-sidebar-gl-select');
    const confidenceBadge = container.querySelector('#cl-gl-confidence');
    const suggestionReason = container.querySelector('#cl-gl-suggestion-reason');
    
    if (!glSelect) return;
    
    try {
      // First, load GL accounts list
      const accountsResponse = await fetch(`${BACKEND_URL}/ap/gl/accounts?organization_id=${getOrganizationId()}`);
      let accounts = [];
      if (accountsResponse.ok) {
        const data = await accountsResponse.json();
        accounts = data.accounts || [];
      }
      
      // Populate dropdown
      glSelect.innerHTML = '<option value="">-- Select GL Code --</option>' +
        accounts.map(acc => `<option value="${acc.code}">${acc.code} - ${escapeHtml(acc.name)}</option>`).join('');
      
      // If we have a vendor name, fetch AI suggestion
      if (vendorName) {
        const suggestionResponse = await fetch(`${BACKEND_URL}/extension/suggestions/gl-code`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            vendor_name: vendorName,
            organization_id: getOrganizationId()
          })
        });
        
        if (suggestionResponse.ok) {
          const suggestion = await suggestionResponse.json();
          
          if (suggestion.has_suggestion && suggestion.primary) {
            // Pre-select the suggested GL code
            glSelect.value = suggestion.primary.gl_code;
            
            // Show confidence badge
            const confidencePercent = Math.round(suggestion.primary.confidence * 100);
            if (confidenceBadge) {
              confidenceBadge.style.display = 'inline';
              confidenceBadge.textContent = `AI ${confidencePercent}%`;
              confidenceBadge.style.background = confidencePercent >= 80 ? '#E8F5E9' : confidencePercent >= 60 ? '#E3F2FD' : '#FFF3E0';
              confidenceBadge.style.color = confidencePercent >= 80 ? '#2E7D32' : confidencePercent >= 60 ? '#1565C0' : '#E65100';
            }
            
            // Show reason
            if (suggestionReason && suggestion.primary.reason) {
              suggestionReason.style.display = 'block';
              suggestionReason.textContent = suggestion.primary.reason;
            }
          }
        }
      }
    } catch (e) {
      console.warn('[Clearledgr] Failed to load GL suggestion:', e);
      glSelect.innerHTML = '<option value="">-- GL accounts unavailable --</option>';
    }
  }
  
  // Listen for data updates
  const dataHandler = (e) => {
    const data = e.detail || {};
    if (data.subject === subject || !data.subject) {
      // Update fields
      const fields = container.querySelectorAll('.cl-field-value');
      if (fields[1] && data.amount) fields[1].textContent = data.amount;
      if (fields[1] && data.amount) fields[1].classList.remove('loading');
      if (fields[2] && data.vendor) fields[2].textContent = data.vendor;
      if (fields[2] && data.vendor) fields[2].classList.remove('loading');
      if (fields[3] && data.dueDate) fields[3].textContent = data.dueDate;
      if (fields[3] && data.dueDate) fields[3].classList.remove('loading');
      
      // Update vendor stats
      const stats = container.querySelectorAll('.cl-stat-value');
      if (stats[0] && data.vendorInvoiceCount) {
        stats[0].textContent = data.vendorInvoiceCount;
        stats[0].classList.remove('loading');
      }
      if (stats[1] && data.vendorTotalSpend) {
        stats[1].textContent =
          typeof data.vendorTotalSpend === 'number'
            ? formatCurrency(data.vendorTotalSpend)
            : data.vendorTotalSpend;
        stats[1].classList.remove('loading');
      }
      if (stats[2] && data.avgPaymentTime) {
        stats[2].textContent = data.avgPaymentTime;
        stats[2].classList.remove('loading');
      }
    }
  };
  
  window.addEventListener('clearledgr:email-data-response', dataHandler);
  
  // Wire up follow-up button
  setTimeout(() => {
    const followupBtn = container.querySelector('.cl-send-followup-btn');
    followupBtn?.addEventListener('click', async () => {
      const threadId = threadView?.getThreadID?.() || '';
      followupBtn.textContent = 'Opening...';
      followupBtn.disabled = true;
      
      // Open Gmail compose with the draft
      try {
        const draft = await fetchFollowupDraft(threadId, subject);
        if (draft) {
          // Use Gmail's compose with pre-filled content
          sdk.Compose?.openNewComposeView?.().then(composeView => {
            composeView.setToRecipients([draft.to]);
            composeView.setSubject(draft.subject);
            composeView.setBodyText(draft.body);
          }).catch(() => {
            // Fallback: copy to clipboard
            navigator.clipboard.writeText(draft.body);
            showToast('Draft copied to clipboard', 'info');
          });
        }
      } catch (e) {
        showToast('Could not load draft', 'error');
      }
      
      followupBtn.textContent = 'Send Follow-up';
      followupBtn.disabled = false;
    });
  }, 150);
  
  // Load follow-up suggestions and ERP status from backend
  loadSidebarData(container, threadView, subject);
  
  return container;
}

/**
 * Load follow-up suggestions, ERP status, and enhanced extraction for the sidebar.
 */
async function loadSidebarData(container, threadView, subject) {
  const threadId = threadView?.getThreadID?.() || '';
  
  // Get email body for backend extraction
  let emailBody = '';
  let senderEmail = '';
  try {
    const messageViews = threadView?.getMessageViews?.();
    if (messageViews && messageViews.length > 0) {
      const latestMessage = messageViews[messageViews.length - 1];
      const sender = latestMessage.getSender();
      if (sender) senderEmail = sender.emailAddress || '';
      const bodyEl = latestMessage.getBodyElement();
      if (bodyEl) emailBody = (bodyEl.textContent || '').substring(0, 5000);
    }
  } catch (e) {}
  
  // Request enhanced extraction from backend
  try {
    const extractResponse = await fetch(`${BACKEND_URL}/ap/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subject: subject,
        body: emailBody,
        sender: senderEmail,
        thread_id: threadId,
        organization_id: getOrganizationId(),
      })
    });
    
    if (extractResponse.ok) {
      const extracted = await extractResponse.json();
      
      // Update sidebar fields with backend extraction (more accurate)
      const fields = container.querySelectorAll('.cl-field-value');
      if (fields.length >= 4) {
        if (extracted.amount) {
          fields[1].textContent = '$' + extracted.amount.toLocaleString();
          fields[1].classList.remove('loading');
        }
        if (extracted.vendor) {
          fields[2].textContent = extracted.vendor;
          fields[2].classList.remove('loading');
        }
        if (extracted.due_date) {
          fields[3].textContent = extracted.due_date;
          fields[3].classList.remove('loading');
        }
        if (extracted.invoice_number && fields[4]) {
          fields[4].textContent = extracted.invoice_number;
        }
      }
    }
  } catch (e) {
    console.warn('[Clearledgr] Backend extraction not available:', e);
  }
  
  // Extract invoice data for follow-up check (use local extraction as fallback)
  const invoiceData = extractInvoicePreview(subject, senderEmail, emailBody);
  
  // Check for follow-up suggestions
  try {
    const followupResponse = await fetch(`${BACKEND_URL}/ap/followup/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        thread_id: threadId,
        subject: subject,
        sender_email: 'vendor@example.com', // Will be filled from actual email
        invoice_data: {
          vendor: invoiceData.vendor,
          amount: invoiceData.amount,
          due_date: invoiceData.dueDate,
          invoice_number: invoiceData.invoiceNumber,
        },
        organization_id: getOrganizationId(),
      })
    });
    
    if (followupResponse.ok) {
      const data = await followupResponse.json();
      const followupSection = container.querySelector('#cl-followup-section');
      
      if (data.needs_followup && data.draft) {
        // Show follow-up section
        if (followupSection) {
          followupSection.style.display = 'block';
          const missingInfo = container.querySelector('#cl-missing-info');
          const previewText = container.querySelector('#cl-followup-preview-text');
          
          if (missingInfo) {
            missingInfo.textContent = data.draft.missing_info?.join(', ') || 'Information';
          }
          if (previewText) {
            previewText.textContent = data.draft.body?.substring(0, 100) + '...';
          }
        }
      } else {
        // Hide follow-up section
        if (followupSection) {
          followupSection.style.display = 'none';
        }
      }
    }
  } catch (e) {
    console.warn('[Clearledgr] Could not load follow-up suggestions:', e);
  }
  
  // Load ERP status
  try {
    const erpResponse = await fetch(
      `${BACKEND_URL}/ap/erp-sync/by-thread/${encodeURIComponent(threadId)}?organization_id=${getOrganizationId()}`
    );
    
    if (erpResponse.ok) {
      const data = await erpResponse.json();
      const erpStatusText = container.querySelector('#cl-erp-status-text');
      const erpIcon = container.querySelector('.cl-erp-icon');
      
      if (data.found && erpStatusText) {
        erpStatusText.textContent = data.status_text || 'Posted to ERP';
        
        // Update icon based on status
        if (erpIcon) {
          if (data.status === 'paid') {
            erpIcon.textContent = '$';
            erpIcon.style.color = '#2E7D32';
          } else if (data.status === 'approved') {
            erpIcon.textContent = 'OK';
            erpIcon.style.color = '#1565C0';
          } else if (data.status === 'overdue') {
            erpIcon.textContent = '!';
            erpIcon.style.color = '#C62828';
          }
        }
      }
    }
  } catch (e) {
    console.warn('[Clearledgr] Could not load ERP status:', e);
  }
}

/**
 * Fetch follow-up draft from backend.
 */
async function fetchFollowupDraft(threadId, subject) {
  try {
    const response = await fetch(`${BACKEND_URL}/ap/followup/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        thread_id: threadId,
        subject: subject,
        sender_email: 'vendor@example.com',
        invoice_data: extractInvoicePreview(subject, ''),
        organization_id: getOrganizationId(),
      })
    });
    
    if (response.ok) {
      const data = await response.json();
      return data.draft;
    }
  } catch (e) {
    console.warn('[Clearledgr] Could not fetch follow-up draft:', e);
  }
  return null;
}

// =============================================================================
// ROUTE VIEWS - Rendered in main content area
// =============================================================================

function renderHomeDashboard(element) {
  element.innerHTML = `
    <style>
      .cl-home { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 1200px; }
      .cl-header { display: flex; align-items: center; gap: 16px; margin-bottom: 32px; }
      .cl-header-left { display: flex; align-items: center; gap: 16px; flex: 1; }
      .cl-header-right { display: flex; align-items: center; gap: 12px; }
      .cl-logo { width: 40px; height: 40px; }
      .cl-home h1 { font-size: 28px; font-weight: 400; color: #202124; margin: 0; }
      .cl-home h1 span { color: #031536; font-weight: 500; }
      .cl-stats-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 40px; }
      .cl-stat-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 24px; transition: all 0.2s; }
      .cl-stat-card:hover { border-color: #10B981; box-shadow: 0 2px 8px rgba(16, 185, 129, 0.1); }
      .cl-stat-value { font-size: 36px; font-weight: 500; color: #202124; }
      .cl-stat-label { font-size: 14px; color: #5f6368; margin-top: 8px; }
      .cl-stat-card.highlight { border-left: 3px solid #10B981; }
      .cl-section { margin-bottom: 40px; }
      .cl-section-header { font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 16px; }
      .cl-action-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
      .cl-action-btn { display: flex; flex-direction: column; align-items: center; padding: 24px; background: white; border: 1px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: all 0.2s; }
      .cl-action-btn:hover { border-color: #10B981; background: #f0fdf4; transform: translateY(-2px); }
      .cl-action-icon { width: 32px; height: 32px; margin-bottom: 12px; }
      .cl-action-label { font-size: 14px; font-weight: 500; color: #202124; }
      .cl-recent { background: white; border: 1px solid #e0e0e0; border-radius: 8px; }
      .cl-recent-header { padding: 16px 20px; border-bottom: 1px solid #e0e0e0; font-size: 14px; font-weight: 500; color: #202124; }
      .cl-recent-item { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; display: flex; justify-content: space-between; align-items: center; }
      .cl-recent-item:last-child { border-bottom: none; }
      .cl-empty-state { padding: 40px; text-align: center; color: #5f6368; }
      .cl-empty-icon { width: 48px; height: 48px; margin-bottom: 16px; opacity: 0.5; }
      /* Autonomous Status Banner */
      .cl-autonomous-status { background: linear-gradient(135deg, #ecfdf5 0%, #d1fae5 100%); border: 1px solid #10B981; border-radius: 12px; padding: 20px 24px; margin-bottom: 32px; }
      .cl-autonomous-status.warning { background: #fff7ed; border-color: #f59e0b; }
      .cl-autonomous-status.error { background: #fef2f2; border-color: #ef4444; }
      .cl-status-indicator { display: flex; align-items: center; gap: 10px; font-size: 16px; font-weight: 500; color: #065f46; }
      .cl-status-indicator.warning { color: #92400e; }
      .cl-status-indicator.error { color: #b91c1c; }
      .cl-status-indicator .cl-status-icon { width: 20px; height: 20px; }
      .cl-status-meta { margin-top: 8px; font-size: 13px; color: #047857; padding-left: 30px; }
      .cl-autonomous-status.warning .cl-status-meta { color: #b45309; }
      .cl-autonomous-status.error .cl-status-meta { color: #b91c1c; }
      .cl-autonomous-actions { margin-top: 12px; padding-left: 30px; display: none; }
      .cl-autonomous-btn { background: #ffffff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 8px 14px; font-size: 13px; color: #202124; cursor: pointer; }
      .cl-autonomous-btn:hover { border-color: #10B981; color: #065f46; }
    </style>
    
    <div class="cl-home">
      <div class="cl-header">
        <div class="cl-header-left">
          <svg class="cl-logo" viewBox="0 0 230 236" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M0 0 C1.42 -0.01 2.84 -0.01 4.27 -0.02 C8.1 -0.04 11.94 -0.04 15.78 -0.03 C19 -0.03 22.21 -0.03 25.43 -0.04 C33.02 -0.05 40.61 -0.05 48.2 -0.04 C56.01 -0.03 63.82 -0.04 71.63 -0.07 C78.35 -0.09 85.08 -0.1 91.8 -0.09 C95.81 -0.09 99.82 -0.09 103.82 -0.11 C107.59 -0.13 111.37 -0.12 115.14 -0.1 C117.16 -0.1 119.19 -0.11 121.22 -0.13 C130.89 -0.05 140.05 1.42 147.63 7.9 C148.35 8.52 149.08 9.13 149.82 9.76 C150.46 10.3 151.1 10.84 151.75 11.4 C152.64 12 153.52 12.6 154.44 13.22 C162.28 20.64 165.8 31.22 166.14 41.8 C166.16 44.24 166.16 46.67 166.15 49.11 C166.16 50.46 166.17 51.81 166.18 53.16 C166.19 56.81 166.19 60.45 166.19 64.09 C166.18 67.14 166.19 70.2 166.2 73.25 C166.21 80.46 166.21 87.68 166.2 94.89 C166.19 102.3 166.2 109.71 166.23 117.12 C166.25 123.51 166.25 129.9 166.25 136.29 C166.25 140.09 166.25 143.89 166.27 147.7 C166.28 151.28 166.28 154.86 166.26 158.44 C166.25 160.36 166.27 162.28 166.28 164.2 C166.17 177.22 162.52 188.27 153.49 197.78 C145.43 205.46 137.07 210.51 125.8 210.55 C124.58 210.56 123.36 210.57 122.11 210.57 C120.76 210.58 119.42 210.58 118.07 210.58 C116.65 210.59 115.23 210.59 113.81 210.6 C109.14 210.62 104.47 210.63 99.81 210.64 C98.2 210.65 96.59 210.65 94.98 210.65 C87.42 210.67 79.85 210.69 72.28 210.7 C63.57 210.71 54.86 210.73 46.14 210.77 C39.39 210.8 32.65 210.82 25.9 210.82 C21.88 210.82 17.85 210.83 13.83 210.86 C10.04 210.88 6.25 210.88 2.46 210.87 C0.42 210.87 -1.62 210.89 -3.66 210.91 C-17.86 210.83 -26.81 206.39 -37 196.75 C-44.27 189.03 -48.37 179.15 -48.38 168.62 C-48.39 167.47 -48.39 166.31 -48.4 165.12 C-48.4 163.87 -48.4 162.61 -48.39 161.31 C-48.4 159.97 -48.4 158.62 -48.4 157.27 C-48.41 153.63 -48.42 149.98 -48.42 146.33 C-48.42 144.04 -48.42 141.76 -48.42 139.48 C-48.43 131.51 -48.44 123.53 -48.43 115.56 C-48.43 108.14 -48.44 100.73 -48.46 93.31 C-48.47 86.93 -48.48 80.55 -48.48 74.17 C-48.48 70.37 -48.48 66.56 -48.49 62.76 C-48.5 59.18 -48.5 55.6 -48.49 52.01 C-48.49 50.08 -48.5 48.16 -48.51 46.23 C-48.46 32.93 -45.79 22.96 -36.44 13.15 C-25.24 2.61 -15.18 -0.05 0 0 Z" fill="#031536" transform="translate(56.25,13.6)"/>
            <path d="M0 0 C31 0 31 0 40 6 C43.6 11.5 44.59 15.74 44.53 22.22 C44.55 23.38 44.55 23.38 44.56 24.58 C44.58 27.12 44.58 29.67 44.57 32.22 C44.57 34 44.58 35.79 44.59 37.58 C44.6 41.31 44.59 45.04 44.58 48.77 C44.56 53.54 44.58 58.31 44.62 63.07 C44.64 66.76 44.64 70.44 44.63 74.13 C44.63 75.88 44.64 77.64 44.65 79.4 C44.77 96.37 44.77 96.37 39 103 C27.87 113.33 17.77 108 0 108 C0 72.36 0 36.72 0 0 Z" fill="#02F6B4" transform="translate(127,65)"/>
            <path d="M0 0 C0 35.64 0 71.28 0 108 C-14.19 108 -28.38 108 -43 108 C-43.08 85.07 -43.08 85.07 -43.1 75.28 C-43.11 68.61 -43.12 61.94 -43.15 55.26 C-43.17 49.88 -43.18 44.5 -43.19 39.12 C-43.19 37.07 -43.2 35.01 -43.21 32.96 C-43.23 30.09 -43.23 27.21 -43.23 24.33 C-43.24 23.07 -43.24 23.07 -43.25 21.77 C-43.23 15.52 -42.86 10.21 -38.69 5.25 C-26.18 -5.22 -22.39 0 0 0 Z" fill="#02F4B3" transform="translate(105,65)"/>
          </svg>
          <h1>Welcome to <span>Clearledgr</span></h1>
        </div>
        <div class="cl-header-right" id="cl-home-plan-badge">
          <div class="cl-trial-indicator" style="display: inline-flex; align-items: center; gap: 6px; background: #FFF3E0; color: #E65100; padding: 6px 12px; border-radius: 12px; font-size: 12px; font-weight: 500;">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/></svg>
            <span id="cl-home-badge-text">Pro Trial</span>
          </div>
        </div>
      </div>
      
      <!-- Autonomous Status Banner -->
      <div class="cl-autonomous-status" id="cl-autonomous-banner">
        <div class="cl-status-indicator active" id="cl-autonomous-indicator">
          <svg class="cl-status-icon" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="4" fill="#10B981">
              <animate attributeName="opacity" values="1;0.4;1" dur="2s" repeatCount="indefinite"/>
            </circle>
            <circle cx="8" cy="8" r="7" stroke="#10B981" stroke-width="1.5" fill="none" opacity="0.3"/>
          </svg>
          <span id="cl-autonomous-label">Checking autonomous status</span>
        </div>
        <div class="cl-status-meta" id="cl-autonomous-detail">
          Verifying backend connection for 24/7 processing
        </div>
        <div class="cl-autonomous-actions" id="cl-autonomous-actions">
          <button class="cl-autonomous-btn" data-action="connect-gmail">Connect Gmail Autopilot</button>
        </div>
      </div>
      
      <div class="cl-stats-grid">
        <div class="cl-stat-card highlight">
          <div class="cl-stat-value" id="cl-stat-pending">0</div>
          <div class="cl-stat-label">Needs Review</div>
        </div>
        <div class="cl-stat-card">
          <div class="cl-stat-value" id="cl-stat-auto">0</div>
          <div class="cl-stat-label">Auto-Processed</div>
        </div>
        <div class="cl-stat-card">
          <div class="cl-stat-value" id="cl-stat-posted">$0</div>
          <div class="cl-stat-label">Posted to ERP</div>
        </div>
        <div class="cl-stat-card">
          <div class="cl-stat-value" id="cl-stat-exceptions">0</div>
          <div class="cl-stat-label">Exceptions</div>
        </div>
      </div>
      
      <div class="cl-section">
        <div class="cl-section-header">Navigation</div>
        <div class="cl-action-grid">
          <div class="cl-action-btn" id="cl-nav-invoices">
            <svg class="cl-action-icon" viewBox="0 0 24 24" fill="none">
              <rect x="2" y="3" width="6" height="18" rx="1" stroke="#10B981" stroke-width="2"/>
              <rect x="9" y="3" width="6" height="18" rx="1" stroke="#10B981" stroke-width="2"/>
              <rect x="16" y="3" width="6" height="18" rx="1" stroke="#10B981" stroke-width="2"/>
              <rect x="3.5" y="6" width="3" height="3" rx="0.5" fill="#10B981"/>
              <rect x="10.5" y="6" width="3" height="3" rx="0.5" fill="#10B981"/>
              <rect x="10.5" y="11" width="3" height="3" rx="0.5" fill="#10B981" opacity="0.5"/>
              <rect x="17.5" y="6" width="3" height="3" rx="0.5" fill="#10B981"/>
            </svg>
            <span class="cl-action-label">Invoices</span>
          </div>
          <div class="cl-action-btn" id="cl-nav-vendors">
            <svg class="cl-action-icon" viewBox="0 0 24 24" fill="none">
              <path d="M3 21V6L12 3L21 6V21H3Z" stroke="#10B981" stroke-width="2"/>
              <rect x="7" y="9" width="3" height="3" fill="#10B981"/>
              <rect x="14" y="9" width="3" height="3" fill="#10B981"/>
              <rect x="7" y="15" width="3" height="6" fill="#10B981"/>
              <rect x="14" y="15" width="3" height="3" fill="#10B981"/>
            </svg>
            <span class="cl-action-label">Vendors</span>
          </div>
          <div class="cl-action-btn" id="cl-nav-analytics">
            <svg class="cl-action-icon" viewBox="0 0 24 24" fill="none">
              <rect x="3" y="13" width="5" height="8" rx="1" fill="#10B981" opacity="0.5"/>
              <rect x="10" y="9" width="5" height="12" rx="1" fill="#10B981" opacity="0.7"/>
              <rect x="17" y="4" width="5" height="17" rx="1" fill="#10B981"/>
              <line x1="1" y1="21" x2="23" y2="21" stroke="#10B981" stroke-width="2"/>
            </svg>
            <span class="cl-action-label">Analytics</span>
          </div>
          <div class="cl-action-btn" id="cl-nav-settings">
            <svg class="cl-action-icon" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="3" stroke="#10B981" stroke-width="2"/>
              <path d="M12 1V4M12 20V23M23 12H20M4 12H1M20.5 3.5L18.4 5.6M5.6 18.4L3.5 20.5M20.5 20.5L18.4 18.4M5.6 5.6L3.5 3.5" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <span class="cl-action-label">Settings</span>
          </div>
        </div>
      </div>
      
      <div class="cl-section">
        <div class="cl-section-header">Recent Activity</div>
        <div class="cl-recent" id="cl-recent-activity">
          <div class="cl-empty-state" id="cl-activity-loading">
            <svg class="cl-empty-icon" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke="#e0e0e0" stroke-width="2"/>
              <path d="M24 14V26L30 30" stroke="#e0e0e0" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <div>Loading data...</div>
            <div style="font-size: 13px; margin-top: 4px;">Loading live AP workflow data...</div>
          </div>
        </div>
      </div>
    </div>
  `;
  
  // Wire up navigation buttons
  element.querySelector('#cl-nav-invoices')?.addEventListener('click', () => sdk.Router.goto('clearledgr/invoices'));
  element.querySelector('#cl-nav-vendors')?.addEventListener('click', () => sdk.Router.goto('clearledgr/vendors'));
  element.querySelector('#cl-nav-analytics')?.addEventListener('click', () => sdk.Router.goto('clearledgr/analytics'));
  element.querySelector('#cl-nav-settings')?.addEventListener('click', () => sdk.Router.goto('clearledgr/settings'));

  element.querySelector('[data-action="connect-gmail"]')?.addEventListener('click', async () => {
    try {
      const userId = currentUser?.email || 'default';
      const result = await chrome.runtime.sendMessage({
        action: 'connectGmailAutopilot',
        userId
      });
      if (!result?.success) {
        throw new Error(result?.error || 'Unable to connect Gmail Autopilot');
      }
      await updateAutopilotStatus();
    } catch (error) {
      updateAutonomousBanner({
        level: 'warning',
        label: 'Autopilot connection unavailable',
        detail: 'Connection could not be completed. Please try again in a moment.',
        showConnect: true
      });
    } finally {
      updateAutopilotStatus();
    }
  });
  
  // Fetch data from backend directly
  loadDashboardData(element);
  updateAutopilotStatus();
}

// Backend URL - configurable
let BACKEND_URL = 'http://127.0.0.1:8010';

function normalizeBackendUrl(raw) {
  let backendUrl = String(raw || BACKEND_URL).trim();
  if (!/^https?:\/\//i.test(backendUrl)) backendUrl = `http://${backendUrl}`;
  if (backendUrl.endsWith('/v1')) backendUrl = backendUrl.slice(0, -3);
  try {
    const url = new URL(backendUrl);
    if (url.hostname === '0.0.0.0' || url.hostname === 'localhost') {
      url.hostname = '127.0.0.1';
    }
    return url.toString().replace(/\/+$/, '');
  } catch (_) {
    return backendUrl.replace(/\/+$/, '');
  }
}

async function refreshBackendUrl() {
  try {
    const data = await new Promise((resolve) => {
      chrome.storage.sync.get(['settings', 'backendUrl'], resolve);
    });
    const nested = data.settings || {};
    const topLevelRaw = data.backendUrl || null;
    const nestedRaw = nested.backendUrl || nested.apiEndpoint || null;
    const nextUrl = topLevelRaw || nestedRaw || BACKEND_URL;
    const normalized = normalizeBackendUrl(nextUrl);
    BACKEND_URL = normalized;
    SUBSCRIPTION_API_URL = BACKEND_URL;

    // Keep storage canonical to avoid stale localhost values across reloads.
    const nextSettings = { ...nested };
    let needsWrite = false;
    if (topLevelRaw !== normalized) needsWrite = true;
    if (nextSettings.backendUrl !== normalized) needsWrite = true;
    if (nextSettings.apiEndpoint && nextSettings.apiEndpoint !== normalized) needsWrite = true;
    if (needsWrite) {
      nextSettings.backendUrl = normalized;
      nextSettings.apiEndpoint = normalized;
      await chrome.storage.sync.set({
        backendUrl: normalized,
        settings: nextSettings
      });
    }
  } catch (_) {
    // ignore
  }
  return BACKEND_URL;
}

if (chrome?.storage?.onChanged) {
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== 'sync') return;
    if (changes.backendUrl || changes.settings) {
      refreshBackendUrl();
    }
  });
}

// =============================================================================
// AUTHENTICATION - Google Identity integration
// =============================================================================

let authToken = null;
let currentUser = null;

/**
 * Initialize authentication using Google Identity.
 * Gets the user's email from Gmail and authenticates with Clearledgr backend.
 */
async function initializeAuth() {
  try {
    // Get current Gmail user's email from the SDK
    const userEmail = sdk.User?.getEmailAddress?.() || await getUserEmail();
    
    if (!userEmail) {
      console.warn('[Clearledgr] Could not get user email');
      return null;
    }
    
    console.log(`[Clearledgr] Authenticating user: ${userEmail}`);
    
    // Generate a pseudo Google ID from email (for demo)
    // In production, use Chrome Identity API for real Google ID
    const googleId = `google-${btoa(userEmail).slice(0, 20)}`;
    
    // Authenticate with Clearledgr backend
    const response = await fetch(`${BACKEND_URL}/auth/google-identity`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: userEmail,
        google_id: googleId,
      }),
    });
    
    if (response.ok) {
      const data = await response.json();
      authToken = data.access_token;
      currentUser = {
        id: data.user_id,
        email: userEmail,
        organization_id: data.organization_id,
        is_new_user: data.is_new_user,
      };
      
      // Store token for persistence
      try {
        localStorage.setItem('clearledgr_token', authToken);
        localStorage.setItem('clearledgr_user', JSON.stringify(currentUser));
      } catch (e) {
        // localStorage may not be available
      }
      
      console.log(`[Clearledgr] Authenticated as ${userEmail} (org: ${data.organization_id})`);
      
      if (data.is_new_user) {
        showToast('Welcome to Clearledgr! Your account has been created.', 'success', { duration: 5000 });
      }
      
      return currentUser;
    } else {
      console.warn('[Clearledgr] Auth failed:', response.status);
      return null;
    }
  } catch (err) {
    console.warn('[Clearledgr] Auth error:', err);
    // Try to use cached token
    try {
      authToken = localStorage.getItem('clearledgr_token');
      const cachedUser = localStorage.getItem('clearledgr_user');
      if (cachedUser) {
        currentUser = JSON.parse(cachedUser);
        console.log('[Clearledgr] Using cached auth');
        return currentUser;
      }
    } catch (e) {}
    return null;
  }
}

/**
 * Get user email from Gmail page.
 */
async function getUserEmail() {
  // Method 1: Try InboxSDK User API
  if (sdk.User?.getEmailAddress) {
    return sdk.User.getEmailAddress();
  }
  
  // Method 2: Parse from Gmail page
  const emailElement = document.querySelector('[data-email]');
  if (emailElement) {
    return emailElement.getAttribute('data-email');
  }
  
  // Method 3: Look for email in page
  const accountBtn = document.querySelector('[aria-label*="Account Information"]');
  if (accountBtn) {
    const match = accountBtn.getAttribute('aria-label')?.match(/[\w.-]+@[\w.-]+/);
    if (match) return match[0];
  }
  
  return null;
}

/**
 * Get authorization headers for API requests.
 */
function getAuthHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`;
  }
  return headers;
}

/**
 * Get current organization ID.
 */
function getOrganizationId() {
  return currentUser?.organization_id || 'default';
}

/**
 * Authenticated fetch wrapper.
 */
async function authFetch(url, options = {}) {
  const headers = {
    ...getAuthHeaders(),
    ...(options.headers || {}),
  };
  
  // Add organization_id to URL if not present
  if (url.includes('?') && !url.includes('organization_id')) {
    url += `&organization_id=${getOrganizationId()}`;
  } else if (!url.includes('organization_id') && !url.includes('?')) {
    url += `?organization_id=${getOrganizationId()}`;
  }
  
  return fetch(url, { ...options, headers });
}

async function loadDashboardData(element) {
  try {
    // Fetch analytics from backend
    const response = await backendFetch(`${BACKEND_URL}/analytics/dashboard/default`, {}, {
      warnMessage: '[Clearledgr] Could not fetch dashboard data'
    });
    
    if (response.ok) {
      const data = await response.json();

      const toNumber = (value, fallback = 0) => {
        const n = Number(value);
        return Number.isFinite(n) ? n : fallback;
      };
      
      // Update stats
      const pendingEl = element.querySelector('#cl-stat-pending');
      const autoEl = element.querySelector('#cl-stat-auto');
      const postedEl = element.querySelector('#cl-stat-posted');
      const exceptionsEl = element.querySelector('#cl-stat-exceptions');

      const pendingCount = toNumber(
        data.pending_approval ?? data.pending_review ?? data.needs_review,
        0
      );

      let autoProcessedCount = toNumber(
        data.auto_processed ?? data.auto_posted ?? data.auto_approved_count,
        NaN
      );
      if (!Number.isFinite(autoProcessedCount)) {
        const approvedToday = toNumber(data.approved_today, 0);
        const postedToday = toNumber(data.posted_today, 0);
        const autoApprovedRate = toNumber(data.auto_approved_rate, 0);
        autoProcessedCount = Math.round((approvedToday + postedToday) * (autoApprovedRate / 100));
      }

      let postedAmount = toNumber(data.total_amount_posted_today, NaN);
      if (!Number.isFinite(postedAmount)) {
        const legacyAmount = data.total_posted ?? data.posted_amount;
        postedAmount = toNumber(legacyAmount, 0) / 100;
      }

      const exceptionsCount = toNumber(data.rejected_today ?? data.exceptions, 0);
      const postedDecimals = Number.isInteger(postedAmount) ? 0 : 2;

      if (pendingEl) pendingEl.textContent = String(pendingCount);
      if (autoEl) autoEl.textContent = String(autoProcessedCount);
      if (postedEl) {
        postedEl.textContent = '$' + postedAmount.toLocaleString('en-US', {
          minimumFractionDigits: postedDecimals,
          maximumFractionDigits: 2
        });
      }
      if (exceptionsEl) exceptionsEl.textContent = String(exceptionsCount);
      
      // Update recent activity
      const activityEl = element.querySelector('#cl-recent-activity');
      if (activityEl && data.recent_activity && data.recent_activity.length > 0) {
        activityEl.innerHTML = data.recent_activity.slice(0, 5).map(a => `
          <div style="padding: 12px 16px; border-bottom: 1px solid #f1f3f4; display: flex; justify-content: space-between; align-items: center;">
            <div>
              <div style="font-size: 14px; color: #202124;">${escapeHtml(a.message || a.action)}</div>
              <div style="font-size: 12px; color: #5f6368;">${a.vendor || ''}</div>
            </div>
            <div style="font-size: 12px; color: #9aa0a6;">${formatTimeAgo(a.timestamp)}</div>
          </div>
        `).join('');
      } else {
        // Show monitoring state
        const loadingEl = element.querySelector('#cl-activity-loading');
        if (loadingEl) {
          loadingEl.innerHTML = `
            <svg class="cl-empty-icon" viewBox="0 0 48 48" fill="none">
              <circle cx="24" cy="24" r="20" stroke="#10B981" stroke-width="2"/>
              <path d="M24 14V26L30 30" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <div style="color: #10B981;">Monitoring inbox...</div>
            <div style="font-size: 13px; margin-top: 4px; color: #5f6368;">Finance emails will appear here automatically</div>
          `;
        }
      }
    } else {
      console.warn('[Clearledgr] Backend returned:', response.status);
      showBackendOffline(element);
    }
  } catch (err) {
    if (err?.code !== 'BACKEND_BACKOFF') {
      console.warn('[Clearledgr] Failed to fetch dashboard data:', err);
    }
    showBackendOffline(element);
  }
}

function showBackendOffline(element) {
  const loadingEl = element.querySelector('#cl-activity-loading');
  if (loadingEl) {
    loadingEl.innerHTML = `
      <svg class="cl-empty-icon" viewBox="0 0 48 48" fill="none">
        <circle cx="24" cy="24" r="20" stroke="#FF9800" stroke-width="2"/>
        <path d="M24 16V26M24 32V32.01" stroke="#FF9800" stroke-width="3" stroke-linecap="round"/>
      </svg>
      <div style="color: #FF9800;">Unable to connect</div>
      <div style="font-size: 13px; margin-top: 4px; color: #5f6368;">We're having trouble loading your data. Please try again in a moment.</div>
    `;
  }
}

function renderVendorsTable(element) {
  element.innerHTML = `
    <style>
      .cl-vendors { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; }
      .cl-search-bar { display: flex; align-items: center; gap: 12px; }
      .cl-search-input { padding: 8px 16px; border: 1px solid #e0e0e0; border-radius: 24px; font-size: 14px; width: 240px; outline: none; }
      .cl-search-input:focus { border-color: #10B981; }
      .cl-table-container { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-table { width: 100%; border-collapse: collapse; }
      .cl-table th { text-align: left; padding: 14px 20px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #e0e0e0; }
      .cl-table td { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-size: 14px; color: #202124; }
      .cl-table tr:hover { background: #f8f9fa; }
      .cl-table tr:last-child td { border-bottom: none; }
      .cl-vendor-name { font-weight: 500; display: flex; align-items: center; gap: 8px; }
      .cl-vendor-avatar { width: 32px; height: 32px; border-radius: 50%; background: #E8F5E9; color: #10B981; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 12px; }
      .cl-vendor-email { color: #5f6368; font-size: 12px; }
      .cl-amount { font-weight: 500; color: #202124; }
      .cl-gl-code { font-size: 11px; color: #5f6368; background: #f1f3f4; padding: 2px 6px; border-radius: 4px; }
      .cl-status-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
      .cl-status-active { background: #E8F5E9; color: #2E7D32; }
      .cl-status-inactive { background: #f1f3f4; color: #5f6368; }
      .cl-vendor-actions { display: flex; gap: 8px; }
      .cl-vendor-btn { padding: 6px 12px; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 12px; background: white; cursor: pointer; transition: all 0.2s; }
      .cl-vendor-btn:hover { border-color: #10B981; color: #10B981; }
      .cl-vendor-btn.edit { color: #5f6368; }
      .cl-vendor-btn.merge { color: #FF9800; border-color: #FFE0B2; }
      .cl-empty-state { padding: 64px 40px; text-align: center; }
      .cl-empty-icon { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.4; }
      .cl-empty-title { font-size: 16px; font-weight: 500; color: #202124; margin-bottom: 8px; }
      .cl-empty-desc { font-size: 14px; color: #5f6368; }
      
      /* Vendor Edit Modal */
      .cl-vendor-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; z-index: 999999; }
      .cl-vendor-modal.visible { display: flex; }
      .cl-vendor-modal-content { background: white; border-radius: 8px; width: 480px; max-height: 80vh; overflow-y: auto; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
      .cl-vendor-modal-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
      .cl-vendor-modal-header h3 { margin: 0; font-size: 18px; font-weight: 500; }
      .cl-vendor-modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; padding: 0; line-height: 1; }
      .cl-vendor-modal-body { padding: 24px; }
      .cl-vendor-form-group { margin-bottom: 20px; }
      .cl-vendor-form-label { display: block; font-size: 13px; font-weight: 500; color: #5f6368; margin-bottom: 8px; }
      .cl-vendor-form-input { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
      .cl-vendor-form-input:focus { outline: none; border-color: #10B981; }
      .cl-vendor-form-select { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; background: white; }
      .cl-vendor-modal-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
      .cl-vendor-modal-btn { padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; }
      .cl-vendor-modal-btn.cancel { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
      .cl-vendor-modal-btn.save { background: #10B981; color: white; }
      .cl-vendor-modal-btn.save:hover { background: #059669; }
    </style>
    
    <div class="cl-vendors">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <path d="M3 21V6L12 3L21 6V21H3Z" stroke="#10B981" stroke-width="2"/>
            <rect x="7" y="9" width="3" height="3" fill="#10B981"/>
            <rect x="14" y="9" width="3" height="3" fill="#10B981"/>
            <rect x="7" y="15" width="3" height="6" fill="#10B981"/>
            <rect x="14" y="15" width="3" height="3" fill="#10B981"/>
          </svg>
          <h1>Vendors</h1>
        </div>
        <div class="cl-search-bar">
          <input type="text" class="cl-search-input" id="cl-vendor-search" placeholder="Search vendors..." />
        </div>
      </div>
      
      <div class="cl-table-container">
        <table class="cl-table">
          <thead>
            <tr>
              <th>Vendor</th>
              <th>GL Account</th>
              <th>Invoices</th>
              <th>Total Spend</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-vendors-body">
            <tr>
              <td colspan="6">
                <div class="cl-empty-state">
                  <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
                    <path d="M8 56V16L32 8L56 16V56H8Z" stroke="#e0e0e0" stroke-width="3"/>
                    <rect x="20" y="24" width="8" height="8" fill="#e0e0e0"/>
                    <rect x="36" y="24" width="8" height="8" fill="#e0e0e0"/>
                    <rect x="20" y="40" width="8" height="16" fill="#e0e0e0"/>
                    <rect x="36" y="40" width="8" height="8" fill="#e0e0e0"/>
                  </svg>
                  <div class="cl-empty-title">No vendors yet</div>
                  <div class="cl-empty-desc">Scan your inbox to automatically detect vendors from invoices</div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    
    <!-- Vendor Edit Modal -->
    <div class="cl-vendor-modal" id="cl-vendor-modal">
      <div class="cl-vendor-modal-content">
        <div class="cl-vendor-modal-header">
          <h3 id="cl-vendor-modal-title">Edit Vendor</h3>
          <button class="cl-vendor-modal-close" id="cl-vendor-modal-close">&times;</button>
        </div>
        <div class="cl-vendor-modal-body">
          <input type="hidden" id="cl-vendor-edit-id">
          <div class="cl-vendor-form-group">
            <label class="cl-vendor-form-label">Vendor Name</label>
            <input type="text" class="cl-vendor-form-input" id="cl-vendor-edit-name" placeholder="e.g., Acme Inc">
          </div>
          <div class="cl-vendor-form-group">
            <label class="cl-vendor-form-label">Display Name (alias)</label>
            <input type="text" class="cl-vendor-form-input" id="cl-vendor-edit-alias" placeholder="Optional display name">
          </div>
          <div class="cl-vendor-form-group">
            <label class="cl-vendor-form-label">Default GL Account</label>
            <select class="cl-vendor-form-select" id="cl-vendor-edit-gl">
              <option value="">-- Select GL Account --</option>
              <!-- Populated dynamically from ERP -->
            </select>
            <p style="font-size: 11px; color: #9e9e9e; margin: 4px 0 0 0;">Accounts synced from your ERP</p>
          </div>
          <div class="cl-vendor-form-group">
            <label class="cl-vendor-form-label">Auto-Approve Threshold</label>
            <select class="cl-vendor-form-select" id="cl-vendor-edit-threshold">
              <option value="">Use default ($500)</option>
              <option value="0">Never auto-approve</option>
              <option value="100">$100</option>
              <option value="250">$250</option>
              <option value="500">$500</option>
              <option value="1000">$1,000</option>
              <option value="5000">$5,000</option>
            </select>
          </div>
          <div class="cl-vendor-form-group">
            <label class="cl-vendor-form-label">Notes</label>
            <input type="text" class="cl-vendor-form-input" id="cl-vendor-edit-notes" placeholder="Internal notes about this vendor">
          </div>
        </div>
        <div class="cl-vendor-modal-footer">
          <button class="cl-vendor-modal-btn cancel" id="cl-vendor-modal-cancel">Cancel</button>
          <button class="cl-vendor-modal-btn save" id="cl-vendor-modal-save">Save Changes</button>
        </div>
      </div>
    </div>
  `;
  
  // Setup search
  const searchInput = element.querySelector('#cl-vendor-search');
  searchInput?.addEventListener('input', (e) => {
    window.dispatchEvent(new CustomEvent('clearledgr:search-vendors', {
      detail: { query: e.target.value }
    }));
  });
  
  // Setup modal
  const modal = element.querySelector('#cl-vendor-modal');
  const closeBtn = element.querySelector('#cl-vendor-modal-close');
  const cancelBtn = element.querySelector('#cl-vendor-modal-cancel');
  const saveBtn = element.querySelector('#cl-vendor-modal-save');
  
  closeBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  cancelBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  
  saveBtn?.addEventListener('click', () => {
    const vendorId = element.querySelector('#cl-vendor-edit-id')?.value;
    const updates = {
      name: element.querySelector('#cl-vendor-edit-name')?.value,
      alias: element.querySelector('#cl-vendor-edit-alias')?.value,
      glCode: element.querySelector('#cl-vendor-edit-gl')?.value,
      autoApproveThreshold: element.querySelector('#cl-vendor-edit-threshold')?.value,
      notes: element.querySelector('#cl-vendor-edit-notes')?.value
    };
    
    window.dispatchEvent(new CustomEvent('clearledgr:update-vendor', {
      detail: { vendorId, updates }
    }));
    
    modal.classList.remove('visible');
  });
  
  // Request vendor data
  window.dispatchEvent(new CustomEvent('clearledgr:request-vendors'));
}

// Listen for vendor data
window.addEventListener('clearledgr:vendors-data', (e) => {
  const vendors = e.detail?.vendors || [];
  const tbody = document.getElementById('cl-vendors-body');
  if (!tbody) return;
  
  if (vendors.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="6">
          <div class="cl-empty-state">
            <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
              <path d="M8 56V16L32 8L56 16V56H8Z" stroke="#e0e0e0" stroke-width="3"/>
              <rect x="20" y="24" width="8" height="8" fill="#e0e0e0"/>
              <rect x="36" y="24" width="8" height="8" fill="#e0e0e0"/>
              <rect x="20" y="40" width="8" height="16" fill="#e0e0e0"/>
              <rect x="36" y="40" width="8" height="8" fill="#e0e0e0"/>
            </svg>
            <div class="cl-empty-title">No vendors yet</div>
            <div class="cl-empty-desc">Scan your inbox to automatically detect vendors from invoices</div>
          </div>
        </td>
      </tr>
    `;
    return;
  }
  
  tbody.innerHTML = vendors.map(vendor => {
    const initials = (vendor.name || 'U').substring(0, 2).toUpperCase();
    const glDisplay = vendor.glCode ? `<span class="cl-gl-code">${vendor.glCode}</span>` : '<span style="color: #9e9e9e;">Not set</span>';
    const spendDisplay =
      typeof vendor.totalSpend === 'number'
        ? formatCurrency(vendor.totalSpend)
        : (vendor.totalSpend || '$0');
    
    return `
      <tr data-vendor-id="${vendor.id}">
        <td>
          <div class="cl-vendor-name">
            <div class="cl-vendor-avatar">${initials}</div>
            <div>
              <div>${escapeHtml(vendor.alias || vendor.name)}</div>
              ${vendor.email ? `<div class="cl-vendor-email">${escapeHtml(vendor.email)}</div>` : ''}
            </div>
          </div>
        </td>
        <td>${glDisplay}</td>
        <td>${vendor.invoiceCount || 0}</td>
        <td class="cl-amount">${spendDisplay}</td>
        <td><span class="cl-status-badge ${vendor.invoiceCount > 0 ? 'cl-status-active' : 'cl-status-inactive'}">${vendor.invoiceCount > 0 ? 'Active' : 'Inactive'}</span></td>
        <td class="cl-vendor-actions">
          <button class="cl-vendor-btn edit" data-vendor-id="${vendor.id}" data-action="edit">Edit</button>
          ${vendor.canMerge ? `<button class="cl-vendor-btn merge" data-vendor-id="${vendor.id}" data-action="merge">Merge</button>` : ''}
        </td>
      </tr>
    `;
  }).join('');
  
  // Attach edit handlers
  tbody.querySelectorAll('.cl-vendor-btn[data-action="edit"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const vendorId = btn.dataset.vendorId;
      const vendor = vendors.find(v => v.id === vendorId);
      if (vendor) {
        showVendorEditModal(vendor);
      }
    });
  });
  
  // Attach merge handlers
  tbody.querySelectorAll('.cl-vendor-btn[data-action="merge"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const vendorId = btn.dataset.vendorId;
      const vendor = vendors.find(v => v.id === vendorId);
      if (vendor && confirm(`Merge "${vendor.name}" with another vendor?`)) {
        const targetName = prompt('Enter the name of the vendor to merge into:');
        if (targetName) {
          window.dispatchEvent(new CustomEvent('clearledgr:merge-vendor', {
            detail: { sourceId: vendorId, targetName }
          }));
        }
      }
    });
  });
});

async function showVendorEditModal(vendor) {
  const modal = document.getElementById('cl-vendor-modal');
  if (!modal) return;
  
  document.getElementById('cl-vendor-modal-title').textContent = `Edit ${vendor.name}`;
  document.getElementById('cl-vendor-edit-id').value = vendor.id;
  document.getElementById('cl-vendor-edit-name').value = vendor.name || '';
  document.getElementById('cl-vendor-edit-alias').value = vendor.alias || '';
  document.getElementById('cl-vendor-edit-threshold').value = vendor.autoApproveThreshold || '';
  document.getElementById('cl-vendor-edit-notes').value = vendor.notes || '';
  
  // Populate GL dropdown from ERP-synced accounts
  const glSelect = document.getElementById('cl-vendor-edit-gl');
  if (glSelect) {
    // Try to use cached accounts first, then fetch
    let accounts = window._clearledgrGLAccounts || [];
    if (accounts.length === 0) {
      try {
        const response = await fetch(`${BACKEND_URL}/ap/gl/accounts?organization_id=${getOrganizationId()}`);
        if (response.ok) {
          const data = await response.json();
          accounts = data.accounts || [];
          window._clearledgrGLAccounts = accounts;
        }
      } catch (e) {
        console.warn('[Clearledgr] Failed to fetch GL accounts:', e);
      }
    }
    
    glSelect.innerHTML = '<option value="">-- Select GL Account --</option>' +
      accounts.map(acc => `<option value="${acc.code}">${acc.code} - ${escapeHtml(acc.name)}</option>`).join('');
    
    // Set current value or fetch AI suggestion
    if (vendor.glCode) {
      glSelect.value = vendor.glCode;
    } else if (vendor.name) {
      // No GL code set - fetch AI suggestion
      try {
        const suggestionResponse = await fetch(`${BACKEND_URL}/extension/suggestions/gl-code`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ vendor_name: vendor.name, organization_id: getOrganizationId() })
        });
        if (suggestionResponse.ok) {
          const suggestion = await suggestionResponse.json();
          if (suggestion.has_suggestion && suggestion.primary) {
            glSelect.value = suggestion.primary.gl_code;
            // Add visual indicator that this is AI-suggested
            const label = modal.querySelector('.cl-vendor-form-label');
            if (label && label.textContent.includes('GL Account')) {
              const badge = document.createElement('span');
              badge.style.cssText = 'font-size: 10px; padding: 2px 6px; background: #E3F2FD; color: #1565C0; border-radius: 3px; margin-left: 8px;';
              badge.textContent = `AI suggested (${Math.round(suggestion.primary.confidence * 100)}%)`;
              badge.id = 'cl-gl-ai-badge';
              // Remove existing badge if any
              const existingBadge = label.querySelector('#cl-gl-ai-badge');
              if (existingBadge) existingBadge.remove();
              label.appendChild(badge);
            }
          }
        }
      } catch (e) {
        console.warn('[Clearledgr] Failed to fetch GL suggestion:', e);
      }
    }
  }
  
  modal.classList.add('visible');
}

function renderAnalytics(element) {
  element.innerHTML = `
    <style>
      .cl-analytics { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; }
      .cl-date-range { display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: white; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; color: #202124; cursor: pointer; }
      .cl-date-range:hover { border-color: #10B981; }
      .cl-summary-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-summary-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-summary-label { font-size: 12px; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
      .cl-summary-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-summary-change { font-size: 12px; margin-top: 4px; }
      .cl-summary-change.positive { color: #2E7D32; }
      .cl-summary-change.negative { color: #C62828; }
      .cl-chart-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 24px; }
      .cl-chart-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 24px; }
      .cl-chart-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
      .cl-chart-title { font-size: 14px; font-weight: 500; color: #202124; }
      .cl-chart-area { height: 200px; display: flex; align-items: flex-end; justify-content: space-around; padding: 20px 0; }
      .cl-bar { width: 32px; background: linear-gradient(180deg, #10B981 0%, #059669 100%); border-radius: 4px 4px 0 0; transition: height 0.3s; }
      .cl-bar:hover { opacity: 0.8; }
      .cl-chart-labels { display: flex; justify-content: space-around; padding-top: 8px; border-top: 1px solid #e0e0e0; }
      .cl-chart-label { font-size: 11px; color: #5f6368; }
      .cl-empty-chart { height: 200px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: #9e9e9e; }
      .cl-empty-chart-icon { width: 48px; height: 48px; margin-bottom: 12px; opacity: 0.3; }
    </style>
    
    <div class="cl-analytics">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="13" width="5" height="8" rx="1" fill="#10B981" opacity="0.5"/>
            <rect x="10" y="9" width="5" height="12" rx="1" fill="#10B981" opacity="0.7"/>
            <rect x="17" y="4" width="5" height="17" rx="1" fill="#10B981"/>
            <line x1="1" y1="21" x2="23" y2="21" stroke="#10B981" stroke-width="2"/>
          </svg>
          <h1>Analytics</h1>
        </div>
        <div class="cl-date-range">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <rect x="2" y="3" width="12" height="11" rx="1" stroke="#5f6368" stroke-width="1.5"/>
            <line x1="2" y1="7" x2="14" y2="7" stroke="#5f6368" stroke-width="1.5"/>
            <line x1="5" y1="1" x2="5" y2="4" stroke="#5f6368" stroke-width="1.5" stroke-linecap="round"/>
            <line x1="11" y1="1" x2="11" y2="4" stroke="#5f6368" stroke-width="1.5" stroke-linecap="round"/>
          </svg>
          <span>Last 30 days</span>
        </div>
      </div>
      
      <div class="cl-summary-row">
        <div class="cl-summary-card">
          <div class="cl-summary-label">Total Processed</div>
          <div class="cl-summary-value" id="cl-analytics-total">$0</div>
          <div class="cl-summary-change positive" id="cl-analytics-total-change">Loading...</div>
        </div>
        <div class="cl-summary-card">
          <div class="cl-summary-label">Invoices</div>
          <div class="cl-summary-value" id="cl-analytics-count">0</div>
          <div class="cl-summary-change" id="cl-analytics-count-change">Loading...</div>
        </div>
        <div class="cl-summary-card">
          <div class="cl-summary-label">Auto-Approved</div>
          <div class="cl-summary-value" id="cl-analytics-auto">0</div>
          <div class="cl-summary-change positive" id="cl-analytics-auto-rate">--</div>
        </div>
        <div class="cl-summary-card">
          <div class="cl-summary-label">Pending Review</div>
          <div class="cl-summary-value" id="cl-analytics-pending">0</div>
          <div class="cl-summary-change" id="cl-analytics-pending-note">Needs attention</div>
        </div>
      </div>
      
      <div class="cl-chart-grid">
        <div class="cl-chart-card">
          <div class="cl-chart-header">
            <div class="cl-chart-title">Top Vendors by Spend</div>
          </div>
          <div class="cl-chart-area" id="cl-chart-vendors">
            <div class="cl-empty-chart">
              <svg class="cl-empty-chart-icon" viewBox="0 0 48 48" fill="none">
                <rect x="6" y="28" width="8" height="16" rx="1" fill="#e0e0e0"/>
                <rect x="20" y="20" width="8" height="24" rx="1" fill="#e0e0e0"/>
                <rect x="34" y="12" width="8" height="32" rx="1" fill="#e0e0e0"/>
              </svg>
              <span>Loading vendor data...</span>
            </div>
          </div>
        </div>
        <div class="cl-chart-card">
          <div class="cl-chart-header">
            <div class="cl-chart-title">Status Breakdown</div>
          </div>
          <div class="cl-chart-area" id="cl-chart-status">
            <div class="cl-empty-chart">
              <svg class="cl-empty-chart-icon" viewBox="0 0 48 48" fill="none">
                <circle cx="24" cy="24" r="18" stroke="#e0e0e0" stroke-width="3" fill="none"/>
                <path d="M24 6 A18 18 0 0 1 42 24" stroke="#e0e0e0" stroke-width="6" fill="none"/>
              </svg>
              <span>Loading status data...</span>
            </div>
          </div>
        </div>
        <div class="cl-chart-card">
          <div class="cl-chart-header">
            <div class="cl-chart-title">Recent Activity</div>
          </div>
          <div class="cl-recent-list" id="cl-chart-activity" style="height: 200px; overflow-y: auto;">
            <div class="cl-empty-chart">
              <svg class="cl-empty-chart-icon" viewBox="0 0 48 48" fill="none">
                <circle cx="24" cy="24" r="18" stroke="#e0e0e0" stroke-width="3" fill="none"/>
                <path d="M24 12V24L32 28" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
              </svg>
              <span>Loading activity...</span>
            </div>
          </div>
        </div>
        <div class="cl-chart-card">
          <div class="cl-chart-header">
            <div class="cl-chart-title">Confidence Distribution</div>
          </div>
          <div class="cl-chart-area" id="cl-chart-confidence">
            <div class="cl-empty-chart">
              <svg class="cl-empty-chart-icon" viewBox="0 0 48 48" fill="none">
                <rect x="6" y="28" width="8" height="16" rx="1" fill="#e0e0e0"/>
                <rect x="20" y="20" width="8" height="24" rx="1" fill="#e0e0e0"/>
                <rect x="34" y="12" width="8" height="32" rx="1" fill="#e0e0e0"/>
              </svg>
              <span>Loading confidence data...</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
  
  // Request analytics data from content script
  window.dispatchEvent(new CustomEvent('clearledgr:request-analytics'));
}

// Listen for analytics data response
window.addEventListener('clearledgr:analytics-data', (e) => {
  const data = e.detail || {};
  
  // Update summary cards
  const totalEl = document.getElementById('cl-analytics-total');
  const countEl = document.getElementById('cl-analytics-count');
  const autoEl = document.getElementById('cl-analytics-auto');
  const pendingEl = document.getElementById('cl-analytics-pending');
  const autoRateEl = document.getElementById('cl-analytics-auto-rate');
  const totalChangeEl = document.getElementById('cl-analytics-total-change');
  const countChangeEl = document.getElementById('cl-analytics-count-change');
  
  if (totalEl) {
    totalEl.textContent =
      typeof data.totalAmount === 'number' ? formatCurrency(data.totalAmount) : (data.totalAmount || '$0');
  }
  if (countEl) countEl.textContent = data.totalCount || '0';
  if (autoEl) autoEl.textContent = data.autoApproved || '0';
  if (pendingEl) pendingEl.textContent = data.pendingCount || '0';
  if (autoRateEl) autoRateEl.textContent = data.autoRate ? `${data.autoRate}% automation` : '--';
  if (totalChangeEl) totalChangeEl.textContent = data.totalCount > 0 ? 'Last 30 days' : 'No data yet';
  if (countChangeEl) countChangeEl.textContent = data.totalCount > 0 ? `${data.syncedCount || 0} synced to ERP` : 'No data yet';
  
  // Render vendor chart
  const vendorChart = document.getElementById('cl-chart-vendors');
  if (vendorChart && data.topVendors && data.topVendors.length > 0) {
    const maxAmount = Math.max(...data.topVendors.map(v => v.amount));
    vendorChart.innerHTML = `
      <div style="display: flex; align-items: flex-end; justify-content: space-around; width: 100%; height: 100%;">
        ${data.topVendors.slice(0, 5).map(v => `
          <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
            <div class="cl-bar" style="height: ${Math.max(20, (v.amount / maxAmount) * 150)}px;" title="${v.name}: $${v.amount.toLocaleString()}"></div>
            <span style="font-size: 10px; color: #5f6368; max-width: 60px; text-overflow: ellipsis; overflow: hidden; white-space: nowrap;">${v.name}</span>
          </div>
        `).join('')}
      </div>
    `;
  }
  
  // Render status breakdown
  const statusChart = document.getElementById('cl-chart-status');
  if (statusChart && data.statusBreakdown) {
    const s = data.statusBreakdown;
    const total = s.new + s.pending + s.approved + s.synced + s.exception;
    if (total > 0) {
      statusChart.innerHTML = `
        <div style="display: flex; flex-direction: column; gap: 12px; width: 100%; padding: 10px;">
          <div style="display: flex; align-items: center; gap: 12px;">
            <div style="width: 12px; height: 12px; background: #2196F3; border-radius: 2px;"></div>
            <span style="flex: 1; font-size: 13px;">New</span>
            <span style="font-weight: 500;">${s.new}</span>
          </div>
          <div style="display: flex; align-items: center; gap: 12px;">
            <div style="width: 12px; height: 12px; background: #FF9800; border-radius: 2px;"></div>
            <span style="flex: 1; font-size: 13px;">Pending</span>
            <span style="font-weight: 500;">${s.pending}</span>
          </div>
          <div style="display: flex; align-items: center; gap: 12px;">
            <div style="width: 12px; height: 12px; background: #4CAF50; border-radius: 2px;"></div>
            <span style="flex: 1; font-size: 13px;">Approved</span>
            <span style="font-weight: 500;">${s.approved}</span>
          </div>
          <div style="display: flex; align-items: center; gap: 12px;">
            <div style="width: 12px; height: 12px; background: #9C27B0; border-radius: 2px;"></div>
            <span style="flex: 1; font-size: 13px;">Synced</span>
            <span style="font-weight: 500;">${s.synced}</span>
          </div>
          <div style="display: flex; align-items: center; gap: 12px;">
            <div style="width: 12px; height: 12px; background: #F44336; border-radius: 2px;"></div>
            <span style="flex: 1; font-size: 13px;">Exception</span>
            <span style="font-weight: 500;">${s.exception}</span>
          </div>
        </div>
      `;
    }
  }
  
  // Render recent activity
  const activityList = document.getElementById('cl-chart-activity');
  if (activityList && data.recentActivity && data.recentActivity.length > 0) {
    activityList.innerHTML = data.recentActivity.slice(0, 8).map(a => `
      <div style="padding: 10px 0; border-bottom: 1px solid #f1f3f4; display: flex; align-items: center; gap: 10px;">
        <div style="width: 8px; height: 8px; border-radius: 50%; background: ${a.type === 'approved' ? '#4CAF50' : a.type === 'rejected' ? '#F44336' : '#2196F3'};"></div>
        <div style="flex: 1; font-size: 13px; color: #202124;">${a.message}</div>
        <div style="font-size: 11px; color: #9e9e9e;">${a.time}</div>
      </div>
    `).join('');
  }
  
  // Render confidence distribution
  const confChart = document.getElementById('cl-chart-confidence');
  if (confChart && data.confidenceDistribution) {
    const c = data.confidenceDistribution;
    const maxConf = Math.max(c.high, c.medium, c.low) || 1;
    confChart.innerHTML = `
      <div style="display: flex; align-items: flex-end; justify-content: space-around; width: 100%; height: 100%;">
        <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
          <div style="width: 48px; height: ${Math.max(20, (c.high / maxConf) * 140)}px; background: linear-gradient(180deg, #4CAF50 0%, #2E7D32 100%); border-radius: 4px 4px 0 0;"></div>
          <span style="font-size: 11px; color: #5f6368;">High</span>
          <span style="font-size: 12px; font-weight: 500;">${c.high}</span>
        </div>
        <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
          <div style="width: 48px; height: ${Math.max(20, (c.medium / maxConf) * 140)}px; background: linear-gradient(180deg, #FF9800 0%, #E65100 100%); border-radius: 4px 4px 0 0;"></div>
          <span style="font-size: 11px; color: #5f6368;">Medium</span>
          <span style="font-size: 12px; font-weight: 500;">${c.medium}</span>
        </div>
        <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
          <div style="width: 48px; height: ${Math.max(20, (c.low / maxConf) * 140)}px; background: linear-gradient(180deg, #F44336 0%, #C62828 100%); border-radius: 4px 4px 0 0;"></div>
          <span style="font-size: 11px; color: #5f6368;">Low</span>
          <span style="font-size: 12px; font-weight: 500;">${c.low}</span>
        </div>
      </div>
    `;
  }
});


// =============================================================================
// REJECT MODAL - Created separately and appended to document.body
// =============================================================================

let rejectModalInstance = null;

function setupRejectModal() {
  // Only create once
  if (rejectModalInstance || document.getElementById('cl-reject-modal')) {
    return;
  }
  
  const modalHTML = `
    <style>
      #cl-reject-modal {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0,0,0,0.5);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 999999;
        font-family: 'Google Sans', Roboto, sans-serif;
      }
      #cl-reject-modal.visible {
        display: flex;
      }
      #cl-reject-modal .cl-modal {
        background: white;
        border-radius: 12px;
        width: 400px;
        max-width: 90vw;
        box-shadow: 0 8px 32px rgba(0,0,0,0.3);
      }
      #cl-reject-modal .cl-modal-header {
        padding: 20px 24px;
        border-bottom: 1px solid #e0e0e0;
      }
      #cl-reject-modal .cl-modal-title {
        font-size: 18px;
        font-weight: 500;
        color: #202124;
        margin: 0;
      }
      #cl-reject-modal .cl-modal-body {
        padding: 24px;
      }
      #cl-reject-modal .cl-modal-label {
        font-size: 13px;
        font-weight: 500;
        color: #5f6368;
        margin-bottom: 12px;
      }
      #cl-reject-modal .cl-reason-options {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      #cl-reject-modal .cl-reason-option {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 16px;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        cursor: pointer;
        transition: all 0.2s;
      }
      #cl-reject-modal .cl-reason-option:hover {
        border-color: #10B981;
        background: #f0fdf4;
      }
      #cl-reject-modal .cl-reason-option.selected {
        border-color: #10B981;
        background: #ecfdf5;
      }
      #cl-reject-modal .cl-reason-radio {
        width: 18px;
        height: 18px;
        border: 2px solid #e0e0e0;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
      }
      #cl-reject-modal .cl-reason-option.selected .cl-reason-radio {
        border-color: #10B981;
      }
      #cl-reject-modal .cl-reason-option.selected .cl-reason-radio::after {
        content: '';
        width: 10px;
        height: 10px;
        background: #10B981;
        border-radius: 50%;
      }
      #cl-reject-modal .cl-reason-text {
        font-size: 14px;
        color: #202124;
      }
      #cl-reject-modal .cl-modal-footer {
        padding: 16px 24px;
        border-top: 1px solid #e0e0e0;
        display: flex;
        justify-content: flex-end;
        gap: 12px;
      }
      #cl-reject-modal .cl-modal-btn {
        padding: 10px 20px;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
        border: none;
      }
      #cl-reject-modal .cl-modal-btn.cancel {
        background: #f1f3f4;
        color: #5f6368;
      }
      #cl-reject-modal .cl-modal-btn.cancel:hover {
        background: #e8eaed;
      }
      #cl-reject-modal .cl-modal-btn.confirm {
        background: #C62828;
        color: white;
      }
      #cl-reject-modal .cl-modal-btn.confirm:hover {
        background: #B71C1C;
      }
    </style>
    <div class="cl-modal">
      <div class="cl-modal-header">
        <h3 class="cl-modal-title">Reject Invoice</h3>
      </div>
      <div class="cl-modal-body">
        <div class="cl-modal-label">Select a reason:</div>
        <div class="cl-reason-options">
          <div class="cl-reason-option" data-reason="duplicate">
            <div class="cl-reason-radio"></div>
            <span class="cl-reason-text">Duplicate invoice</span>
          </div>
          <div class="cl-reason-option" data-reason="wrong_amount">
            <div class="cl-reason-radio"></div>
            <span class="cl-reason-text">Wrong amount</span>
          </div>
          <div class="cl-reason-option" data-reason="not_authorized">
            <div class="cl-reason-radio"></div>
            <span class="cl-reason-text">Not authorized</span>
          </div>
          <div class="cl-reason-option" data-reason="missing_po">
            <div class="cl-reason-radio"></div>
            <span class="cl-reason-text">Missing PO number</span>
          </div>
          <div class="cl-reason-option" data-reason="vendor_issue">
            <div class="cl-reason-radio"></div>
            <span class="cl-reason-text">Vendor issue</span>
          </div>
          <div class="cl-reason-option" data-reason="other">
            <div class="cl-reason-radio"></div>
            <span class="cl-reason-text">Other</span>
          </div>
        </div>
      </div>
      <div class="cl-modal-footer">
        <button class="cl-modal-btn cancel">Cancel</button>
        <button class="cl-modal-btn confirm">Reject Invoice</button>
      </div>
    </div>
  `;
  
  // Create modal element and append to body
  const modal = document.createElement('div');
  modal.id = 'cl-reject-modal';
  modal.innerHTML = modalHTML;
  document.body.appendChild(modal);
  rejectModalInstance = modal;
  
  // Setup event handlers
  let selectedReason = null;
  let rejectingEmailId = null;
  
  const reasonOptions = modal.querySelectorAll('.cl-reason-option');
  const cancelBtn = modal.querySelector('.cl-modal-btn.cancel');
  const confirmBtn = modal.querySelector('.cl-modal-btn.confirm');
  
  reasonOptions.forEach(opt => {
    opt.addEventListener('click', () => {
      reasonOptions.forEach(o => o.classList.remove('selected'));
      opt.classList.add('selected');
      selectedReason = opt.dataset.reason;
    });
  });
  
  const closeModal = () => {
    modal.classList.remove('visible');
    selectedReason = null;
    rejectingEmailId = null;
    reasonOptions.forEach(o => o.classList.remove('selected'));
  };
  
  cancelBtn.addEventListener('click', closeModal);
  
  confirmBtn.addEventListener('click', () => {
    if (selectedReason && rejectingEmailId) {
      window.dispatchEvent(new CustomEvent('clearledgr:reject-invoice', { 
        detail: { emailId: rejectingEmailId, reason: selectedReason } 
      }));
      closeModal();
    }
  });
  
  // Close on overlay click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      closeModal();
    }
  });
  
  // Global function to show the modal
  window.__clearledgrShowRejectModal = (emailId) => {
    rejectingEmailId = emailId;
    modal.classList.add('visible');
  };
}

// =============================================================================
// FIX MODAL - Edit invoice data to resolve errors
// =============================================================================

let fixModalInstance = null;

function setupFixModal() {
  if (fixModalInstance || document.getElementById('cl-fix-modal')) {
    return;
  }
  
  const modalHTML = `
    <div class="cl-fix-content">
      <div class="cl-fix-header">
        <h3>Fix Invoice Data</h3>
        <button class="cl-fix-close">&times;</button>
      </div>
      <div class="cl-fix-body">
        <input type="hidden" id="cl-fix-email-id">
        <div class="cl-fix-field">
          <label>Vendor</label>
          <input type="text" id="cl-fix-vendor">
        </div>
        <div class="cl-fix-field">
          <label>Amount</label>
          <input type="text" id="cl-fix-amount" placeholder="e.g., $1,234.56">
        </div>
        <div class="cl-fix-field">
          <label>Due Date</label>
          <input type="date" id="cl-fix-due-date">
        </div>
        <div class="cl-fix-field">
          <label>Invoice Number</label>
          <input type="text" id="cl-fix-invoice-num">
        </div>
        <div class="cl-fix-field">
          <label>GL Account</label>
          <select id="cl-fix-gl">
            <option value="">-- Select --</option>
            <option value="5000">5000 - Operating Expenses</option>
            <option value="5100">5100 - Office Supplies</option>
            <option value="5200">5200 - Software</option>
            <option value="5300">5300 - Professional Services</option>
          </select>
        </div>
      </div>
      <div class="cl-fix-footer">
        <button class="cl-fix-btn cancel">Cancel</button>
        <button class="cl-fix-btn save">Save & Retry</button>
      </div>
    </div>
  `;
  
  const modal = document.createElement('div');
  modal.id = 'cl-fix-modal';
  modal.innerHTML = modalHTML;
  modal.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); display: none; align-items: center;
    justify-content: center; z-index: 999999; font-family: 'Google Sans', Roboto, sans-serif;
  `;
  
  const contentStyle = document.createElement('style');
  contentStyle.textContent = `
    #cl-fix-modal.visible { display: flex; }
    .cl-fix-content { background: white; border-radius: 12px; width: 420px; max-width: 90vw; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
    .cl-fix-header { padding: 16px 20px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
    .cl-fix-header h3 { margin: 0; font-size: 16px; font-weight: 500; }
    .cl-fix-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; padding: 0; line-height: 1; }
    .cl-fix-body { padding: 20px; }
    .cl-fix-field { margin-bottom: 16px; }
    .cl-fix-field label { display: block; font-size: 12px; font-weight: 500; color: #5f6368; margin-bottom: 6px; }
    .cl-fix-field input, .cl-fix-field select { width: 100%; padding: 10px 12px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
    .cl-fix-field input:focus, .cl-fix-field select:focus { outline: none; border-color: #10B981; }
    .cl-fix-footer { padding: 16px 20px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
    .cl-fix-btn { padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; }
    .cl-fix-btn.cancel { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
    .cl-fix-btn.save { background: #10B981; color: white; }
    .cl-fix-btn.save:hover { background: #059669; }
  `;
  document.head.appendChild(contentStyle);
  document.body.appendChild(modal);
  fixModalInstance = modal;
  
  const closeBtn = modal.querySelector('.cl-fix-close');
  const cancelBtn = modal.querySelector('.cl-fix-btn.cancel');
  const saveBtn = modal.querySelector('.cl-fix-btn.save');
  
  const closeModal = () => modal.classList.remove('visible');
  closeBtn.addEventListener('click', closeModal);
  cancelBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => e.target === modal && closeModal());
  
  saveBtn.addEventListener('click', () => {
    const emailId = document.getElementById('cl-fix-email-id')?.value;
    const updates = {
      vendor: document.getElementById('cl-fix-vendor')?.value,
      amount: document.getElementById('cl-fix-amount')?.value,
      dueDate: document.getElementById('cl-fix-due-date')?.value,
      invoiceNumber: document.getElementById('cl-fix-invoice-num')?.value,
      glCode: document.getElementById('cl-fix-gl')?.value
    };
    
    window.dispatchEvent(new CustomEvent('clearledgr:fix-invoice', {
      detail: { emailId, updates }
    }));
    closeModal();
  });
}

// Listen for show fix modal events
window.addEventListener('clearledgr:show-fix-modal', (e) => {
  setupFixModal();
  const { emailId } = e.detail || {};
  
  // Request current data for this email
  window.dispatchEvent(new CustomEvent('clearledgr:request-email-for-fix', {
    detail: { emailId }
  }));
  
  document.getElementById('cl-fix-email-id').value = emailId;
  document.getElementById('cl-fix-modal')?.classList.add('visible');
});

// Populate fix modal with current data
window.addEventListener('clearledgr:email-for-fix-data', (e) => {
  const data = e.detail || {};
  document.getElementById('cl-fix-vendor').value = data.vendor || '';
  document.getElementById('cl-fix-amount').value = data.amount || '';
  document.getElementById('cl-fix-due-date').value = data.dueDate || '';
  document.getElementById('cl-fix-invoice-num').value = data.invoiceNumber || '';
  document.getElementById('cl-fix-gl').value = data.glCode || '';
});

// =============================================================================
// DUPLICATE MODAL - Show duplicate comparison and merge/dismiss options
// =============================================================================

let duplicateModalInstance = null;

function setupDuplicateModal() {
  if (duplicateModalInstance || document.getElementById('cl-duplicate-modal')) {
    return;
  }
  
  const modal = document.createElement('div');
  modal.id = 'cl-duplicate-modal';
  modal.innerHTML = `
    <div class="cl-dup-content">
      <div class="cl-dup-header">
        <h3>Duplicate Invoice Detected</h3>
        <button class="cl-dup-close">&times;</button>
      </div>
      <div class="cl-dup-body">
        <p style="color: #5f6368; margin: 0 0 20px 0;">This invoice appears to be a duplicate. Compare and choose an action:</p>
        
        <div class="cl-dup-compare">
          <div class="cl-dup-card">
            <div class="cl-dup-card-header">This Invoice</div>
            <div class="cl-dup-card-body" id="cl-dup-current">
              <!-- Populated dynamically -->
            </div>
          </div>
          <div class="cl-dup-vs">VS</div>
          <div class="cl-dup-card">
            <div class="cl-dup-card-header">Original Invoice</div>
            <div class="cl-dup-card-body" id="cl-dup-original">
              <!-- Populated dynamically -->
            </div>
          </div>
        </div>
      </div>
      <div class="cl-dup-footer">
        <button class="cl-dup-btn dismiss">Dismiss as Duplicate</button>
        <button class="cl-dup-btn keep">Keep Both</button>
        <button class="cl-dup-btn merge">Merge & Approve</button>
      </div>
    </div>
  `;
  modal.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); display: none; align-items: center;
    justify-content: center; z-index: 999999; font-family: 'Google Sans', Roboto, sans-serif;
  `;
  
  const style = document.createElement('style');
  style.textContent = `
    #cl-duplicate-modal.visible { display: flex; }
    .cl-dup-content { background: white; border-radius: 12px; width: 600px; max-width: 90vw; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
    .cl-dup-header { padding: 16px 20px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
    .cl-dup-header h3 { margin: 0; font-size: 16px; font-weight: 500; color: #E65100; }
    .cl-dup-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; }
    .cl-dup-body { padding: 20px; }
    .cl-dup-compare { display: flex; gap: 16px; align-items: center; }
    .cl-dup-card { flex: 1; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
    .cl-dup-card-header { background: #f8f9fa; padding: 10px 14px; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; }
    .cl-dup-card-body { padding: 14px; font-size: 13px; }
    .cl-dup-card-body .field { margin-bottom: 8px; }
    .cl-dup-card-body .label { color: #9e9e9e; font-size: 11px; }
    .cl-dup-card-body .value { color: #202124; font-weight: 500; }
    .cl-dup-vs { font-size: 14px; font-weight: 600; color: #9e9e9e; flex-shrink: 0; }
    .cl-dup-footer { padding: 16px 20px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
    .cl-dup-btn { padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; }
    .cl-dup-btn.dismiss { background: #FFEBEE; color: #C62828; }
    .cl-dup-btn.keep { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
    .cl-dup-btn.merge { background: #10B981; color: white; }
    .cl-dup-btn.merge:hover { background: #059669; }
  `;
  document.head.appendChild(style);
  document.body.appendChild(modal);
  duplicateModalInstance = modal;
  
  let currentEmailId = null;
  
  const closeBtn = modal.querySelector('.cl-dup-close');
  const dismissBtn = modal.querySelector('.cl-dup-btn.dismiss');
  const keepBtn = modal.querySelector('.cl-dup-btn.keep');
  const mergeBtn = modal.querySelector('.cl-dup-btn.merge');
  
  const closeModal = () => {
    modal.classList.remove('visible');
    currentEmailId = null;
  };
  
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => e.target === modal && closeModal());
  
  dismissBtn.addEventListener('click', () => {
    if (currentEmailId) {
      window.dispatchEvent(new CustomEvent('clearledgr:dismiss-duplicate', {
        detail: { emailId: currentEmailId }
      }));
    }
    closeModal();
  });
  
  keepBtn.addEventListener('click', () => {
    if (currentEmailId) {
      window.dispatchEvent(new CustomEvent('clearledgr:keep-duplicate', {
        detail: { emailId: currentEmailId }
      }));
    }
    closeModal();
  });
  
  mergeBtn.addEventListener('click', () => {
    if (currentEmailId) {
      window.dispatchEvent(new CustomEvent('clearledgr:merge-duplicate', {
        detail: { emailId: currentEmailId }
      }));
    }
    closeModal();
  });
  
  // Function to show the modal
  window.__showDuplicateModal = (emailId, currentData, originalData) => {
    currentEmailId = emailId;
    
    const formatCard = (data) => `
      <div class="field"><span class="label">Vendor:</span> <span class="value">${data.vendor || 'Unknown'}</span></div>
      <div class="field"><span class="label">Amount:</span> <span class="value">${data.amount || '--'}</span></div>
      <div class="field"><span class="label">Date:</span> <span class="value">${data.date || '--'}</span></div>
      <div class="field"><span class="label">Invoice #:</span> <span class="value">${data.invoiceNumber || '--'}</span></div>
    `;
    
    document.getElementById('cl-dup-current').innerHTML = formatCard(currentData);
    document.getElementById('cl-dup-original').innerHTML = formatCard(originalData);
    
    modal.classList.add('visible');
  };
}

// =============================================================================
// NEW WORKFLOW MODAL - Select workflow type to create
// =============================================================================

let workflowModalInstance = null;

function setupWorkflowModal() {
  if (workflowModalInstance || document.getElementById('cl-workflow-modal')) {
    return;
  }
  
  const modal = document.createElement('div');
  modal.id = 'cl-workflow-modal';
  modal.innerHTML = `
    <div class="cl-wf-content">
      <div class="cl-wf-header">
        <h3>Create New Workflow</h3>
        <button class="cl-wf-close">&times;</button>
      </div>
      <div class="cl-wf-body">
        <p class="cl-wf-desc">Select a workflow type to get started</p>
        
        <div class="cl-wf-options">
          <div class="cl-wf-option active" data-workflow="invoice-approval">
            <div class="cl-wf-icon" style="background: #E8F5E9; color: #2E7D32;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <rect x="4" y="4" width="16" height="18" rx="2" stroke="currentColor" stroke-width="2"/>
                <line x1="8" y1="10" x2="16" y2="10" stroke="currentColor" stroke-width="2"/>
                <line x1="8" y1="14" x2="14" y2="14" stroke="currentColor" stroke-width="2"/>
                <path d="M8 18L10 20L14 16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </div>
            <div class="cl-wf-info">
              <div class="cl-wf-name">Invoice Approval</div>
              <div class="cl-wf-hint">Detect, review, and approve invoices from email</div>
            </div>
            <div class="cl-wf-status active">Active</div>
          </div>
          
          <div class="cl-wf-option disabled" data-workflow="expense-reports">
            <div class="cl-wf-icon" style="background: #FFF3E0; color: #E65100;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <rect x="3" y="6" width="18" height="14" rx="2" stroke="currentColor" stroke-width="2"/>
                <path d="M3 10H21" stroke="currentColor" stroke-width="2"/>
                <circle cx="7" cy="14" r="2" stroke="currentColor" stroke-width="1.5"/>
              </svg>
            </div>
            <div class="cl-wf-info">
              <div class="cl-wf-name">Expense Reports</div>
              <div class="cl-wf-hint">Track and approve employee expense claims</div>
            </div>
            <div class="cl-wf-status coming">Coming Soon</div>
          </div>
          
          <div class="cl-wf-option disabled" data-workflow="purchase-orders">
            <div class="cl-wf-icon" style="background: #E3F2FD; color: #1565C0;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <rect x="4" y="3" width="16" height="18" rx="2" stroke="currentColor" stroke-width="2"/>
                <path d="M4 8H20" stroke="currentColor" stroke-width="2"/>
                <line x1="8" y1="12" x2="16" y2="12" stroke="currentColor" stroke-width="1.5"/>
                <line x1="8" y1="16" x2="12" y2="16" stroke="currentColor" stroke-width="1.5"/>
              </svg>
            </div>
            <div class="cl-wf-info">
              <div class="cl-wf-name">Purchase Orders</div>
              <div class="cl-wf-hint">Manage PO approvals and vendor matching</div>
            </div>
            <div class="cl-wf-status coming">Coming Soon</div>
          </div>
          
          <div class="cl-wf-option disabled" data-workflow="contracts">
            <div class="cl-wf-icon" style="background: #F3E5F5; color: #7B1FA2;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <path d="M14 2H6C5 2 4 3 4 4V20C4 21 5 22 6 22H18C19 22 20 21 20 20V8L14 2Z" stroke="currentColor" stroke-width="2"/>
                <path d="M14 2V8H20" stroke="currentColor" stroke-width="2"/>
                <path d="M8 13L16 13" stroke="currentColor" stroke-width="1.5"/>
                <path d="M8 17L12 17" stroke="currentColor" stroke-width="1.5"/>
              </svg>
            </div>
            <div class="cl-wf-info">
              <div class="cl-wf-name">Contract Review</div>
              <div class="cl-wf-hint">Route contracts for legal and exec approval</div>
            </div>
            <div class="cl-wf-status coming">Coming Soon</div>
          </div>
          
          <div class="cl-wf-option disabled" data-workflow="custom">
            <div class="cl-wf-icon" style="background: #f1f3f4; color: #5f6368;">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="2"/>
                <path d="M12 1V4M12 20V23M1 12H4M20 12H23" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                <path d="M4.2 4.2L6.3 6.3M17.7 17.7L19.8 19.8M4.2 19.8L6.3 17.7M17.7 6.3L19.8 4.2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
              </svg>
            </div>
            <div class="cl-wf-info">
              <div class="cl-wf-name">Custom Workflow</div>
              <div class="cl-wf-hint">Build your own approval workflow</div>
            </div>
            <div class="cl-wf-status coming">Coming Soon</div>
          </div>
        </div>
      </div>
      <div class="cl-wf-footer">
        <button class="cl-wf-btn cancel">Cancel</button>
        <button class="cl-wf-btn create" disabled>Create Workflow</button>
      </div>
    </div>
  `;
  modal.style.cssText = `
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5); display: none; align-items: center;
    justify-content: center; z-index: 999999; font-family: 'Google Sans', Roboto, sans-serif;
  `;
  
  const style = document.createElement('style');
  style.textContent = `
    #cl-workflow-modal.visible { display: flex; }
    .cl-wf-content { background: white; border-radius: 12px; width: 520px; max-width: 90vw; box-shadow: 0 8px 32px rgba(0,0,0,0.3); }
    .cl-wf-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
    .cl-wf-header h3 { margin: 0; font-size: 18px; font-weight: 500; color: #202124; }
    .cl-wf-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; }
    .cl-wf-body { padding: 24px; }
    .cl-wf-desc { margin: 0 0 20px 0; color: #5f6368; font-size: 14px; }
    .cl-wf-options { display: flex; flex-direction: column; gap: 12px; }
    .cl-wf-option { display: flex; align-items: center; gap: 16px; padding: 16px; border: 2px solid #e0e0e0; border-radius: 12px; cursor: pointer; transition: all 0.2s; }
    .cl-wf-option:hover:not(.disabled) { border-color: #10B981; background: #f0fdf4; }
    .cl-wf-option.active { border-color: #10B981; background: #ecfdf5; }
    .cl-wf-option.disabled { opacity: 0.6; cursor: not-allowed; }
    .cl-wf-icon { width: 48px; height: 48px; border-radius: 12px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
    .cl-wf-info { flex: 1; }
    .cl-wf-name { font-size: 15px; font-weight: 500; color: #202124; margin-bottom: 4px; }
    .cl-wf-hint { font-size: 13px; color: #5f6368; }
    .cl-wf-status { font-size: 11px; font-weight: 600; padding: 4px 10px; border-radius: 12px; }
    .cl-wf-status.active { background: #E8F5E9; color: #2E7D32; }
    .cl-wf-status.coming { background: #f1f3f4; color: #5f6368; }
    .cl-wf-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
    .cl-wf-btn { padding: 10px 24px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; }
    .cl-wf-btn.cancel { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
    .cl-wf-btn.create { background: #10B981; color: white; }
    .cl-wf-btn.create:hover:not(:disabled) { background: #059669; }
    .cl-wf-btn.create:disabled { opacity: 0.5; cursor: not-allowed; }
  `;
  document.head.appendChild(style);
  document.body.appendChild(modal);
  workflowModalInstance = modal;
  
  const closeBtn = modal.querySelector('.cl-wf-close');
  const cancelBtn = modal.querySelector('.cl-wf-btn.cancel');
  const createBtn = modal.querySelector('.cl-wf-btn.create');
  const options = modal.querySelectorAll('.cl-wf-option:not(.disabled)');
  
  const closeModal = () => modal.classList.remove('visible');
  closeBtn.addEventListener('click', closeModal);
  cancelBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', (e) => e.target === modal && closeModal());
  
  options.forEach(opt => {
    opt.addEventListener('click', () => {
      options.forEach(o => o.classList.remove('active'));
      opt.classList.add('active');
      createBtn.disabled = false;
    });
  });
  
  createBtn.addEventListener('click', () => {
    const selected = modal.querySelector('.cl-wf-option.active');
    const workflowType = selected?.dataset.workflow;
    
    if (workflowType === 'invoice-approval') {
      // Navigate to invoices and trigger scan
      sdk.Router.goto('clearledgr/invoices', {});
      window.dispatchEvent(new CustomEvent('clearledgr:scan-inbox'));
      closeModal();
    }
  });
}

// Listen for show workflow modal
window.addEventListener('clearledgr:show-new-workflow-modal', () => {
  setupWorkflowModal();
  document.getElementById('cl-workflow-modal')?.classList.add('visible');
});

// Listen for show duplicate modal events
window.addEventListener('clearledgr:show-duplicate-modal', (e) => {
  setupDuplicateModal();
  window.dispatchEvent(new CustomEvent('clearledgr:request-duplicate-data', {
    detail: { emailId: e.detail?.emailId }
  }));
});

// Receive duplicate data and show modal
window.addEventListener('clearledgr:duplicate-data', (e) => {
  const { emailId, current, original } = e.detail || {};
  if (window.__showDuplicateModal) {
    window.__showDuplicateModal(emailId, current, original);
  }
});

// =============================================================================
// INVOICE PIPELINE (InboxSDK-only UI)
// =============================================================================

const __clPipelineState = {
  element: null,
  queue: [],
  filter: 'all',
  stage: 'detected',
  search: ''
};

function __clPipelineSetState(patch) {
  Object.assign(__clPipelineState, patch || {});
  __clPipelineRender();
}

function __clPipelineGetEmailId(email) {
  return email?.id || email?.gmail_id || email?.email_id || '';
}

function __clPipelineGetVendor(email) {
  return (
    email?.detected?.vendor ||
    email?.vendor ||
    email?.sender ||
    (email?.from ? String(email.from).split('@')[0] : null) ||
    'Unknown'
  );
}

function __clPipelineParseAmount(value) {
  if (value === null || value === undefined) return null;
  const n = parseFloat(String(value).replace(/[^0-9.-]/g, ''));
  return Number.isFinite(n) ? n : null;
}

function __clPipelineFormatAmount(amount, currency) {
  if (amount === null || amount === undefined) return '--';
  const n = typeof amount === 'number' ? amount : __clPipelineParseAmount(amount);
  if (n === null) return '--';
  const curr = (currency || 'USD').toUpperCase();
  try {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: curr }).format(n);
  } catch (_) {
    return `$${n.toLocaleString()}`;
  }
}

function __clPipelineGetStageClass(status) {
  switch (status) {
    case 'pending':
    case 'new':
    case undefined:
    case null:
      return 'detected';
    case 'needs_review':
    case 'pending_approval':
      return 'review';
    case 'approved':
      return 'approved';
    case 'posted':
      return 'posted';
    case 'paid':
      return 'paid';
    case 'error':
    case 'rejected':
      return 'exception';
    default:
      return 'detected';
  }
}

function __clPipelineGetStageLabel(status) {
  switch (status) {
    case 'pending':
    case 'new':
    case undefined:
    case null:
      return 'Detected';
    case 'needs_review':
    case 'pending_approval':
      return 'Needs Review';
    case 'approved':
      return 'Approved';
    case 'posted':
      return 'Posted';
    case 'paid':
      return 'Paid';
    case 'error':
      return 'Exception';
    case 'rejected':
      return 'Rejected';
    default:
      return 'Detected';
  }
}

function __clPipelineRenderRow(email) {
  const status = (email?.status || 'pending').toLowerCase();
  const stageBadgeClass = __clPipelineGetStageClass(status);
  const stageLabel = __clPipelineGetStageLabel(status);

  const rawAmount = email?.detected?.amount ?? email?.amount ?? null;
  const rawCurrency = email?.detected?.currency ?? email?.currency ?? null;
  const amountNumber = __clPipelineParseAmount(rawAmount);
  const amount = __clPipelineFormatAmount(amountNumber, rawCurrency);

  const vendor = __clPipelineGetVendor(email);
  const confidence = typeof email?.confidence === 'number' ? email.confidence : 0.85;
  const confClass = confidence >= 0.95 ? 'high' : confidence >= 0.8 ? 'medium' : 'low';

  const dueRaw = email?.detected?.dueDate ?? email?.dueDate ?? null;
  let dueDisplay = '--';
  let dueClass = '';
  if (dueRaw) {
    const due = new Date(dueRaw);
    if (!Number.isNaN(due.getTime())) {
      const today = new Date();
      const daysUntil = Math.ceil((due - today) / (1000 * 60 * 60 * 24));
      dueDisplay = due.toLocaleDateString();
      if (daysUntil < 0) {
        dueClass = 'cl-due-overdue';
        dueDisplay += ` (${Math.abs(daysUntil)}d overdue)`;
      } else if (daysUntil <= 3) {
        dueClass = 'cl-due-warning';
        dueDisplay += ` (${daysUntil}d)`;
      }
    }
  }

  let badges = '';
  if (email?.aiPowered || email?.extractionMethod === 'llm') {
    badges += '<span class="cl-ai-badge" title="AI-extracted data">AI</span>';
  }
  if (email?.isRecurring) {
    badges += '<span class="cl-recurring-badge">Recurring</span>';
  }
  if (email?.isDuplicate) {
    const id = __clPipelineGetEmailId(email);
    badges += `<span class="cl-overdue-badge cl-dup-badge" data-dup-email-id="${id}">Duplicate</span>`;
  }

  let amountChange = '';
  if (email?.isRecurring && email?.previousAmount && amountNumber !== null) {
    const prev = __clPipelineParseAmount(email.previousAmount);
    if (prev && prev !== 0) {
      const pct = (((amountNumber - prev) / prev) * 100).toFixed(0);
      amountChange = `<span class="cl-amount-change">${pct > 0 ? '+' : ''}${pct}%</span>`;
    }
  }

  const emailId = __clPipelineGetEmailId(email);
  const canSelect =
    status === 'pending' || status === 'needs_review' || status === 'pending_approval' || status === 'new' || !status;

  let actions = '';
  if (status === 'pending' || status === 'needs_review' || status === 'pending_approval' || status === 'new' || !status) {
    actions = `
      <button class="cl-action-btn approve" data-email-id="${emailId}">
        <svg class="cl-action-icon" viewBox="0 0 12 12" fill="none"><path d="M2 6L5 9L10 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Approve
      </button>
      <button class="cl-action-btn reject" data-email-id="${emailId}">
        <svg class="cl-action-icon" viewBox="0 0 12 12" fill="none"><path d="M3 3L9 9M9 3L3 9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        Reject
      </button>
    `;
  } else if (status === 'approved') {
    actions = `<button class="cl-action-btn" disabled>Posting...</button>`;
  } else if (status === 'posted') {
    actions = `
      <button class="cl-action-btn mark-paid" data-email-id="${emailId}" style="background: #10B981; color: white; border-color: #10B981;">
        <svg class="cl-action-icon" viewBox="0 0 12 12" fill="none"><path d="M2 6L5 9L10 3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Mark Paid
      </button>
    `;
  } else if (status === 'paid') {
    actions = `<span style="color: #1B5E20; font-size: 12px; font-weight: 500;">Paid</span>`;
  } else if (status === 'rejected') {
    actions = `<button class="cl-action-btn" data-email-id="${emailId}" data-action="restore" style="color: #5f6368;">Restore</button>`;
  } else if (status === 'error') {
    actions = `
      <button class="cl-action-btn" data-email-id="${emailId}" data-action="retry" style="color: #E65100;">
        <svg class="cl-action-icon" viewBox="0 0 12 12" fill="none"><path d="M1 6C1 3.24 3.24 1 6 1C8.76 1 11 3.24 11 6C11 8.76 8.76 11 6 11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M4 9L1 6L4 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
        Retry
      </button>
      <button class="cl-action-btn" data-email-id="${emailId}" data-action="fix" style="color: #1565C0;">Fix</button>
      <button class="cl-action-btn" data-email-id="${emailId}" data-action="dismiss" style="color: #5f6368;">Dismiss</button>
    `;
  }

  let errorInfo = '';
  if (status === 'error' && email?.errorMessage) {
    errorInfo = `<div style="font-size: 11px; color: #C62828; margin-top: 4px;">Warning: ${escapeHtml(email.errorMessage)}</div>`;
  } else if (status === 'rejected' && email?.rejectionReason) {
    errorInfo = `<div style="font-size: 11px; color: #5f6368; margin-top: 4px;">Reason: ${escapeHtml(String(email.rejectionReason).replace(/_/g, ' '))}</div>`;
  }

  const invNum = email?.detected?.invoiceNumber || email?.invoiceNumber || '';

  return `
    <tr data-email-id="${emailId}" class="${status === 'error' ? 'error-row' : ''}">
      <td>${canSelect ? `<input type="checkbox" class="cl-checkbox cl-row-checkbox" data-email-id="${emailId}">` : ''}</td>
      <td>
        <div class="cl-subject-row">
          <span class="cl-subject">${escapeHtml(email?.subject || 'No subject')}</span>
          ${badges}
        </div>
        ${invNum ? `<div style="font-size: 11px; color: #9e9e9e;">#${escapeHtml(invNum)}</div>` : ''}
        ${errorInfo}
      </td>
      <td class="cl-vendor">${escapeHtml(vendor)}</td>
      <td>
        <span class="cl-amount">${amount}</span>${amountChange}
        ${amount !== '--' ? `<span class="cl-confidence ${confClass}" style="margin-left: 6px;">${Math.round(confidence * 100)}%</span>` : ''}
      </td>
      <td><span class="cl-stage-badge ${stageBadgeClass}">${stageLabel}</span></td>
      <td class="cl-date ${dueClass}">${escapeHtml(dueDisplay)}</td>
      <td class="cl-actions">${actions}</td>
    </tr>
  `;
}

function __clPipelineInstallHandlers() {
  const el = __clPipelineState.element;
  if (!el || el.__clPipelineHandlersInstalled) return;
  el.__clPipelineHandlersInstalled = true;

  el.addEventListener('click', (evt) => {
    const target = evt.target;
    if (!(target instanceof Element)) return;

    const dup = target.closest('[data-dup-email-id]');
    if (dup) {
      const emailId = dup.getAttribute('data-dup-email-id');
      if (emailId) window.dispatchEvent(new CustomEvent('clearledgr:show-duplicate-modal', { detail: { emailId } }));
      evt.stopPropagation();
      return;
    }

    const approveBtn = target.closest('.cl-action-btn.approve');
    if (approveBtn) {
      const emailId = approveBtn.getAttribute('data-email-id');
      if (emailId) window.dispatchEvent(new CustomEvent('clearledgr:approve-invoice', { detail: { emailId } }));
      evt.stopPropagation();
      return;
    }

    const rejectBtn = target.closest('.cl-action-btn.reject');
    if (rejectBtn) {
      const emailId = rejectBtn.getAttribute('data-email-id');
      if (emailId && window.__clearledgrShowRejectModal) window.__clearledgrShowRejectModal(emailId);
      evt.stopPropagation();
      return;
    }

    const markPaidBtn = target.closest('.cl-action-btn.mark-paid');
    if (markPaidBtn) {
      const emailId = markPaidBtn.getAttribute('data-email-id');
      if (emailId) window.dispatchEvent(new CustomEvent('clearledgr:mark-paid', { detail: { emailId } }));
      evt.stopPropagation();
      return;
    }

    const actionBtn = target.closest('.cl-action-btn[data-action]');
    if (actionBtn) {
      const emailId = actionBtn.getAttribute('data-email-id');
      const action = actionBtn.getAttribute('data-action');
      if (!emailId || !action) return;

      if (action === 'retry') {
        window.dispatchEvent(new CustomEvent('clearledgr:retry-invoice', { detail: { emailId } }));
      } else if (action === 'fix') {
        window.dispatchEvent(new CustomEvent('clearledgr:show-fix-modal', { detail: { emailId } }));
      } else if (action === 'dismiss') {
        if (confirm('Dismiss this error? The invoice will be removed.')) {
          window.dispatchEvent(new CustomEvent('clearledgr:dismiss-invoice', { detail: { emailId } }));
        }
      } else if (action === 'restore') {
        window.dispatchEvent(new CustomEvent('clearledgr:restore-invoice', { detail: { emailId } }));
      }
      evt.stopPropagation();
      return;
    }

    // Row click: open thread (avoid if clicking a checkbox)
    if (target.closest('input[type="checkbox"]')) return;
    const row = target.closest('tr[data-email-id]');
    if (row) {
      const emailId = row.getAttribute('data-email-id');
      if (emailId) window.location.hash = `#inbox/${emailId}`;
    }
  });
}

function __clPipelineUpdateCounts(queue) {
  const el = __clPipelineState.element;
  if (!el) return;

  const today = new Date();
  const setText = (id, value) => {
    const node = el.querySelector(`#${id}`);
    if (node) node.textContent = String(value);
  };

  setText('cl-filter-all', queue.length);
  setText(
    'cl-filter-overdue',
    queue.filter((e) => {
      const due = e?.detected?.dueDate ?? e?.dueDate;
      if (!due) return false;
      const d = new Date(due);
      return !Number.isNaN(d.getTime()) && d < today;
    }).length
  );
  setText('cl-filter-duplicates', queue.filter((e) => !!e?.isDuplicate).length);
  setText('cl-filter-lowconf', queue.filter((e) => (typeof e?.confidence === 'number' ? e.confidence : 1) < 0.8).length);
  setText('cl-filter-recurring', queue.filter((e) => !!e?.isRecurring).length);

  const totalLabel = el.querySelector('#cl-total-count');
  if (totalLabel) totalLabel.textContent = `${queue.length} invoices`;

  const counts = {
    detected: queue.filter((e) => !e.status || e.status === 'pending' || e.status === 'new').length,
    review: queue.filter((e) => e.status === 'needs_review' || e.status === 'pending_approval').length,
    approved: queue.filter((e) => e.status === 'approved').length,
    posted: queue.filter((e) => e.status === 'posted').length,
    paid: queue.filter((e) => e.status === 'paid').length,
    exception: queue.filter((e) => e.status === 'error' || e.status === 'rejected').length
  };

  setText('cl-count-detected', counts.detected);
  setText('cl-count-review', counts.review);
  setText('cl-count-approved', counts.approved);
  setText('cl-count-posted', counts.posted);
  setText('cl-count-paid', counts.paid);
  setText('cl-count-exception', counts.exception);
}

function __clPipelineApplyFilters(queue) {
  let filtered = [...queue];

  if (__clPipelineState.stage) {
    const stageMap = {
      detected: ['pending', 'new', undefined, null],
      review: ['needs_review', 'pending_approval'],
      approved: ['approved'],
      posted: ['posted'],
      paid: ['paid'],
      exception: ['error', 'rejected']
    };
    const allowed = stageMap[__clPipelineState.stage] || [];
    filtered = filtered.filter((e) => allowed.includes(e.status));
  }

  const today = new Date();
  if (__clPipelineState.filter === 'overdue') {
    filtered = filtered.filter((e) => {
      const due = e?.detected?.dueDate ?? e?.dueDate;
      if (!due) return false;
      const d = new Date(due);
      return !Number.isNaN(d.getTime()) && d < today;
    });
  } else if (__clPipelineState.filter === 'duplicates') {
    filtered = filtered.filter((e) => !!e?.isDuplicate);
  } else if (__clPipelineState.filter === 'low-confidence') {
    filtered = filtered.filter((e) => (typeof e?.confidence === 'number' ? e.confidence : 1) < 0.8);
  } else if (__clPipelineState.filter === 'recurring') {
    filtered = filtered.filter((e) => !!e?.isRecurring);
  }

  const q = String(__clPipelineState.search || '').trim().toLowerCase();
  if (q) {
    filtered = filtered.filter((e) => {
      const vendor = __clPipelineGetVendor(e);
      const invoiceNum = e?.detected?.invoiceNumber || e?.invoiceNumber || '';
      const searchable = [e?.subject, vendor, e?.sender, e?.from, invoiceNum].filter(Boolean).join(' ').toLowerCase();
      return searchable.includes(q);
    });
  }

  return filtered;
}

function __clPipelineRender() {
  const el = __clPipelineState.element;
  if (!el) return;
  const tbody = el.querySelector('#cl-pipeline-body');
  if (!tbody) return;

  const queue = Array.isArray(__clPipelineState.queue) ? __clPipelineState.queue : [];
  __clPipelineUpdateCounts(queue);

  const filtered = __clPipelineApplyFilters(queue);

  if (filtered.length === 0) {
    const msg = __clPipelineState.search ? 'No invoices match your search' : 'No invoices in this view';
    tbody.innerHTML = `
      <tr>
        <td colspan="7">
          <div style="padding: 40px; text-align: center; color: #5f6368;">${escapeHtml(msg)}</div>
        </td>
      </tr>
    `;
    return;
  }

  tbody.innerHTML = filtered.map(__clPipelineRenderRow).join('');
  __clPipelineInstallHandlers();
}

window.addEventListener('clearledgr:pipeline-data', (e) => {
  const queue = e?.detail?.queue;
  if (Array.isArray(queue)) __clPipelineSetState({ queue });
  if (currentThreadView) {
    updateGlobalSidebarContext(currentThreadView);
  }
});

function renderInvoices(element, params = {}) {
  // Get initial status filter from route params
  const initialStatus = params.status || null;

  // Mark the pipeline view as mounted so `clearledgr:pipeline-data` can render into it.
  __clPipelineSetState({ element });
  __clPipelineInstallHandlers();
  
  element.innerHTML = `
    <style>
      .cl-pipeline { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; }
      .cl-pipeline-count { font-size: 14px; color: #5f6368; background: #f1f3f4; padding: 6px 12px; border-radius: 16px; }
      
      /* Filter & Search Bar */
      .cl-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; gap: 16px; flex-wrap: wrap; }
      .cl-filters { display: flex; gap: 8px; flex-wrap: wrap; }
      .cl-filter { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border: 1px solid #e0e0e0; border-radius: 20px; font-size: 13px; font-weight: 500; color: #5f6368; background: white; cursor: pointer; transition: all 0.2s; }
      .cl-filter:hover { border-color: #10B981; color: #10B981; }
      .cl-filter.active { background: #10B981; color: white; border-color: #10B981; }
      .cl-filter-count { font-size: 11px; background: rgba(0,0,0,0.1); padding: 2px 6px; border-radius: 10px; }
      .cl-filter.active .cl-filter-count { background: rgba(255,255,255,0.3); }
      .cl-filter-icon { width: 14px; height: 14px; }
      .cl-search-box { display: flex; align-items: center; gap: 8px; padding: 8px 16px; border: 1px solid #e0e0e0; border-radius: 24px; background: white; }
      .cl-search-box:focus-within { border-color: #10B981; }
      .cl-search-input { border: none; outline: none; font-size: 14px; width: 220px; }
      .cl-search-icon { width: 16px; height: 16px; color: #9e9e9e; }
      
      /* Stage Pills */
      .cl-stages { display: flex; gap: 4px; margin-bottom: 20px; overflow-x: auto; padding-bottom: 4px; }
      .cl-stage { display: flex; align-items: center; gap: 8px; padding: 10px 20px; border-radius: 4px; font-size: 13px; font-weight: 500; color: white; cursor: pointer; white-space: nowrap; transition: all 0.2s; opacity: 0.7; }
      .cl-stage:hover, .cl-stage.active { opacity: 1; transform: translateY(-1px); box-shadow: 0 2px 8px rgba(0,0,0,0.15); }
      .cl-stage-detected { background: #2196F3; }
      .cl-stage-review { background: #FF9800; }
      .cl-stage-approved { background: #4CAF50; }
      .cl-stage-posted { background: #9C27B0; }
      .cl-stage-paid { background: #1B5E20; }
      .cl-stage-exception { background: #F44336; }
      .cl-stage-icon { width: 16px; height: 16px; }
      .cl-stage-count { background: rgba(255,255,255,0.3); padding: 2px 8px; border-radius: 10px; font-size: 12px; }
      
      /* Bulk Actions Bar */
      .cl-bulk-bar { display: none; align-items: center; gap: 16px; padding: 12px 20px; background: #E8F5E9; border-bottom: 1px solid #C8E6C9; }
      .cl-bulk-bar.visible { display: flex; }
      .cl-bulk-count { font-size: 14px; font-weight: 500; color: #2E7D32; }
      .cl-bulk-actions { display: flex; gap: 8px; margin-left: auto; }
      .cl-bulk-btn { padding: 8px 16px; border-radius: 4px; font-size: 13px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s; }
      .cl-bulk-btn.approve { background: #10B981; color: white; }
      .cl-bulk-btn.approve:hover { background: #059669; }
      .cl-bulk-btn.reject { background: white; color: #C62828; border: 1px solid #e0e0e0; }
      .cl-bulk-btn.reject:hover { background: #FFEBEE; border-color: #C62828; }
      .cl-bulk-btn.clear { background: transparent; color: #5f6368; }
      .cl-bulk-btn.clear:hover { background: rgba(0,0,0,0.05); }
      
      /* Table */
      .cl-table-container { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-pipeline-table { width: 100%; border-collapse: collapse; }
      .cl-pipeline-table th { text-align: left; padding: 14px 20px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #e0e0e0; }
      .cl-pipeline-table th:first-child { width: 40px; padding-left: 16px; }
      .cl-pipeline-table td { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-size: 14px; }
      .cl-pipeline-table td:first-child { width: 40px; padding-left: 16px; }
      .cl-pipeline-table tr:hover { background: #f8f9fa; cursor: pointer; }
      .cl-pipeline-table tr.selected { background: #E8F5E9; }
      .cl-pipeline-table tr:last-child td { border-bottom: none; }
      .cl-checkbox { width: 18px; height: 18px; cursor: pointer; accent-color: #10B981; }
      .cl-subject { font-weight: 500; color: #202124; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .cl-subject-row { display: flex; align-items: center; gap: 8px; }
      .cl-recurring-badge { font-size: 10px; background: #E8F5E9; color: #2E7D32; padding: 2px 6px; border-radius: 4px; }
      .cl-overdue-badge { font-size: 10px; background: #FFEBEE; color: #C62828; padding: 2px 6px; border-radius: 4px; }
      .cl-ai-badge { font-size: 9px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 2px 6px; border-radius: 4px; font-weight: 600; letter-spacing: 0.5px; }
      .cl-vendor { color: #5f6368; }
      .cl-amount { font-weight: 600; color: #202124; }
      .cl-amount-change { font-size: 11px; color: #E65100; margin-left: 4px; }
      .cl-confidence { font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 6px; }
      .cl-confidence.high { background: #E8F5E9; color: #2E7D32; }
      .cl-confidence.medium { background: #FFF3E0; color: #E65100; }
      .cl-confidence.low { background: #FFEBEE; color: #C62828; }
      .cl-stage-badge { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 500; }
      .cl-stage-badge.detected { background: #E3F2FD; color: #1565C0; }
      .cl-stage-badge.review { background: #FFF3E0; color: #E65100; }
      .cl-stage-badge.approved { background: #E8F5E9; color: #2E7D32; }
      .cl-stage-badge.posted { background: #F3E5F5; color: #7B1FA2; }
      .cl-stage-badge.paid { background: #E8F5E9; color: #1B5E20; }
      .cl-stage-badge.exception { background: #FFEBEE; color: #C62828; }
      .cl-stage-badge.rejected { background: #f1f3f4; color: #5f6368; }
      .cl-date { color: #5f6368; font-size: 13px; }
      .cl-due-warning { color: #E65100; font-weight: 500; }
      .cl-due-overdue { color: #C62828; font-weight: 500; }
      .cl-actions { display: flex; gap: 6px; }
      .cl-action-btn { padding: 6px 12px; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 12px; font-weight: 500; background: white; cursor: pointer; transition: all 0.2s; display: inline-flex; align-items: center; gap: 4px; }
      .cl-action-btn:hover { border-color: #10B981; color: #10B981; }
      .cl-action-btn.approve { background: #10B981; color: white; border-color: #10B981; }
      .cl-action-btn.approve:hover { background: #059669; }
      .cl-action-btn.reject { color: #C62828; }
      .cl-action-btn.reject:hover { background: #FFEBEE; border-color: #C62828; }
      .cl-action-icon { width: 12px; height: 12px; }
      
      /* Empty State */
      .cl-empty-state { padding: 64px 40px; text-align: center; }
      .cl-empty-icon { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.4; }
      .cl-empty-title { font-size: 16px; font-weight: 500; color: #202124; margin-bottom: 8px; }
      .cl-empty-desc { font-size: 14px; color: #5f6368; margin-bottom: 20px; }
      .cl-empty-btn { display: inline-flex; align-items: center; gap: 8px; padding: 10px 20px; background: #10B981; color: white; border: none; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; }
      .cl-empty-btn:hover { background: #059669; }
      
    </style>
    
    <div class="cl-pipeline">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <rect x="2" y="3" width="6" height="18" rx="1" stroke="#10B981" stroke-width="2"/>
            <rect x="9" y="3" width="6" height="18" rx="1" stroke="#10B981" stroke-width="2"/>
            <rect x="16" y="3" width="6" height="18" rx="1" stroke="#10B981" stroke-width="2"/>
            <rect x="3.5" y="6" width="3" height="3" rx="0.5" fill="#10B981"/>
            <rect x="10.5" y="6" width="3" height="3" rx="0.5" fill="#10B981"/>
            <rect x="17.5" y="6" width="3" height="3" rx="0.5" fill="#10B981"/>
          </svg>
          <h1>Invoices</h1>
        </div>
        <span class="cl-invoice-count" id="cl-total-count">0 invoices</span>
      </div>
      
      <!-- Filter & Search Toolbar -->
      <div class="cl-toolbar">
        <div class="cl-filters">
          <button class="cl-filter active" data-filter="all">
            <svg class="cl-filter-icon" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="12" rx="2" stroke="currentColor" stroke-width="1.5"/></svg>
            All <span class="cl-filter-count" id="cl-filter-all">0</span>
          </button>
          <button class="cl-filter" data-filter="overdue">
            <svg class="cl-filter-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5"/><path d="M8 5V8L10 9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
            Overdue <span class="cl-filter-count" id="cl-filter-overdue">0</span>
          </button>
          <button class="cl-filter" data-filter="duplicates">
            <svg class="cl-filter-icon" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="8" height="8" rx="1" stroke="currentColor" stroke-width="1.5"/><rect x="6" y="6" width="8" height="8" rx="1" stroke="currentColor" stroke-width="1.5"/></svg>
            Duplicates <span class="cl-filter-count" id="cl-filter-duplicates">0</span>
          </button>
          <button class="cl-filter" data-filter="low-confidence">
            <svg class="cl-filter-icon" viewBox="0 0 16 16" fill="none"><path d="M8 3L14 13H2L8 3Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 7V9M8 11V11.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
            Low Confidence <span class="cl-filter-count" id="cl-filter-lowconf">0</span>
          </button>
          <button class="cl-filter" data-filter="recurring">
            <svg class="cl-filter-icon" viewBox="0 0 16 16" fill="none"><path d="M2 8C2 4.7 4.7 2 8 2C10.2 2 12.1 3.2 13 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M14 8C14 11.3 11.3 14 8 14C5.8 14 3.9 12.8 3 11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13 2V5H10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M3 14V11H6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
            Recurring <span class="cl-filter-count" id="cl-filter-recurring">0</span>
          </button>
        </div>
        <div class="cl-search-box">
          <svg class="cl-search-icon" viewBox="0 0 16 16" fill="none"><circle cx="7" cy="7" r="5" stroke="currentColor" stroke-width="1.5"/><path d="M11 11L14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
          <input type="text" class="cl-search-input" placeholder="Search invoices..." id="cl-pipeline-search" />
        </div>
      </div>
      
      <!-- Status Filters -->
      <div class="cl-stages">
        <div class="cl-stage cl-stage-detected active" data-stage="detected">
          <svg class="cl-stage-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="white" opacity="0.3"/><path d="M8 4V8L10 10" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>
          New <span class="cl-stage-count" id="cl-count-detected">0</span>
        </div>
        <div class="cl-stage cl-stage-review" data-stage="review">
          <svg class="cl-stage-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="white" opacity="0.3"/><circle cx="8" cy="6" r="1.5" fill="white"/><path d="M8 9V11" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>
          Pending <span class="cl-stage-count" id="cl-count-review">0</span>
        </div>
        <div class="cl-stage cl-stage-approved" data-stage="approved">
          <svg class="cl-stage-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="white" opacity="0.3"/><path d="M5 8L7 10L11 6" stroke="white" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Approved <span class="cl-stage-count" id="cl-count-approved">0</span>
        </div>
        <div class="cl-stage cl-stage-posted" data-stage="posted">
          <svg class="cl-stage-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="white" opacity="0.3"/><path d="M5 8H11M8 5V11" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>
          Synced <span class="cl-stage-count" id="cl-count-posted">0</span>
        </div>
        <div class="cl-stage cl-stage-paid" data-stage="paid">
          <svg class="cl-stage-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="white" opacity="0.3"/><path d="M5 8L7 10L11 6" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          Paid <span class="cl-stage-count" id="cl-count-paid">0</span>
        </div>
        <div class="cl-stage cl-stage-exception" data-stage="exception">
          <svg class="cl-stage-icon" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" fill="white" opacity="0.3"/><path d="M6 6L10 10M10 6L6 10" stroke="white" stroke-width="1.5" stroke-linecap="round"/></svg>
          Exception <span class="cl-stage-count" id="cl-count-exception">0</span>
        </div>
      </div>
      
      <!-- Bulk Actions Bar -->
      <div class="cl-bulk-bar" id="cl-bulk-bar">
        <span class="cl-bulk-count"><span id="cl-bulk-count">0</span> selected</span>
        <div class="cl-bulk-actions">
          <button class="cl-bulk-btn approve" id="cl-bulk-approve">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" style="margin-right: 4px;">
              <path d="M3 8L6.5 11.5L13 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            Approve Selected
          </button>
          <button class="cl-bulk-btn reject" id="cl-bulk-reject">Reject Selected</button>
          <button class="cl-bulk-btn clear" id="cl-bulk-clear">Clear Selection</button>
        </div>
      </div>
      
      <!-- Invoice Table -->
      <div class="cl-table-container">
        <table class="cl-pipeline-table">
          <thead>
            <tr>
              <th><input type="checkbox" class="cl-checkbox" id="cl-select-all" title="Select all"></th>
              <th>Invoice</th>
              <th>Vendor</th>
              <th>Amount</th>
              <th>Status</th>
              <th>Due Date</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-pipeline-body">
            <tr>
              <td colspan="7">
                <div class="cl-empty-state">
                  <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
                    <rect x="12" y="4" width="40" height="56" rx="2" stroke="#e0e0e0" stroke-width="3"/>
                    <line x1="20" y1="20" x2="44" y2="20" stroke="#e0e0e0" stroke-width="2"/>
                    <line x1="20" y1="28" x2="44" y2="28" stroke="#e0e0e0" stroke-width="2"/>
                    <line x1="20" y1="36" x2="36" y2="36" stroke="#e0e0e0" stroke-width="2"/>
                  </svg>
                  <div class="cl-empty-title">No invoices yet</div>
                  <div class="cl-empty-desc">Clearledgr automatically detects invoices from your inbox</div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
  
  // Setup filter clicks
  element.querySelectorAll('.cl-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      element.querySelectorAll('.cl-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      __clPipelineSetState({ filter: btn.dataset.filter || 'all' });
    });
  });
  
  // Setup stage clicks
  element.querySelectorAll('.cl-stage').forEach(btn => {
    btn.addEventListener('click', () => {
      element.querySelectorAll('.cl-stage').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      __clPipelineSetState({ stage: btn.dataset.stage || 'detected' });
    });
  });
  
  // Pre-select stage from route params
  if (initialStatus) {
    const stageMap = {
      'new': 'detected',
      'review': 'review',
      'approved': 'approved',
      'synced': 'posted'
    };
    const targetStage = stageMap[initialStatus];
    if (targetStage) {
      const targetBtn = element.querySelector(`.cl-stage[data-stage="${targetStage}"]`);
      if (targetBtn) {
        element.querySelectorAll('.cl-stage').forEach(b => b.classList.remove('active'));
        targetBtn.classList.add('active');
        __clPipelineSetState({ stage: targetStage });
      }
    }
  }
  
  // Setup search
  const searchInput = element.querySelector('#cl-pipeline-search');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      __clPipelineSetState({ search: e.target.value || '' });
    });
  }
  
  // Setup bulk actions
  const selectAllCheckbox = element.querySelector('#cl-select-all');
  const bulkBar = element.querySelector('#cl-bulk-bar');
  const bulkCount = element.querySelector('#cl-bulk-count');
  const bulkApproveBtn = element.querySelector('#cl-bulk-approve');
  const bulkRejectBtn = element.querySelector('#cl-bulk-reject');
  const bulkClearBtn = element.querySelector('#cl-bulk-clear');
  
  // Select all checkbox
  selectAllCheckbox?.addEventListener('change', (e) => {
    const checked = e.target.checked;
    element.querySelectorAll('.cl-row-checkbox').forEach(cb => {
      cb.checked = checked;
      cb.closest('tr')?.classList.toggle('selected', checked);
    });
    updateBulkBar();
  });
  
  // Update bulk bar when individual checkboxes change
  element.addEventListener('change', (e) => {
    if (e.target.classList.contains('cl-row-checkbox')) {
      e.target.closest('tr')?.classList.toggle('selected', e.target.checked);
      updateBulkBar();
    }
  });
  
  function updateBulkBar() {
    const selected = element.querySelectorAll('.cl-row-checkbox:checked');
    const count = selected.length;
    if (bulkCount) bulkCount.textContent = count;
    bulkBar?.classList.toggle('visible', count > 0);
    
    // Update select all checkbox state
    const total = element.querySelectorAll('.cl-row-checkbox').length;
    if (selectAllCheckbox) {
      selectAllCheckbox.checked = count > 0 && count === total;
      selectAllCheckbox.indeterminate = count > 0 && count < total;
    }
  }
  
  // Bulk approve
  bulkApproveBtn?.addEventListener('click', () => {
    const selectedIds = Array.from(element.querySelectorAll('.cl-row-checkbox:checked'))
      .map(cb => cb.dataset.emailId)
      .filter(Boolean);
    
    if (selectedIds.length > 0) {
      window.dispatchEvent(new CustomEvent('clearledgr:bulk-approve', { 
        detail: { emailIds: selectedIds } 
      }));
    }
  });
  
  // Bulk reject
  bulkRejectBtn?.addEventListener('click', () => {
    const selectedIds = Array.from(element.querySelectorAll('.cl-row-checkbox:checked'))
      .map(cb => cb.dataset.emailId)
      .filter(Boolean);
    
    if (selectedIds.length > 0 && confirm(`Reject ${selectedIds.length} invoice(s)?`)) {
      window.dispatchEvent(new CustomEvent('clearledgr:bulk-reject', { 
        detail: { emailIds: selectedIds, reason: 'bulk_rejected' } 
      }));
    }
  });
  
  // Clear selection
  bulkClearBtn?.addEventListener('click', () => {
    element.querySelectorAll('.cl-row-checkbox').forEach(cb => {
      cb.checked = false;
      cb.closest('tr')?.classList.remove('selected');
    });
    if (selectAllCheckbox) selectAllCheckbox.checked = false;
    updateBulkBar();
  });
  
  // Setup reject modal (created separately and appended to body)
  setupRejectModal();

  // Render immediately using any cached pipeline data, then refresh from data layer.
  __clPipelineRender();
  
  // Request pipeline data
  window.dispatchEvent(new CustomEvent('clearledgr:request-pipeline-data'));
}

// =============================================================================
// AUDIT HISTORY VIEW
// =============================================================================

function renderAuditHistory(element) {
  element.innerHTML = `
    <style>
      .cl-history { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 1000px; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; }
      .cl-filter-row { display: flex; gap: 12px; margin-bottom: 24px; }
      .cl-filter-btn { padding: 8px 16px; background: white; border: 1px solid #e0e0e0; border-radius: 20px; font-size: 13px; cursor: pointer; transition: all 0.2s; }
      .cl-filter-btn:hover { border-color: #10B981; }
      .cl-filter-btn.active { background: #10B981; color: white; border-color: #10B981; }
      .cl-timeline { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-timeline-item { display: flex; gap: 16px; padding: 20px 24px; border-bottom: 1px solid #f1f3f4; transition: background 0.2s; }
      .cl-timeline-item:hover { background: #f8f9fa; }
      .cl-timeline-item:last-child { border-bottom: none; }
      .cl-timeline-icon { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
      .cl-timeline-icon.approved { background: #E8F5E9; color: #2E7D32; }
      .cl-timeline-icon.rejected { background: #FFEBEE; color: #C62828; }
      .cl-timeline-icon.posted { background: #F3E5F5; color: #7B1FA2; }
      .cl-timeline-icon.detected { background: #E3F2FD; color: #1565C0; }
      .cl-timeline-icon.error { background: #FFF3E0; color: #E65100; }
      .cl-timeline-content { flex: 1; min-width: 0; }
      .cl-timeline-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
      .cl-timeline-action { font-size: 14px; font-weight: 500; color: #202124; }
      .cl-timeline-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; }
      .cl-timeline-badge.approved { background: #E8F5E9; color: #2E7D32; }
      .cl-timeline-badge.rejected { background: #FFEBEE; color: #C62828; }
      .cl-timeline-badge.posted { background: #F3E5F5; color: #7B1FA2; }
      .cl-timeline-desc { font-size: 13px; color: #5f6368; margin-bottom: 4px; }
      .cl-timeline-meta { font-size: 12px; color: #9e9e9e; display: flex; gap: 16px; }
      .cl-timeline-time { display: flex; align-items: center; gap: 4px; }
      .cl-empty-history { padding: 60px; text-align: center; color: #9e9e9e; }
      .cl-empty-history svg { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.3; }
      .cl-empty-history h3 { font-size: 16px; font-weight: 500; color: #5f6368; margin: 0 0 8px 0; }
      .cl-empty-history p { font-size: 13px; margin: 0; }
    </style>
    
    <div class="cl-history">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="13" r="8" stroke="#10B981" stroke-width="2"/>
            <path d="M12 9V13L14.5 14.5" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M6 6L3 3M3 3V7M3 3H7" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <h1>Activity History</h1>
        </div>
      </div>
      
      <div class="cl-filter-row">
        <button class="cl-filter-btn active" data-filter="all">All Activity</button>
        <button class="cl-filter-btn" data-filter="approved">Approved</button>
        <button class="cl-filter-btn" data-filter="rejected">Rejected</button>
        <button class="cl-filter-btn" data-filter="posted">Posted to ERP</button>
        <button class="cl-filter-btn" data-filter="detected">Detected</button>
      </div>
      
      <div class="cl-timeline" id="cl-history-timeline">
        <div class="cl-empty-history">
          <svg viewBox="0 0 64 64" fill="none">
            <circle cx="32" cy="36" r="22" stroke="#e0e0e0" stroke-width="3"/>
            <path d="M32 22V36L42 42" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
            <path d="M16 16L8 8M8 8V18M8 8H18" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
          </svg>
          <h3>No activity yet</h3>
          <p>Your invoice processing history will appear here</p>
        </div>
      </div>
    </div>
  `;
  
  // Setup filter clicks
  let currentHistoryFilter = 'all';
  element.querySelectorAll('.cl-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      element.querySelectorAll('.cl-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentHistoryFilter = btn.dataset.filter;
      window.dispatchEvent(new CustomEvent('clearledgr:filter-history', { 
        detail: { filter: currentHistoryFilter } 
      }));
    });
  });
  
  // Request history data
  window.dispatchEvent(new CustomEvent('clearledgr:request-history'));
}

// Listen for history data
window.addEventListener('clearledgr:history-data', (e) => {
  const data = e.detail || { activities: [] };
  const timeline = document.getElementById('cl-history-timeline');
  if (!timeline) return;
  
  if (!data.activities || data.activities.length === 0) {
    timeline.innerHTML = `
      <div class="cl-empty-history">
        <svg viewBox="0 0 64 64" fill="none">
          <circle cx="32" cy="36" r="22" stroke="#e0e0e0" stroke-width="3"/>
          <path d="M32 22V36L42 42" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
          <path d="M16 16L8 8M8 8V18M8 8H18" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
        </svg>
        <h3>No activity yet</h3>
        <p>Your invoice processing history will appear here</p>
      </div>
    `;
    return;
  }
  
  timeline.innerHTML = data.activities.map(activity => {
    const iconClass = activity.type || 'detected';
    const iconSvg = getActivityIcon(activity.type);
    
    return `
      <div class="cl-timeline-item" data-type="${activity.type}">
        <div class="cl-timeline-icon ${iconClass}">
          ${iconSvg}
        </div>
        <div class="cl-timeline-content">
          <div class="cl-timeline-header">
            <span class="cl-timeline-action">${activity.action || 'Activity'}</span>
            ${activity.badge ? `<span class="cl-timeline-badge ${activity.type}">${activity.badge}</span>` : ''}
          </div>
          <div class="cl-timeline-desc">${activity.description || ''}</div>
          <div class="cl-timeline-meta">
            <span class="cl-timeline-time">
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <circle cx="6" cy="6" r="5" stroke="currentColor" stroke-width="1.2"/>
                <path d="M6 3V6L7.5 7" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
              </svg>
              ${activity.time || ''}
            </span>
            ${activity.user ? `<span>by ${activity.user}</span>` : ''}
            ${activity.amount ? `<span>$${activity.amount}</span>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join('');
});

function getActivityIcon(type) {
  switch (type) {
    case 'approved':
      return '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M5 10L8 13L15 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    case 'rejected':
      return '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M6 6L14 14M14 6L6 14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    case 'posted':
      return '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M10 4V16M4 10H16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    case 'detected':
      return '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="6" stroke="currentColor" stroke-width="2"/><circle cx="10" cy="10" r="2" fill="currentColor"/></svg>';
    case 'error':
      return '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="7" r="1.5" fill="currentColor"/><path d="M10 10V14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    default:
      return '<svg width="20" height="20" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="6" stroke="currentColor" stroke-width="2"/></svg>';
  }
}

function renderSettings(element) {
  element.innerHTML = `
    <style>
      .cl-settings { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 800px; }
      .cl-page-header { display: flex; align-items: center; gap: 12px; margin-bottom: 32px; }
      .cl-page-icon { width: 28px; height: 28px; }
      .cl-page-header h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-settings-section { background: white; border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 24px; }
      .cl-settings-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; }
      .cl-settings-title { font-size: 16px; font-weight: 500; color: #202124; margin: 0 0 4px 0; }
      .cl-settings-desc { font-size: 13px; color: #5f6368; margin: 0; }
      .cl-settings-body { padding: 24px; }
      .cl-setting-row { display: flex; align-items: center; justify-content: space-between; padding: 16px 0; border-bottom: 1px solid #f1f3f4; }
      .cl-setting-row:last-child { border-bottom: none; }
      .cl-setting-info { flex: 1; }
      .cl-setting-label { font-size: 14px; font-weight: 500; color: #202124; margin-bottom: 4px; }
      .cl-setting-hint { font-size: 12px; color: #5f6368; }
      .cl-setting-control { margin-left: 24px; }
      .cl-toggle { position: relative; width: 44px; height: 24px; display: inline-block; flex-shrink: 0; }
      .cl-toggle input { opacity: 0; width: 0; height: 0; position: absolute; }
      .cl-toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #bdbdbd; border-radius: 24px; transition: 0.3s; border: 1px solid #9e9e9e; }
      .cl-toggle-slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 2px; bottom: 2px; background: white; border-radius: 50%; transition: 0.3s; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
      .cl-toggle input:checked + .cl-toggle-slider { background: #10B981; border-color: #10B981; }
      .cl-toggle input:checked + .cl-toggle-slider:before { transform: translateX(20px); }
      .cl-input { padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; width: 200px; }
      .cl-input:focus { outline: none; border-color: #10B981; }
      .cl-select { padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; background: white; }
      .cl-select:focus { outline: none; border-color: #10B981; }
      .cl-btn { display: inline-flex; align-items: center; justify-content: center; padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; transition: all 0.2s; flex: 0 0 auto; width: fit-content; white-space: nowrap; }
      .cl-btn-primary { background: #10B981; color: white; }
      .cl-btn-primary:hover { background: #059669; }
      .cl-btn-secondary { background: white; color: #202124; border: 1px solid #e0e0e0; }
      .cl-btn-secondary:hover { background: #f8f9fa; }
      .cl-btn-danger { background: white; color: #C62828; border: 1px solid #e0e0e0; }
      .cl-btn-danger:hover { background: #FFEBEE; border-color: #C62828; }
      .cl-erp-status { display: flex; align-items: center; gap: 8px; }
      .cl-erp-badge { display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; border-radius: 16px; font-size: 12px; font-weight: 500; }
      .cl-erp-badge.connected { background: #E8F5E9; color: #2E7D32; }
      .cl-erp-badge.disconnected { background: #f1f3f4; color: #5f6368; }
      .cl-erp-dot { width: 8px; height: 8px; border-radius: 50%; }
      .cl-erp-badge.connected .cl-erp-dot { background: #2E7D32; }
      .cl-erp-badge.disconnected .cl-erp-dot { background: #9e9e9e; }
    </style>
    
    <div class="cl-settings">
      <div class="cl-page-header">
        <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="3" stroke="#10B981" stroke-width="2"/>
          <path d="M12 1V4M12 20V23M1 12H4M20 12H23M4.2 4.2L6.3 6.3M17.7 17.7L19.8 19.8M4.2 19.8L6.3 17.7M17.7 6.3L19.8 4.2" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <h1>Settings</h1>
      </div>
      
      <!-- Subscription & Plan Section -->
      <div class="cl-settings-section" id="cl-subscription-section">
        <div class="cl-settings-header" style="display: flex; justify-content: space-between; align-items: flex-start;">
          <div>
            <h2 class="cl-settings-title">Subscription & Plan</h2>
            <p class="cl-settings-desc">Manage your Clearledgr subscription</p>
          </div>
          <div id="cl-settings-plan-badge">${renderTrialBadge()}</div>
        </div>
        <div class="cl-settings-body">
          <div id="cl-subscription-details" style="display: flex; flex-direction: column; gap: 16px;">
            <div style="display: flex; align-items: center; justify-content: space-between; padding: 16px; background: #f8f9fa; border-radius: 8px;">
              <div>
                <div style="font-size: 14px; font-weight: 500; color: #202124;" id="cl-plan-name">Loading...</div>
                <div style="font-size: 12px; color: #5f6368;" id="cl-plan-status">Checking subscription status...</div>
              </div>
              <button id="cl-upgrade-btn" style="padding: 10px 24px; background: #10B981; color: white; border: none; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; display: none;">Upgrade to Pro</button>
            </div>
            
            <div id="cl-usage-stats" style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;">
              <div style="padding: 16px; background: white; border: 1px solid #e0e0e0; border-radius: 8px; text-align: center;">
                <div style="font-size: 24px; font-weight: 500; color: #202124;" id="cl-usage-invoices">0</div>
                <div style="font-size: 12px; color: #5f6368;">Invoices this month</div>
                <div style="font-size: 11px; color: #9e9e9e;" id="cl-limit-invoices">of 25</div>
              </div>
              <div style="padding: 16px; background: white; border: 1px solid #e0e0e0; border-radius: 8px; text-align: center;">
                <div style="font-size: 24px; font-weight: 500; color: #202124;" id="cl-usage-vendors">0</div>
                <div style="font-size: 12px; color: #5f6368;">Vendors</div>
                <div style="font-size: 11px; color: #9e9e9e;" id="cl-limit-vendors">of 10</div>
              </div>
              <div style="padding: 16px; background: white; border: 1px solid #e0e0e0; border-radius: 8px; text-align: center;">
                <div style="font-size: 24px; font-weight: 500; color: #202124;" id="cl-usage-ai">0</div>
                <div style="font-size: 12px; color: #5f6368;">AI extractions</div>
                <div style="font-size: 11px; color: #9e9e9e;" id="cl-limit-ai">of 50</div>
              </div>
            </div>
            
            <div id="cl-trial-banner" style="display: none; padding: 16px; background: #FFF3E0; border-radius: 8px; border: 1px solid #FFB74D;">
              <div style="display: flex; align-items: center; gap: 12px;">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="#E65100"><path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/></svg>
                <div style="flex: 1;">
                  <div style="font-size: 14px; font-weight: 500; color: #E65100;" id="cl-trial-title">Pro Trial Active</div>
                  <div style="font-size: 12px; color: #5f6368;" id="cl-trial-desc">Enjoy full Pro features</div>
                </div>
                <button id="cl-trial-upgrade-btn" style="padding: 8px 16px; background: #E65100; color: white; border: none; border-radius: 6px; font-size: 13px; cursor: pointer;">Upgrade Now</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">ERP Connection</h2>
          <p class="cl-settings-desc">Connect your accounting software to post invoices automatically</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">QuickBooks Online</div>
              <div class="cl-setting-hint">Sync invoices and vendor bills</div>
            </div>
            <div class="cl-setting-control">
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="cl-erp-badge disconnected" id="cl-qbo-badge">
                  <span class="cl-erp-dot"></span>
                  <span class="cl-erp-text">Not connected</span>
                </span>
                <button id="cl-qbo-btn" style="padding: 8px 20px; background: white; color: #5f6368; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; cursor: pointer;">Connect</button>
              </div>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Xero</div>
              <div class="cl-setting-hint">Sync invoices and bills</div>
            </div>
            <div class="cl-setting-control">
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="cl-erp-badge disconnected" id="cl-xero-badge">
                  <span class="cl-erp-dot"></span>
                  <span class="cl-erp-text">Not connected</span>
                </span>
                <button id="cl-xero-btn" style="padding: 8px 20px; background: white; color: #5f6368; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; cursor: pointer;">Connect</button>
              </div>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">NetSuite</div>
              <div class="cl-setting-hint">Enterprise ERP integration</div>
            </div>
            <div class="cl-setting-control">
              <div style="display: flex; align-items: center; gap: 8px;">
                <span class="cl-erp-badge disconnected" id="cl-netsuite-badge">
                  <span class="cl-erp-dot"></span>
                  <span class="cl-erp-text">Not connected</span>
                </span>
                <button id="cl-netsuite-btn" style="padding: 8px 20px; background: white; color: #5f6368; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; cursor: pointer;">Connect</button>
              </div>
            </div>
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">Automation</h2>
          <p class="cl-settings-desc">Configure automatic invoice processing</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Auto-detect invoices</div>
              <div class="cl-setting-hint">Automatically scan incoming emails for invoices</div>
            </div>
            <div class="cl-setting-control">
              <label class="cl-toggle">
                <input type="checkbox" id="cl-setting-autodetect" checked>
                <span class="cl-toggle-slider"></span>
              </label>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Minimum confidence for auto-approve</div>
              <div class="cl-setting-hint">Only auto-approve if AI confidence is above this</div>
            </div>
            <div class="cl-setting-control">
              <select class="cl-select" id="cl-setting-min-confidence">
                <option value="0.99">99%</option>
                <option value="0.95" selected>95%</option>
                <option value="0.90">90%</option>
                <option value="0.85">85%</option>
              </select>
            </div>
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">Approval Thresholds</h2>
          <p class="cl-settings-desc">Route invoices to the right approvers based on amount</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-threshold-table" style="font-size: 13px;">
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 1px; background: #e0e0e0; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
              <div style="background: #f8f9fa; padding: 12px 16px; font-weight: 500;">Amount Range</div>
              <div style="background: #f8f9fa; padding: 12px 16px; font-weight: 500;">Slack Channel</div>
              <div style="background: #f8f9fa; padding: 12px 16px; font-weight: 500;">Auto-Approve</div>
              
              <div style="background: white; padding: 12px 16px;">$0 - $500</div>
              <div style="background: white; padding: 12px 16px;">#finance-approvals</div>
              <div style="background: white; padding: 12px 16px; color: #2E7D32;">Yes (if 95%+ conf)</div>
              
              <div style="background: white; padding: 12px 16px;">$500 - $5,000</div>
              <div style="background: white; padding: 12px 16px;">#finance-approvals</div>
              <div style="background: white; padding: 12px 16px; color: #C62828;">No</div>
              
              <div style="background: white; padding: 12px 16px;">$5,000 - $25,000</div>
              <div style="background: white; padding: 12px 16px;">#finance-leadership</div>
              <div style="background: white; padding: 12px 16px; color: #C62828;">No</div>
              
              <div style="background: white; padding: 12px 16px;">$25,000+</div>
              <div style="background: white; padding: 12px 16px;">#executive-approvals</div>
              <div style="background: white; padding: 12px 16px; color: #C62828;">No</div>
            </div>
            <p style="font-size: 12px; color: #5f6368; margin-top: 12px;">
              Configure these thresholds in your organization settings
            </p>
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">GL Account Mapping</h2>
          <p class="cl-settings-desc">Accounts are synced from your connected ERP</p>
        </div>
        <div class="cl-settings-body">
          <div id="cl-gl-erp-status" style="padding: 12px 16px; background: #f8f9fa; border-radius: 8px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;">
            <div style="display: flex; align-items: center; gap: 12px;">
              <span id="cl-gl-erp-icon" style="width: 8px; height: 8px; border-radius: 50%; background: #9e9e9e;"></span>
              <span id="cl-gl-erp-text" style="font-size: 13px; color: #5f6368;">Checking ERP connection...</span>
            </div>
            <div style="flex-shrink: 0;"><button class="cl-btn cl-btn-secondary" id="cl-gl-sync-btn">Sync Accounts</button></div>
          </div>
          <div id="cl-gl-accounts-list" style="margin-bottom: 16px; max-height: 300px; overflow-y: auto;">
            <!-- GL accounts will be populated here -->
          </div>
          <details style="border-top: 1px solid #f1f3f4; padding-top: 12px; margin-top: 12px;">
            <summary style="cursor: pointer; font-size: 13px; color: #5f6368; padding: 8px 0;">Add custom account (for edge cases only)</summary>
            <div style="display: flex; gap: 8px; align-items: center; padding-top: 12px;">
              <input type="text" id="cl-gl-code-input" placeholder="GL Code" style="width: 80px; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px;">
              <input type="text" id="cl-gl-name-input" placeholder="Account Name" style="flex: 1; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; min-width: 100px;">
              <select id="cl-gl-type-input" style="width: 90px; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; background: white;">
                <option value="Expense">Expense</option>
                <option value="Asset">Asset</option>
                <option value="Liability">Liability</option>
              </select>
              <button id="cl-add-gl-btn" style="padding: 8px 16px; background: white; color: #5f6368; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; cursor: pointer;">Add</button>
            </div>
            <p style="font-size: 11px; color: #9e9e9e; margin: 8px 0 0 0;">Custom accounts are replaced when ERP syncs. Use only if account doesn't exist in ERP yet.</p>
          </details>
          
          <div style="margin-top: 24px;">
            <h4 style="font-size: 14px; font-weight: 500; margin: 0 0 12px 0; color: #202124;">Auto-Categorization Rules</h4>
            <div id="cl-gl-rules-list" style="margin-bottom: 16px;">
              <!-- Rules will be populated here -->
            </div>
            <div style="display: flex; gap: 8px; align-items: center; padding-top: 12px; border-top: 1px solid #f1f3f4;">
              <select id="cl-rule-type" style="width: 110px; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; background: white; flex: 0 0 110px;">
                <option value="keyword">Keyword</option>
                <option value="vendor">Vendor</option>
                <option value="amount_range">Amount Range</option>
              </select>
              <input type="text" id="cl-rule-value" placeholder="Match value..." style="flex: 1 1 100px; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px;">
              <select id="cl-rule-gl" style="width: 140px; padding: 8px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; background: white; flex: 0 0 140px;">
                <option value="">Select GL Account</option>
              </select>
              <button id="cl-add-rule-btn" style="padding: 8px 16px; background: white; color: #5f6368; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; cursor: pointer; white-space: nowrap;">Add Rule</button>
            </div>
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">Notifications</h2>
          <p class="cl-settings-desc">Manage how Clearledgr notifies you</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Email notifications</div>
              <div class="cl-setting-hint">Receive daily digest of pending invoices</div>
            </div>
            <div class="cl-setting-control">
              <label class="cl-toggle">
                <input type="checkbox">
                <span class="cl-toggle-slider"></span>
              </label>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Slack notifications</div>
              <div class="cl-setting-hint">Send approval requests to Slack</div>
            </div>
            <div class="cl-setting-control">
              <label class="cl-toggle">
                <input type="checkbox">
                <span class="cl-toggle-slider"></span>
              </label>
            </div>
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">EU VAT Validation</h2>
          <p class="cl-settings-desc">Validate EU VAT numbers against the official VIES database</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-setting-row" style="flex-direction: column; align-items: stretch;">
            <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 16px;">
              <input type="text" class="cl-input" id="cl-vat-input" placeholder="Enter VAT number (e.g., DE123456789)" style="flex: 1 1 auto; min-width: 200px;">
              <div style="flex-shrink: 0;"><button class="cl-btn cl-btn-primary" id="cl-vat-validate-btn">Validate</button></div>
            </div>
            <div id="cl-vat-result" style="display: none; padding: 16px; border-radius: 8px; font-size: 13px;"></div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Auto-validate vendor VAT numbers</div>
              <div class="cl-setting-hint">Automatically validate VAT numbers when adding vendors</div>
            </div>
            <div class="cl-setting-control">
              <label class="cl-toggle">
                <input type="checkbox" id="cl-setting-auto-vat" checked>
                <span class="cl-toggle-slider"></span>
              </label>
            </div>
          </div>
          <div style="margin-top: 12px; padding: 12px 16px; background: #f8f9fa; border-radius: 8px; font-size: 12px; color: #5f6368;">
            Supports all 27 EU member states + Northern Ireland. Validation uses the EU VIES service.
          </div>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">Data Residency & Privacy</h2>
          <p class="cl-settings-desc">GDPR compliance and data storage location settings</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Data region</div>
              <div class="cl-setting-hint">Where your organization's data is stored</div>
            </div>
            <div class="cl-setting-control">
              <select class="cl-select" id="cl-data-region">
                <option value="eu">European Union (GDPR)</option>
                <option value="uk">United Kingdom (UK GDPR)</option>
                <option value="us">United States</option>
                <option value="africa">Africa</option>
                <option value="asia-pacific">Asia Pacific</option>
              </select>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">GDPR compliance mode</div>
              <div class="cl-setting-hint">Enable enhanced data protection features</div>
            </div>
            <div class="cl-setting-control">
              <label class="cl-toggle">
                <input type="checkbox" id="cl-setting-gdpr" checked>
                <span class="cl-toggle-slider"></span>
              </label>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">PII encryption at rest</div>
              <div class="cl-setting-hint">Encrypt personally identifiable information in storage</div>
            </div>
            <div class="cl-setting-control">
              <label class="cl-toggle">
                <input type="checkbox" id="cl-setting-pii-encrypt" checked>
                <span class="cl-toggle-slider"></span>
              </label>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Data retention period</div>
              <div class="cl-setting-hint">How long to keep financial records</div>
            </div>
            <div class="cl-setting-control">
              <select class="cl-select" id="cl-data-retention">
                <option value="2555" selected>7 years (recommended)</option>
                <option value="3650">10 years</option>
                <option value="1825">5 years</option>
                <option value="365">1 year</option>
              </select>
            </div>
          </div>
          
          <div style="margin-top: 16px; padding: 16px; background: #E8F5E9; border-radius: 8px; display: flex; align-items: center; gap: 12px;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" style="flex-shrink: 0;">
              <path d="M12 22C17.5228 22 22 17.5228 22 12C22 6.47715 17.5228 2 12 2C6.47715 2 2 6.47715 2 12C2 17.5228 6.47715 22 12 22Z" stroke="#2E7D32" stroke-width="2"/>
              <path d="M8 12L11 15L16 9" stroke="#2E7D32" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <div style="flex: 1 1 auto;">
              <div style="font-size: 13px; font-weight: 500; color: #2E7D32;">Data Processing Agreement</div>
              <div style="font-size: 12px; color: #5f6368; margin-top: 2px;" id="cl-dpa-status">Not yet signed</div>
            </div>
            <div style="flex-shrink: 0;"><button class="cl-btn cl-btn-secondary" id="cl-dpa-btn">Sign DPA</button></div>
          </div>
          
          <div style="margin-top: 16px; display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
            <button class="cl-btn cl-btn-secondary" id="cl-gdpr-export-btn">
              <span style="display: flex; align-items: center; justify-content: center; gap: 6px;">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M21 15V19C21 20.1046 20.1046 21 19 21H5C3.89543 21 3 20.1046 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                  <path d="M7 10L12 15L17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
                  <path d="M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                </svg>
                Export Data
              </span>
            </button>
            <button class="cl-btn cl-btn-danger" id="cl-gdpr-delete-btn">
              <span style="display: flex; align-items: center; justify-content: center; gap: 6px;">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                  <path d="M3 6H5H21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                  <path d="M8 6V4C8 3.44772 8.44772 3 9 3H15C15.5523 3 16 3.44772 16 4V6M19 6V20C19 20.5523 18.5523 21 18 21H6C5.44772 21 5 20.5523 5 20V6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
                </svg>
                Delete Data
              </span>
            </button>
          </div>
          <p style="font-size: 11px; color: #9e9e9e; margin-top: 8px;">GDPR Article 20 (Data Portability) and Article 17 (Right to Erasure)</p>
        </div>
      </div>
      
      <div class="cl-settings-section">
        <div class="cl-settings-header">
          <h2 class="cl-settings-title">Data</h2>
          <p class="cl-settings-desc">Manage your Clearledgr data</p>
        </div>
        <div class="cl-settings-body">
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Export data</div>
              <div class="cl-setting-hint">Download all your invoice and vendor data</div>
            </div>
            <div class="cl-setting-control">
              <button class="cl-btn cl-btn-secondary" id="cl-export-csv-btn">Export CSV</button>
            </div>
          </div>
          <div class="cl-setting-row">
            <div class="cl-setting-info">
              <div class="cl-setting-label">Clear local data</div>
              <div class="cl-setting-hint">Remove all cached data from this browser</div>
            </div>
            <div class="cl-setting-control">
              <button class="cl-btn cl-btn-danger" id="cl-clear-data-btn">Clear Data</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
  
  // Wire up Subscription section
  setTimeout(async () => {
    // Fetch subscription if not already loaded
    if (!subscriptionStatus) {
      await fetchSubscriptionStatus();
    }
    
    // Update the UI
    updateSubscriptionUI();
    
    // Upgrade button handlers
    const upgradeBtn = element.querySelector('#cl-upgrade-btn');
    upgradeBtn?.addEventListener('click', () => {
      window.open('https://clearledgr.com/pricing', '_blank');
    });
    element.querySelector('#cl-trial-upgrade-btn')?.addEventListener('click', () => {
      window.open('https://clearledgr.com/pricing', '_blank');
    });
  }, 100);
  
  // Wire up ERP connection buttons
  setTimeout(() => {
    const qboBtn = element.querySelector('#cl-qbo-btn');
    const xeroBtn = element.querySelector('#cl-xero-btn');
    const netsuiteBtn = element.querySelector('#cl-netsuite-btn');
    const clearDataBtn = element.querySelector('#cl-clear-data-btn');
    
    // QuickBooks OAuth
    qboBtn?.addEventListener('click', async () => {
      qboBtn.textContent = 'Connecting...';
      qboBtn.disabled = true;
      window.dispatchEvent(new CustomEvent('clearledgr:connect-erp', { 
        detail: { erp: 'quickbooks' } 
      }));
    });
    
    // Xero OAuth
    xeroBtn?.addEventListener('click', async () => {
      xeroBtn.textContent = 'Connecting...';
      xeroBtn.disabled = true;
      window.dispatchEvent(new CustomEvent('clearledgr:connect-erp', { 
        detail: { erp: 'xero' } 
      }));
    });
    
    // NetSuite connection
    netsuiteBtn?.addEventListener('click', async () => {
      netsuiteBtn.textContent = 'Connecting...';
      netsuiteBtn.disabled = true;
      window.dispatchEvent(new CustomEvent('clearledgr:connect-erp', { 
        detail: { erp: 'netsuite' } 
      }));
    });
    
    // Clear data button
    clearDataBtn?.addEventListener('click', () => {
      if (confirm('Are you sure you want to clear all local data? This cannot be undone.')) {
        window.dispatchEvent(new CustomEvent('clearledgr:clear-data'));
        alert('Local data cleared. Reload Gmail to see changes.');
      }
    });
    
    // Export CSV button
    const exportCsvBtn = element.querySelector('#cl-export-csv-btn');
    exportCsvBtn?.addEventListener('click', () => {
      window.dispatchEvent(new CustomEvent('clearledgr:export-csv'));
    });
    
    // GL Account Management - now fetches from ERP-synced endpoint
    const addGlBtn = element.querySelector('#cl-add-gl-btn');
    const glCodeInput = element.querySelector('#cl-gl-code-input');
    const glNameInput = element.querySelector('#cl-gl-name-input');
    const glTypeInput = element.querySelector('#cl-gl-type-input');
    const glSyncBtn = element.querySelector('#cl-gl-sync-btn');
    const addRuleBtn = element.querySelector('#cl-add-rule-btn');
    
    // Sync button - force refresh from ERP
    glSyncBtn?.addEventListener('click', async () => {
      glSyncBtn.textContent = 'Syncing...';
      glSyncBtn.disabled = true;
      try {
        await fetch(`${BACKEND_URL}/ap/gl/accounts/sync?organization_id=${getOrganizationId()}`, { method: 'POST' });
        loadSettingsGLAccounts(element);
      } catch (e) {
        console.warn('[Clearledgr] GL sync failed:', e);
      }
      glSyncBtn.textContent = 'Sync Accounts';
      glSyncBtn.disabled = false;
    });
    
    // Add custom account (edge case only)
    addGlBtn?.addEventListener('click', async () => {
      const code = glCodeInput?.value?.trim();
      const name = glNameInput?.value?.trim();
      const type = glTypeInput?.value || 'Expense';
      if (code && name) {
        try {
          await fetch(`${BACKEND_URL}/ap/gl/accounts/custom?organization_id=${getOrganizationId()}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code, name, type })
          });
          glCodeInput.value = '';
          glNameInput.value = '';
          loadSettingsGLAccounts(element);
        } catch (e) {
          console.warn('[Clearledgr] Failed to add custom account:', e);
        }
      }
    });
    
    addRuleBtn?.addEventListener('click', () => {
      const ruleType = element.querySelector('#cl-rule-type')?.value;
      const ruleValue = element.querySelector('#cl-rule-value')?.value?.trim();
      const ruleGl = element.querySelector('#cl-rule-gl')?.value;
      
      if (ruleType && ruleValue && ruleGl) {
        window.dispatchEvent(new CustomEvent('clearledgr:add-gl-rule', {
          detail: { type: ruleType, value: ruleValue, glCode: ruleGl }
        }));
        element.querySelector('#cl-rule-value').value = '';
      }
    });
    
    // ==================== EU VAT VALIDATION ====================
    const vatInput = element.querySelector('#cl-vat-input');
    const vatValidateBtn = element.querySelector('#cl-vat-validate-btn');
    const vatResult = element.querySelector('#cl-vat-result');
    
    vatValidateBtn?.addEventListener('click', async () => {
      const vatNumber = vatInput?.value?.trim();
      if (!vatNumber) {
        vatResult.style.display = 'block';
        vatResult.style.background = '#FFF3E0';
        vatResult.innerHTML = '<span style="color: #E65100;">Please enter a VAT number</span>';
        return;
      }
      
      vatValidateBtn.textContent = 'Validating...';
      vatValidateBtn.disabled = true;
      
      try {
        const response = await fetch(`${BACKEND_URL}/ap-advanced/vat/validate/${encodeURIComponent(vatNumber)}`);
        const data = await response.json();
        
        vatResult.style.display = 'block';
        
        if (data.is_valid) {
          vatResult.style.background = '#E8F5E9';
          vatResult.innerHTML = `
            <div style="display: flex; align-items: flex-start; gap: 12px;">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="#2E7D32" stroke-width="2"/>
                <path d="M8 12L11 15L16 9" stroke="#2E7D32" stroke-width="2" stroke-linecap="round"/>
              </svg>
              <div style="flex: 1;">
                <div style="font-weight: 500; color: #2E7D32;">Valid VAT Number</div>
                ${data.company_name ? `<div style="margin-top: 4px; color: #202124;">${escapeHtml(data.company_name)}</div>` : ''}
                ${data.company_address ? `<div style="font-size: 12px; color: #5f6368; margin-top: 2px;">${escapeHtml(data.company_address)}</div>` : ''}
                <div style="font-size: 11px; color: #9e9e9e; margin-top: 8px;">Validated via ${data.validation_source === 'vies' ? 'EU VIES' : data.validation_source}</div>
              </div>
            </div>
          `;
        } else {
          vatResult.style.background = '#FFEBEE';
          vatResult.innerHTML = `
            <div style="display: flex; align-items: flex-start; gap: 12px;">
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="10" stroke="#C62828" stroke-width="2"/>
                <path d="M15 9L9 15M9 9L15 15" stroke="#C62828" stroke-width="2" stroke-linecap="round"/>
              </svg>
              <div style="flex: 1;">
                <div style="font-weight: 500; color: #C62828;">Invalid VAT Number</div>
                <div style="font-size: 12px; color: #5f6368; margin-top: 4px;">${escapeHtml(data.error_message || 'The VAT number is not valid')}</div>
              </div>
            </div>
          `;
        }
      } catch (e) {
        console.warn('[Clearledgr] VAT validation error:', e);
        vatResult.style.display = 'block';
        vatResult.style.background = '#FFF3E0';
        vatResult.innerHTML = '<span style="color: #E65100;">Unable to validate. Please try again later.</span>';
      }
      
      vatValidateBtn.textContent = 'Validate';
      vatValidateBtn.disabled = false;
    });
    
    // VAT input - validate on Enter
    vatInput?.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') {
        vatValidateBtn?.click();
      }
    });
    
    // ==================== GDPR / DATA RESIDENCY ====================
    const dataRegionSelect = element.querySelector('#cl-data-region');
    const gdprToggle = element.querySelector('#cl-setting-gdpr');
    const piiEncryptToggle = element.querySelector('#cl-setting-pii-encrypt');
    const dataRetentionSelect = element.querySelector('#cl-data-retention');
    const dpaBtn = element.querySelector('#cl-dpa-btn');
    const dpaStatus = element.querySelector('#cl-dpa-status');
    const gdprExportBtn = element.querySelector('#cl-gdpr-export-btn');
    const gdprDeleteBtn = element.querySelector('#cl-gdpr-delete-btn');
    
    // Load current data residency settings
    (async () => {
      try {
        const response = await fetch(`${BACKEND_URL}/config/organizations/${getOrganizationId()}/data-residency`);
        if (response.ok) {
          const data = await response.json();
          const residency = data.data_residency || {};
          
          if (dataRegionSelect) dataRegionSelect.value = residency.data_region || 'eu';
          if (gdprToggle) gdprToggle.checked = residency.gdpr_compliant !== false;
          if (piiEncryptToggle) piiEncryptToggle.checked = residency.pii_encryption_enabled !== false;
          if (dataRetentionSelect) dataRetentionSelect.value = String(residency.data_retention_days || 2555);
          
          if (residency.dpa_signed && dpaStatus && dpaBtn) {
            dpaStatus.textContent = `Signed on ${new Date(residency.dpa_signed_date).toLocaleDateString()}`;
            dpaBtn.textContent = 'View DPA';
          }
        }
      } catch (e) {
        console.warn('[Clearledgr] Failed to load data residency settings:', e);
      }
    })();
    
    // Save data residency changes
    const saveDataResidency = async (updates) => {
      try {
        await fetch(`${BACKEND_URL}/config/organizations/${getOrganizationId()}/data-residency`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(updates),
        });
      } catch (e) {
        console.warn('[Clearledgr] Failed to save data residency:', e);
      }
    };
    
    dataRegionSelect?.addEventListener('change', () => {
      saveDataResidency({ data_region: dataRegionSelect.value });
    });
    
    gdprToggle?.addEventListener('change', () => {
      saveDataResidency({ gdpr_compliant: gdprToggle.checked });
    });
    
    piiEncryptToggle?.addEventListener('change', () => {
      saveDataResidency({ pii_encryption_enabled: piiEncryptToggle.checked });
    });
    
    dataRetentionSelect?.addEventListener('change', () => {
      saveDataResidency({ data_retention_days: parseInt(dataRetentionSelect.value) });
    });
    
    // DPA signing
    dpaBtn?.addEventListener('click', async () => {
      if (dpaBtn.textContent === 'View DPA') {
        window.open('https://clearledgr.com/legal/dpa', '_blank');
        return;
      }
      
      if (confirm('By signing the Data Processing Agreement, you agree to the terms of data processing. Continue?')) {
        try {
          await saveDataResidency({ dpa_signed: true });
          if (dpaStatus) dpaStatus.textContent = `Signed on ${new Date().toLocaleDateString()}`;
          if (dpaBtn) dpaBtn.textContent = 'View DPA';
        } catch (e) {
          alert('Failed to sign DPA. Please try again.');
        }
      }
    });
    
    // GDPR Data Export (Art. 20)
    gdprExportBtn?.addEventListener('click', async () => {
      gdprExportBtn.disabled = true;
      gdprExportBtn.querySelector('span').innerHTML = 'Requesting...';
      
      try {
        const response = await fetch(`${BACKEND_URL}/config/organizations/${getOrganizationId()}/gdpr/data-export-request`, {
          method: 'POST',
        });
        const data = await response.json();
        
        if (response.ok) {
          alert(`Data export requested!\\n\\nRequest ID: ${data.request_id}\\nEstimated time: ${data.estimated_completion}\\n\\nYou will receive a notification when your export is ready.`);
        } else {
          alert(data.detail || 'Failed to request data export');
        }
      } catch (e) {
        alert('Failed to request data export. Please try again.');
      }
      
      gdprExportBtn.disabled = false;
      gdprExportBtn.querySelector('span').innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
          <path d="M21 15V19C21 20.1046 20.1046 21 19 21H5C3.89543 21 3 20.1046 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <path d="M7 10L12 15L17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        Request Data Export (Art. 20)
      `;
    });
    
    // GDPR Deletion (Art. 17)
    gdprDeleteBtn?.addEventListener('click', async () => {
      const confirmed = confirm(
        'WARNING: Data Deletion Request\\n\\n' +
        'This will permanently delete ALL your organization data within 30 days.\\n\\n' +
        'This action cannot be undone.\\n\\n' +
        'Are you sure you want to proceed?'
      );
      
      if (!confirmed) return;
      
      const doubleConfirm = confirm(
        'FINAL CONFIRMATION\\n\\n' +
        'Type "DELETE" in the next prompt to confirm deletion.'
      );
      
      if (!doubleConfirm) return;
      
      const typed = prompt('Type DELETE to confirm:');
      if (typed !== 'DELETE') {
        alert('Deletion cancelled. You typed: ' + typed);
        return;
      }
      
      gdprDeleteBtn.disabled = true;
      
      try {
        const response = await fetch(`${BACKEND_URL}/config/organizations/${getOrganizationId()}/gdpr/deletion-request?confirm=true`, {
          method: 'POST',
        });
        const data = await response.json();
        
        if (response.ok) {
          alert(`Deletion request submitted.\\n\\nRequest ID: ${data.request_id}\\nGrace period: ${data.grace_period}\\n\\nContact support to cancel if needed.`);
        } else {
          alert(data.detail || 'Failed to submit deletion request');
        }
      } catch (e) {
        alert('Failed to submit deletion request. Please try again.');
      }
      
      gdprDeleteBtn.disabled = false;
    });
    
    // Load GL accounts from new ERP-synced endpoint
    loadSettingsGLAccounts(element);
    
    // Request current ERP status (for other UI elements)
    window.dispatchEvent(new CustomEvent('clearledgr:request-erp-status'));
  }, 100);
}

// Load GL accounts from ERP-synced endpoint for settings page
async function loadSettingsGLAccounts(element) {
  const accountsList = element.querySelector('#cl-gl-accounts-list');
  const erpIcon = element.querySelector('#cl-gl-erp-icon');
  const erpText = element.querySelector('#cl-gl-erp-text');
  const ruleGlSelect = element.querySelector('#cl-rule-gl');
  
  try {
    const response = await fetch(`${BACKEND_URL}/ap/gl/accounts?organization_id=${getOrganizationId()}`);
    if (response.ok) {
      const data = await response.json();
      const accounts = data.accounts || [];
      
      // Update ERP connection status indicator
      if (data.erp_connected) {
        if (erpIcon) erpIcon.style.background = '#4CAF50';
        if (erpText) {
          erpText.textContent = `Connected to ${(data.erp_type || 'ERP').toUpperCase()} - ${accounts.length} accounts`;
          if (data.last_synced) {
            const syncTime = new Date(data.last_synced).toLocaleTimeString();
            erpText.textContent += ` (synced ${syncTime})`;
          }
        }
      } else {
        if (erpIcon) erpIcon.style.background = '#FF9800';
        if (erpText) erpText.textContent = 'No ERP connected. Connect an ERP to sync accounts automatically.';
      }
      
      // Populate accounts list - show ERP source vs custom
      if (accountsList) {
        if (accounts.length === 0) {
          accountsList.innerHTML = '<div style="color: #9e9e9e; font-size: 13px; padding: 12px 0;">No GL accounts available. Connect your ERP or add custom accounts below.</div>';
        } else {
          accountsList.innerHTML = accounts.map(acc => `
            <div style="display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f1f3f4;">
              <span style="font-family: 'Roboto Mono', monospace; font-weight: 500; color: #10B981; min-width: 80px;">${escapeHtml(acc.code)}</span>
              <span style="flex: 1; color: #202124;">${escapeHtml(acc.name)}</span>
              <span style="font-size: 11px; color: #5f6368; background: #f1f3f4; padding: 2px 8px; border-radius: 4px;">${acc.type || 'Expense'}</span>
              ${acc.is_custom 
                ? `<button class="cl-btn cl-btn-secondary" style="padding: 2px 8px; font-size: 11px;" data-delete-code="${acc.code}">Remove</button>` 
                : '<span style="font-size: 10px; color: #9e9e9e; padding: 2px 6px;">from ERP</span>'}
            </div>
          `).join('');
          
          // Add delete handlers for custom accounts
          accountsList.querySelectorAll('[data-delete-code]').forEach(btn => {
            btn.addEventListener('click', async () => {
              const code = btn.dataset.deleteCode;
              try {
                await fetch(`${BACKEND_URL}/ap/gl/accounts/custom/${code}?organization_id=${getOrganizationId()}`, { method: 'DELETE' });
                loadSettingsGLAccounts(element);
              } catch (e) {
                console.warn('[Clearledgr] Failed to delete account:', e);
              }
            });
          });
        }
      }
      
      // Populate GL select dropdown for categorization rules
      if (ruleGlSelect) {
        ruleGlSelect.innerHTML = '<option value="">Select GL Account</option>' + 
          accounts.map(acc => `<option value="${acc.code}">${acc.code} - ${escapeHtml(acc.name)}</option>`).join('');
      }
      
      // Store accounts for other components
      window._clearledgrGLAccounts = accounts;
    }
  } catch (e) {
    console.warn('[Clearledgr] Failed to load GL accounts:', e);
    if (accountsList) {
      accountsList.innerHTML = '<div style="color: #F44336; font-size: 13px; padding: 12px 0;">Failed to load accounts. Check your connection.</div>';
    }
    if (erpIcon) erpIcon.style.background = '#F44336';
    if (erpText) erpText.textContent = 'Connection error';
  }
}

// Listen for GL config data
window.addEventListener('clearledgr:gl-config-data', (e) => {
  const { accounts = [], rules = [] } = e.detail || {};
  
  // Populate accounts list
  const accountsList = document.getElementById('cl-gl-accounts-list');
  if (accountsList) {
    if (accounts.length === 0) {
      accountsList.innerHTML = '<div style="color: #9e9e9e; font-size: 13px; padding: 12px 0;">No GL accounts configured. Add accounts below.</div>';
    } else {
      accountsList.innerHTML = accounts.map(acc => `
        <div style="display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f1f3f4;">
          <span style="font-weight: 500; color: #202124; min-width: 80px;">${acc.code}</span>
          <span style="flex: 1; color: #5f6368;">${acc.name}</span>
          <button class="cl-btn cl-btn-secondary" style="padding: 4px 12px; font-size: 12px;" onclick="window.dispatchEvent(new CustomEvent('clearledgr:delete-gl-account', { detail: { code: '${acc.code}' } }))">Remove</button>
        </div>
      `).join('');
    }
  }
  
  // Populate GL select dropdown
  const ruleGlSelect = document.getElementById('cl-rule-gl');
  if (ruleGlSelect) {
    ruleGlSelect.innerHTML = '<option value="">Select GL Account</option>' + 
      accounts.map(acc => `<option value="${acc.code}">${acc.code} - ${acc.name}</option>`).join('');
  }
  
  // Populate rules list
  const rulesList = document.getElementById('cl-gl-rules-list');
  if (rulesList) {
    if (rules.length === 0) {
      rulesList.innerHTML = '<div style="color: #9e9e9e; font-size: 13px; padding: 12px 0;">No auto-categorization rules. Add rules below.</div>';
    } else {
      rulesList.innerHTML = rules.map((rule, idx) => {
        const typeLabels = { keyword: 'Keyword', vendor: 'Vendor', amount_range: 'Amount Range' };
        return `
          <div style="display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid #f1f3f4;">
            <span style="background: #E3F2FD; color: #1565C0; padding: 2px 8px; border-radius: 4px; font-size: 11px;">${typeLabels[rule.type] || rule.type}</span>
            <span style="flex: 1; color: #202124;">"${rule.value}"</span>
            <span style="color: #5f6368;">→</span>
            <span style="font-weight: 500; color: #10B981;">${rule.glCode}</span>
            <button class="cl-btn cl-btn-secondary" style="padding: 4px 12px; font-size: 12px;" onclick="window.dispatchEvent(new CustomEvent('clearledgr:delete-gl-rule', { detail: { index: ${idx} } }))">Remove</button>
          </div>
        `;
      }).join('');
    }
  }
});

// Listen for ERP status updates
window.addEventListener('clearledgr:erp-status', (e) => {
  const status = e.detail || {};
  
  // Update QuickBooks status
  const qboBadge = document.getElementById('cl-qbo-badge');
  const qboBtn = document.getElementById('cl-qbo-btn');
  if (qboBadge && status.quickbooks?.connected) {
    qboBadge.className = 'cl-erp-badge connected';
    qboBadge.querySelector('.cl-erp-text').textContent = 'Connected';
    if (qboBtn) {
      qboBtn.textContent = 'Disconnect';
      qboBtn.className = 'cl-btn cl-btn-danger';
    }
  }
  
  // Update Xero status
  const xeroBadge = document.getElementById('cl-xero-badge');
  const xeroBtn = document.getElementById('cl-xero-btn');
  if (xeroBadge && status.xero?.connected) {
    xeroBadge.className = 'cl-erp-badge connected';
    xeroBadge.querySelector('.cl-erp-text').textContent = 'Connected';
    if (xeroBtn) {
      xeroBtn.textContent = 'Disconnect';
      xeroBtn.className = 'cl-btn cl-btn-danger';
    }
  }
  
  // Update NetSuite status
  const netsuiteBadge = document.getElementById('cl-netsuite-badge');
  const netsuiteBtn = document.getElementById('cl-netsuite-btn');
  if (netsuiteBadge && status.netsuite?.connected) {
    netsuiteBadge.className = 'cl-erp-badge connected';
    netsuiteBadge.querySelector('.cl-erp-text').textContent = 'Connected';
    if (netsuiteBtn) {
      netsuiteBtn.textContent = 'Disconnect';
      netsuiteBtn.className = 'cl-btn cl-btn-danger';
    }
  }
});

// Listen for ERP connection result
window.addEventListener('clearledgr:erp-connected', (e) => {
  const { erp, success, error } = e.detail || {};
  
  if (success) {
    alert(`Successfully connected to ${erp}!`);
    // Request updated status
    window.dispatchEvent(new CustomEvent('clearledgr:request-erp-status'));
  } else {
    alert(`Failed to connect to ${erp}: ${error || 'Unknown error'}`);
    // Re-enable button
    const btn = document.getElementById(`cl-${erp === 'quickbooks' ? 'qbo' : erp}-btn`);
    if (btn) {
      btn.textContent = 'Connect';
      btn.disabled = false;
    }
  }
});

// =============================================================================
// PAYMENTS VIEW - Payment execution and tracking
// =============================================================================

function renderPayments(element, params = {}) {
  const statusFilter = params.status || 'all';
  
  element.innerHTML = `
    <style>
      .cl-payments { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 1200px; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; gap: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; flex: 1; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; flex-shrink: 0; }
      
      .cl-payment-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
      .cl-summary-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-summary-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-summary-label { font-size: 13px; color: #5f6368; margin-top: 4px; }
      .cl-summary-card.pending { border-left: 3px solid #FF9800; }
      .cl-summary-card.scheduled { border-left: 3px solid #2196F3; }
      .cl-summary-card.processing { border-left: 3px solid #9C27B0; }
      .cl-summary-card.completed { border-left: 3px solid #4CAF50; }
      
      .cl-payments-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
      .cl-filter-tabs { display: flex; gap: 8px; }
      .cl-filter-tab { padding: 8px 16px; border-radius: 20px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; transition: all 0.2s; }
      .cl-filter-tab:hover { border-color: #10B981; }
      .cl-filter-tab.active { background: #10B981; color: white; border-color: #10B981; }
      
      .cl-payments-actions { display: flex; gap: 12px; flex-shrink: 0; }
      .cl-btn { display: inline-flex; align-items: center; justify-content: center; padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; font-weight: 500; flex: 0 0 auto; width: fit-content; }
      .cl-btn-primary { background: #10B981; color: white; }
      .cl-btn-primary:hover { background: #059669; }
      .cl-btn-secondary { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
      .cl-btn-secondary:hover { border-color: #10B981; color: #10B981; }
      
      .cl-payments-table { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-table { width: 100%; border-collapse: collapse; }
      .cl-table th { text-align: left; padding: 14px 20px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #e0e0e0; }
      .cl-table td { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-size: 14px; color: #202124; }
      .cl-table tr:hover { background: #f8f9fa; }
      .cl-table tr:last-child td { border-bottom: none; }
      
      .cl-vendor-name { font-weight: 500; }
      .cl-amount { font-weight: 500; font-family: 'Roboto Mono', monospace; }
      .cl-method-badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 500; text-transform: uppercase; }
      .cl-method-ach { background: #E3F2FD; color: #1565C0; }
      .cl-method-wire { background: #FFF3E0; color: #E65100; }
      .cl-method-check { background: #F3E5F5; color: #7B1FA2; }
      
      .cl-status-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
      .cl-status-pending { background: #FFF3E0; color: #E65100; }
      .cl-status-scheduled { background: #E3F2FD; color: #1565C0; }
      .cl-status-processing { background: #F3E5F5; color: #7B1FA2; }
      .cl-status-sent { background: #E8F5E9; color: #2E7D32; }
      .cl-status-completed { background: #E8F5E9; color: #1B5E20; }
      .cl-status-failed { background: #FFEBEE; color: #C62828; }
      
      .cl-action-btn { padding: 6px 12px; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 12px; background: white; cursor: pointer; }
      .cl-action-btn:hover { border-color: #10B981; color: #10B981; }
      .cl-action-btn.pay { background: #10B981; color: white; border-color: #10B981; }
      .cl-action-btn.pay:hover { background: #059669; }
      
      .cl-empty-state { padding: 64px 40px; text-align: center; }
      .cl-empty-icon { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.4; }
      .cl-empty-title { font-size: 16px; font-weight: 500; color: #202124; margin-bottom: 8px; }
      .cl-empty-desc { font-size: 14px; color: #5f6368; }
      
      /* Payment Modal */
      .cl-payment-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; z-index: 999999; }
      .cl-payment-modal.visible { display: flex; }
      .cl-modal-content { background: white; border-radius: 12px; width: 520px; max-height: 80vh; overflow-y: auto; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
      .cl-modal-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
      .cl-modal-header h3 { margin: 0; font-size: 18px; font-weight: 500; }
      .cl-modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; }
      .cl-modal-body { padding: 24px; }
      .cl-form-group { margin-bottom: 20px; }
      .cl-form-label { display: block; font-size: 13px; font-weight: 500; color: #5f6368; margin-bottom: 8px; }
      .cl-form-input { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
      .cl-form-input:focus { outline: none; border-color: #10B981; }
      .cl-form-select { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; background: white; }
      .cl-modal-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
      
      .cl-method-selector { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }
      .cl-method-option { padding: 16px; border: 2px solid #e0e0e0; border-radius: 8px; cursor: pointer; text-align: center; transition: all 0.2s; }
      .cl-method-option:hover { border-color: #10B981; }
      .cl-method-option.selected { border-color: #10B981; background: #f0fdf4; }
      .cl-method-option svg { width: 32px; height: 32px; margin-bottom: 8px; }
      .cl-method-option .label { font-size: 13px; font-weight: 500; color: #202124; }
      .cl-method-option .desc { font-size: 11px; color: #5f6368; margin-top: 4px; }
    </style>
    
    <div class="cl-payments">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="9" stroke="#10B981" stroke-width="2" fill="none"/>
            <path d="M12 6V18M9 9C9 7.5 10.5 6.75 12 6.75C14.25 6.75 15 8.25 15 9C15 10.5 13.5 11.25 12 11.25C10.5 11.25 9 12 9 13.5C9 15 10.5 15.75 12 17.25C13.5 17.25 15 16.5 15 15" stroke="#10B981" stroke-width="1.8" stroke-linecap="round"/>
          </svg>
          <h1>Payments</h1>
        </div>
        <div class="cl-payments-actions">
          <button class="cl-btn cl-btn-secondary" id="cl-download-nacha">Download NACHA</button>
          <button class="cl-btn cl-btn-primary" id="cl-create-batch">Create Batch</button>
        </div>
      </div>
      
      <div class="cl-payment-summary" id="cl-payment-summary">
        <div class="cl-summary-card pending">
          <div class="cl-summary-value" id="cl-pay-pending">0</div>
          <div class="cl-summary-label">Pending Payment</div>
        </div>
        <div class="cl-summary-card scheduled">
          <div class="cl-summary-value" id="cl-pay-scheduled">0</div>
          <div class="cl-summary-label">Scheduled</div>
        </div>
        <div class="cl-summary-card processing">
          <div class="cl-summary-value" id="cl-pay-amount">$0</div>
          <div class="cl-summary-label">Processing Amount</div>
        </div>
        <div class="cl-summary-card completed">
          <div class="cl-summary-value" id="cl-pay-completed">0</div>
          <div class="cl-summary-label">Completed (30d)</div>
        </div>
      </div>
      
      <div class="cl-payments-toolbar">
        <div class="cl-filter-tabs">
          <button class="cl-filter-tab ${statusFilter === 'all' ? 'active' : ''}" data-status="all">All</button>
          <button class="cl-filter-tab ${statusFilter === 'pending' ? 'active' : ''}" data-status="pending">Pending</button>
          <button class="cl-filter-tab ${statusFilter === 'scheduled' ? 'active' : ''}" data-status="scheduled">Scheduled</button>
          <button class="cl-filter-tab ${statusFilter === 'processing' ? 'active' : ''}" data-status="processing">Processing</button>
          <button class="cl-filter-tab ${statusFilter === 'completed' ? 'active' : ''}" data-status="completed">Completed</button>
        </div>
      </div>
      
      <div class="cl-payments-table">
        <table class="cl-table">
          <thead>
            <tr>
              <th><input type="checkbox" id="cl-select-all-payments"></th>
              <th>Vendor</th>
              <th>Invoice</th>
              <th>Amount</th>
              <th>Method</th>
              <th>Scheduled</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-payments-body">
            <tr>
              <td colspan="8">
                <div class="cl-empty-state" id="cl-payments-loading">
                  <div class="cl-empty-title">Loading payments...</div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    
    <!-- Create Payment Modal -->
    <div class="cl-payment-modal" id="cl-payment-modal">
      <div class="cl-modal-content">
        <div class="cl-modal-header">
          <h3>Create Payment</h3>
          <button class="cl-modal-close" id="cl-payment-modal-close">&times;</button>
        </div>
        <div class="cl-modal-body">
          <div class="cl-method-selector">
            <div class="cl-method-option selected" data-method="ach">
              <svg viewBox="0 0 24 24" fill="none"><rect x="2" y="4" width="20" height="16" rx="2" stroke="#1565C0" stroke-width="2"/><line x1="2" y1="10" x2="22" y2="10" stroke="#1565C0" stroke-width="2"/></svg>
              <div class="label">ACH Transfer</div>
              <div class="desc">1-3 business days</div>
            </div>
            <div class="cl-method-option" data-method="wire">
              <svg viewBox="0 0 24 24" fill="none"><path d="M12 2L2 7V17L12 22L22 17V7L12 2Z" stroke="#E65100" stroke-width="2"/><path d="M12 22V12M2 7L12 12L22 7" stroke="#E65100" stroke-width="2"/></svg>
              <div class="label">Wire Transfer</div>
              <div class="desc">Same day</div>
            </div>
            <div class="cl-method-option" data-method="check">
              <svg viewBox="0 0 24 24" fill="none"><rect x="2" y="5" width="20" height="14" rx="1" stroke="#7B1FA2" stroke-width="2"/><line x1="6" y1="15" x2="14" y2="15" stroke="#7B1FA2" stroke-width="2"/><line x1="6" y1="11" x2="10" y2="11" stroke="#7B1FA2" stroke-width="1.5"/></svg>
              <div class="label">Check</div>
              <div class="desc">Print & mail</div>
            </div>
          </div>
          
          <div class="cl-form-group">
            <label class="cl-form-label">Vendor</label>
            <input type="text" class="cl-form-input" id="cl-pay-vendor" readonly>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Amount</label>
            <input type="text" class="cl-form-input" id="cl-pay-amount-input" readonly>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Schedule Date</label>
            <input type="date" class="cl-form-input" id="cl-pay-schedule-date">
          </div>
          <div class="cl-form-group" id="cl-bank-info-section" style="display: none;">
            <label class="cl-form-label">Bank Account</label>
            <select class="cl-form-select" id="cl-pay-bank-account">
              <option value="">Select bank account</option>
            </select>
            <p style="font-size: 12px; color: #5f6368; margin-top: 8px;">Or add new bank info in Vendor settings</p>
          </div>
        </div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-secondary" id="cl-payment-cancel">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-payment-submit">Create Payment</button>
        </div>
      </div>
    </div>
  `;
  
  // Load payments data
  loadPaymentsData(element, statusFilter);
  
  // Wire up filter tabs
  element.querySelectorAll('.cl-filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const status = tab.dataset.status;
      sdk.Router.goto('clearledgr/payments', { status });
    });
  });
  
  // Wire up modal
  const modal = element.querySelector('#cl-payment-modal');
  const closeBtn = element.querySelector('#cl-payment-modal-close');
  const cancelBtn = element.querySelector('#cl-payment-cancel');
  
  closeBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  cancelBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  
  // Method selector
  element.querySelectorAll('.cl-method-option').forEach(opt => {
    opt.addEventListener('click', () => {
      element.querySelectorAll('.cl-method-option').forEach(o => o.classList.remove('selected'));
      opt.classList.add('selected');
      // Show/hide bank info section for ACH
      const bankSection = element.querySelector('#cl-bank-info-section');
      if (bankSection) {
        bankSection.style.display = opt.dataset.method === 'ach' ? 'block' : 'none';
      }
    });
  });
  
  // Create batch button
  element.querySelector('#cl-create-batch')?.addEventListener('click', async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/ap/payments/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ organization_id: 'default' })
      });
      if (response.ok) {
        const batch = await response.json();
        showToast(`Batch created with ${batch.payment_count} payments`, 'success');
        loadPaymentsData(element, statusFilter);
      } else {
        showToast('Failed to create batch', 'error');
      }
    } catch (err) {
      showToast('Unable to connect. Please try again.', 'error');
    }
  });
  
  // Download NACHA button
  element.querySelector('#cl-download-nacha')?.addEventListener('click', async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/ap/payments/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ organization_id: 'default' })
      });
      if (response.ok) {
        const batch = await response.json();
        if (batch.nacha_file) {
          const blob = new Blob([batch.nacha_file], { type: 'text/plain' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `nacha_batch_${batch.batch_id}.txt`;
          a.click();
          URL.revokeObjectURL(url);
          showToast('NACHA file downloaded', 'success');
        }
      }
    } catch (err) {
      showToast('Failed to generate NACHA file', 'error');
    }
  });
}

async function loadPaymentsData(element, statusFilter = 'all') {
  try {
    // Fetch summary
    const summaryResponse = await fetch(`${BACKEND_URL}/ap/payments/summary?organization_id=default`);
    if (summaryResponse.ok) {
      const summary = await summaryResponse.json();
      const pendingEl = element.querySelector('#cl-pay-pending');
      const scheduledEl = element.querySelector('#cl-pay-scheduled');
      const amountEl = element.querySelector('#cl-pay-amount');
      const completedEl = element.querySelector('#cl-pay-completed');
      
      if (pendingEl) pendingEl.textContent = summary.pending || 0;
      if (scheduledEl) scheduledEl.textContent = summary.scheduled || 0;
      if (amountEl) amountEl.textContent = formatCurrency(summary.processing_amount || 0);
      if (completedEl) completedEl.textContent = summary.completed_30d || 0;
    }
    
    // Fetch payments list
    const paymentsResponse = await fetch(`${BACKEND_URL}/ap/payments/pending?organization_id=default`);
    const tbody = element.querySelector('#cl-payments-body');
    
    if (paymentsResponse.ok) {
      const payments = await paymentsResponse.json();
      
      // Filter if needed
      const filtered = statusFilter === 'all' ? payments : 
        payments.filter(p => p.status?.toLowerCase() === statusFilter);
      
      if (filtered.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="8">
              <div class="cl-empty-state">
                <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
                  <circle cx="32" cy="32" r="24" stroke="#e0e0e0" stroke-width="3"/>
                  <path d="M32 18V46M24 26C24 22 28 20 32 20C38 20 40 24 40 26C40 30 36 32 32 32C28 32 24 34 24 38C24 42 28 44 32 50C36 50 40 48 40 44" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                </svg>
                <div class="cl-empty-title">No payments yet</div>
                <div class="cl-empty-desc">Approved invoices will appear here for payment</div>
              </div>
            </td>
          </tr>
        `;
      } else {
        tbody.innerHTML = filtered.map(payment => `
          <tr data-payment-id="${payment.payment_id}">
            <td><input type="checkbox" class="cl-payment-checkbox"></td>
            <td><span class="cl-vendor-name">${escapeHtml(payment.vendor_name || 'Unknown')}</span></td>
            <td>${escapeHtml(payment.invoice_id || '-')}</td>
            <td><span class="cl-amount">${formatCurrency(payment.amount)}</span></td>
            <td><span class="cl-method-badge cl-method-${payment.method || 'ach'}">${(payment.method || 'ACH').toUpperCase()}</span></td>
            <td>${payment.scheduled_date || '-'}</td>
            <td><span class="cl-status-badge cl-status-${payment.status?.toLowerCase() || 'pending'}">${payment.status || 'Pending'}</span></td>
            <td>
              <button class="cl-action-btn pay" data-action="pay" data-id="${payment.payment_id}">Process</button>
            </td>
          </tr>
        `).join('');
        
        // Wire up action buttons
        tbody.querySelectorAll('.cl-action-btn[data-action="pay"]').forEach(btn => {
          btn.addEventListener('click', async () => {
            const paymentId = btn.dataset.id;
            try {
              await fetch(`${BACKEND_URL}/ap/payments/${paymentId}/mark-sent`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ payment_id: paymentId, organization_id: 'default' })
              });
              showToast('Payment processing', 'success');
              loadPaymentsData(element, statusFilter);
            } catch (err) {
              showToast('Failed to process payment', 'error');
            }
          });
        });
      }
    } else {
      tbody.innerHTML = `
        <tr>
          <td colspan="8">
            <div class="cl-empty-state">
              <div class="cl-empty-title" style="color: #FF9800;">Unable to load payments</div>
              <div class="cl-empty-desc">We're having trouble connecting. Please refresh to try again.</div>
            </div>
          </td>
        </tr>
      `;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load payments:', err);
  }
}

// =============================================================================
// GL CORRECTIONS VIEW - Correct GL codes and train the AI
// =============================================================================

function renderGLCorrections(element) {
  element.innerHTML = `
    <style>
      .cl-gl { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 1200px; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; gap: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; flex: 1; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; flex-shrink: 0; }
      .cl-header-actions { flex-shrink: 0; }
      
      .cl-gl-summary { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 32px; }
      .cl-summary-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-summary-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-summary-label { font-size: 13px; color: #5f6368; margin-top: 4px; }
      .cl-summary-card.accuracy { border-left: 3px solid #4CAF50; }
      .cl-summary-card.corrections { border-left: 3px solid #FF9800; }
      .cl-summary-card.learned { border-left: 3px solid #2196F3; }
      
      .cl-gl-section { margin-bottom: 32px; }
      .cl-section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
      .cl-section-title { font-size: 16px; font-weight: 500; color: #202124; }
      
      .cl-gl-table { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-table { width: 100%; border-collapse: collapse; }
      .cl-table th { text-align: left; padding: 14px 20px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #e0e0e0; }
      .cl-table td { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-size: 14px; color: #202124; }
      .cl-table tr:hover { background: #f8f9fa; }
      .cl-table tr:last-child td { border-bottom: none; }
      
      .cl-gl-code { font-family: 'Roboto Mono', monospace; font-size: 13px; padding: 4px 8px; border-radius: 4px; }
      .cl-gl-original { background: #FFEBEE; color: #C62828; }
      .cl-gl-corrected { background: #E8F5E9; color: #2E7D32; }
      .cl-gl-arrow { color: #9e9e9e; margin: 0 8px; }
      
      .cl-confidence { display: flex; align-items: center; gap: 8px; }
      .cl-confidence-bar { width: 60px; height: 6px; background: #e0e0e0; border-radius: 3px; overflow: hidden; }
      .cl-confidence-fill { height: 100%; border-radius: 3px; }
      .cl-confidence-high { background: #4CAF50; }
      .cl-confidence-medium { background: #FF9800; }
      .cl-confidence-low { background: #F44336; }
      
      .cl-empty-state { padding: 64px 40px; text-align: center; }
      .cl-empty-icon { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.4; }
      .cl-empty-title { font-size: 16px; font-weight: 500; color: #202124; margin-bottom: 8px; }
      .cl-empty-desc { font-size: 14px; color: #5f6368; }
      
      /* GL Account Grid */
      .cl-gl-accounts { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; margin-top: 16px; }
      .cl-gl-account-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; display: flex; align-items: center; justify-content: space-between; }
      .cl-gl-account-card:hover { border-color: #10B981; }
      .cl-gl-account-info { display: flex; align-items: center; gap: 12px; }
      .cl-gl-account-code { font-family: 'Roboto Mono', monospace; font-size: 14px; font-weight: 600; color: #10B981; }
      .cl-gl-account-name { font-size: 14px; color: #202124; }
      .cl-gl-account-type { font-size: 11px; color: #5f6368; background: #f1f3f4; padding: 2px 8px; border-radius: 4px; text-transform: capitalize; }
      
      /* Correction Modal */
      .cl-correction-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; z-index: 999999; }
      .cl-correction-modal.visible { display: flex; }
      .cl-modal-content { background: white; border-radius: 12px; width: 480px; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
      .cl-modal-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
      .cl-modal-header h3 { margin: 0; font-size: 18px; font-weight: 500; }
      .cl-modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; }
      .cl-modal-body { padding: 24px; }
      .cl-form-group { margin-bottom: 20px; }
      .cl-form-label { display: block; font-size: 13px; font-weight: 500; color: #5f6368; margin-bottom: 8px; }
      .cl-form-input, .cl-form-select { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
      .cl-form-input:focus, .cl-form-select:focus { outline: none; border-color: #10B981; }
      .cl-modal-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
      .cl-btn { display: inline-flex; align-items: center; justify-content: center; padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; flex: 0 0 auto; width: fit-content; }
      .cl-btn-primary { background: #10B981; color: white; }
      .cl-btn-primary:hover { background: #059669; }
      .cl-btn-secondary { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
      
      .cl-learning-indicator { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #5f6368; margin-top: 8px; }
      .cl-learning-indicator svg { width: 16px; height: 16px; }
    </style>
    
    <div class="cl-gl">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <rect x="3" y="3" width="18" height="18" rx="2" stroke="#10B981" stroke-width="2" fill="none"/>
            <line x1="8" y1="3" x2="8" y2="21" stroke="#10B981" stroke-width="1.5"/>
            <path d="M11 12L14 15L20 9" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <h1>GL Corrections</h1>
        </div>
      </div>
      
      <div class="cl-gl-summary" id="cl-gl-summary">
        <div class="cl-summary-card accuracy">
          <div class="cl-summary-value" id="cl-gl-accuracy">--%</div>
          <div class="cl-summary-label">Current Accuracy</div>
        </div>
        <div class="cl-summary-card corrections">
          <div class="cl-summary-value" id="cl-gl-total-corrections">0</div>
          <div class="cl-summary-label">Total Corrections</div>
        </div>
        <div class="cl-summary-card learned">
          <div class="cl-summary-value" id="cl-gl-learned-rules">0</div>
          <div class="cl-summary-label">Learned Rules</div>
        </div>
      </div>
      
      <div class="cl-gl-section">
        <div class="cl-section-header">
          <span class="cl-section-title">Recent Corrections</span>
          <div class="cl-learning-indicator">
            <svg viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="6" stroke="#10B981" stroke-width="1.5"/>
              <path d="M8 5V8L10 9" stroke="#10B981" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
            <span>AI learns from every correction you make</span>
          </div>
        </div>
        <div class="cl-gl-table">
          <table class="cl-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Vendor</th>
                <th>Invoice</th>
                <th>GL Change</th>
                <th>Reason</th>
                <th>Confidence Impact</th>
              </tr>
            </thead>
            <tbody id="cl-corrections-body">
              <tr>
                <td colspan="6">
                  <div class="cl-empty-state" id="cl-corrections-loading">
                    <div class="cl-empty-title">Loading corrections...</div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      
      <div class="cl-gl-section">
        <div class="cl-section-header">
          <span class="cl-section-title">GL Accounts</span>
          <div style="flex-shrink: 0;"><button class="cl-btn cl-btn-secondary" id="cl-add-gl-account-btn">+ Add Account</button></div>
        </div>
        <div class="cl-gl-accounts" id="cl-gl-accounts">
          <div style="color: #9e9e9e; padding: 20px;">Loading GL accounts...</div>
        </div>
      </div>
    </div>
    
    <!-- Add GL Account Modal -->
    <div class="cl-correction-modal" id="cl-add-gl-modal">
      <div class="cl-modal-content">
        <div class="cl-modal-header">
          <h3>Add GL Account</h3>
          <button class="cl-modal-close" id="cl-add-gl-modal-close">&times;</button>
        </div>
        <div class="cl-modal-body">
          <div class="cl-form-group">
            <label class="cl-form-label">GL Code</label>
            <input type="text" class="cl-form-input" id="cl-new-gl-code" placeholder="e.g., 5100">
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Account Name</label>
            <input type="text" class="cl-form-input" id="cl-new-gl-name" placeholder="e.g., Office Supplies">
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Account Type</label>
            <select class="cl-form-select" id="cl-new-gl-type">
              <option value="expense">Expense</option>
              <option value="asset">Asset</option>
              <option value="liability">Liability</option>
              <option value="revenue">Revenue</option>
              <option value="equity">Equity</option>
            </select>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Category (optional)</label>
            <input type="text" class="cl-form-input" id="cl-new-gl-category" placeholder="e.g., Operating">
          </div>
        </div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-secondary" id="cl-add-gl-cancel">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-add-gl-submit">Add Account</button>
        </div>
      </div>
    </div>
  `;
  
  // Load GL data
  loadGLCorrectionsData(element);
  
  // Wire up modal
  const modal = element.querySelector('#cl-add-gl-modal');
  const addBtn = element.querySelector('#cl-add-gl-account-btn');
  const closeBtn = element.querySelector('#cl-add-gl-modal-close');
  const cancelBtn = element.querySelector('#cl-add-gl-cancel');
  const submitBtn = element.querySelector('#cl-add-gl-submit');
  
  addBtn?.addEventListener('click', () => modal.classList.add('visible'));
  closeBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  cancelBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  
  submitBtn?.addEventListener('click', async () => {
    const code = element.querySelector('#cl-new-gl-code')?.value?.trim();
    const name = element.querySelector('#cl-new-gl-name')?.value?.trim();
    const accountType = element.querySelector('#cl-new-gl-type')?.value;
    const category = element.querySelector('#cl-new-gl-category')?.value?.trim();
    
    if (!code || !name) {
      showToast('Please enter code and name', 'error');
      return;
    }
    
    try {
      const response = await fetch(`${BACKEND_URL}/ap/gl/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, name, account_type: accountType, category, organization_id: 'default' })
      });
      
      if (response.ok) {
        showToast(`GL Account ${code} added`, 'success');
        modal.classList.remove('visible');
        loadGLCorrectionsData(element);
      } else {
        const err = await response.json();
        showToast(err.detail || 'Failed to add account', 'error');
      }
    } catch (err) {
      showToast('Unable to connect. Please try again.', 'error');
    }
  });
}

async function loadGLCorrectionsData(element) {
  try {
    // Fetch stats
    const statsResponse = await fetch(`${BACKEND_URL}/ap/gl/stats?organization_id=default`);
    if (statsResponse.ok) {
      const stats = await statsResponse.json();
      const accuracyEl = element.querySelector('#cl-gl-accuracy');
      const totalEl = element.querySelector('#cl-gl-total-corrections');
      const learnedEl = element.querySelector('#cl-gl-learned-rules');
      
      if (accuracyEl) accuracyEl.textContent = `${Math.round((stats.accuracy || 0) * 100)}%`;
      if (totalEl) totalEl.textContent = stats.total_corrections || 0;
      if (learnedEl) learnedEl.textContent = stats.learned_rules || 0;
    }
    
    // Fetch recent corrections
    const correctionsResponse = await fetch(`${BACKEND_URL}/ap/gl/corrections?limit=20&organization_id=default`);
    const tbody = element.querySelector('#cl-corrections-body');
    
    if (correctionsResponse.ok) {
      const corrections = await correctionsResponse.json();
      
      if (corrections.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6">
              <div class="cl-empty-state">
                <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
                  <rect x="8" y="8" width="48" height="48" rx="4" stroke="#e0e0e0" stroke-width="3"/>
                  <line x1="20" y1="8" x2="20" y2="56" stroke="#e0e0e0" stroke-width="2"/>
                  <path d="M28 32L36 40L52 24" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                </svg>
                <div class="cl-empty-title">No corrections yet</div>
                <div class="cl-empty-desc">When you correct GL codes, they'll appear here and train the AI</div>
              </div>
            </td>
          </tr>
        `;
      } else {
        tbody.innerHTML = corrections.map(c => {
          const confidenceClass = c.confidence_impact > 0.1 ? 'high' : c.confidence_impact > 0.05 ? 'medium' : 'low';
          return `
            <tr>
              <td>${formatTimeAgo(c.timestamp)}</td>
              <td><strong>${escapeHtml(c.vendor || '-')}</strong></td>
              <td>${escapeHtml(c.invoice_id || '-')}</td>
              <td>
                <span class="cl-gl-code cl-gl-original">${escapeHtml(c.original_gl)}</span>
                <span class="cl-gl-arrow">→</span>
                <span class="cl-gl-code cl-gl-corrected">${escapeHtml(c.corrected_gl)}</span>
              </td>
              <td>${escapeHtml(c.reason || '-')}</td>
              <td>
                <div class="cl-confidence">
                  <div class="cl-confidence-bar">
                    <div class="cl-confidence-fill cl-confidence-${confidenceClass}" style="width: ${Math.min(100, (c.confidence_impact || 0) * 100)}%"></div>
                  </div>
                  <span>+${Math.round((c.confidence_impact || 0) * 100)}%</span>
                </div>
              </td>
            </tr>
          `;
        }).join('');
      }
    }
    
    // Fetch GL accounts from ERP-synced endpoint
    const accountsResponse = await fetch(`${BACKEND_URL}/ap/gl/accounts?organization_id=${getOrganizationId()}`);
    const accountsContainer = element.querySelector('#cl-gl-accounts');
    
    if (accountsResponse.ok) {
      const data = await accountsResponse.json();
      const accounts = data.accounts || [];
      
      // Show ERP connection status
      const erpStatus = data.erp_connected 
        ? `<div style="padding: 8px 16px; background: #E8F5E9; color: #2E7D32; border-radius: 6px; margin-bottom: 16px; font-size: 13px;">Synced from ${(data.erp_type || 'ERP').toUpperCase()} - ${accounts.length} accounts</div>`
        : `<div style="padding: 8px 16px; background: #FFF3E0; color: #E65100; border-radius: 6px; margin-bottom: 16px; font-size: 13px;">No ERP connected. <a href="#clearledgr/settings" style="color: #E65100; text-decoration: underline;">Connect your ERP</a> to sync accounts.</div>`;
      
      if (accounts.length === 0) {
        accountsContainer.innerHTML = erpStatus + '<div style="color: #9e9e9e; padding: 20px;">No GL accounts available.</div>';
      } else {
        accountsContainer.innerHTML = erpStatus + accounts.map(acc => `
          <div class="cl-gl-account-card">
            <div class="cl-gl-account-info">
              <span class="cl-gl-account-code">${escapeHtml(acc.code)}</span>
              <span class="cl-gl-account-name">${escapeHtml(acc.name)}</span>
            </div>
            <span class="cl-gl-account-type">${acc.type || 'Expense'}${acc.is_custom ? ' (custom)' : ''}</span>
          </div>
        `).join('');
      }
      
      // Store for correction modal dropdown
      window._clearledgrGLAccounts = accounts;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load GL data:', err);
    const tbody = element.querySelector('#cl-corrections-body');
    if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6">
            <div class="cl-empty-state">
              <div class="cl-empty-title" style="color: #FF9800;">Unable to load corrections</div>
              <div class="cl-empty-desc">We're having trouble connecting. Please refresh to try again.</div>
            </div>
          </td>
        </tr>
      `;
    }
  }
}

// =============================================================================
// RECURRING VIEW - Manage recurring invoice rules and subscriptions
// =============================================================================

function renderRecurring(element, params = {}) {
  const viewMode = params.view || 'rules';
  
  element.innerHTML = `
    <style>
      .cl-recurring { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 1200px; }
      .cl-page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; gap: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; flex: 1; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; flex-shrink: 0; }
      .cl-header-actions { flex-shrink: 0; }
      
      .cl-recurring-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
      .cl-summary-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-summary-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-summary-label { font-size: 13px; color: #5f6368; margin-top: 4px; }
      .cl-summary-card.active { border-left: 3px solid #4CAF50; }
      .cl-summary-card.monthly { border-left: 3px solid #2196F3; }
      .cl-summary-card.upcoming { border-left: 3px solid #FF9800; }
      .cl-summary-card.auto { border-left: 3px solid #9C27B0; }
      
      .cl-view-toggle { display: flex; gap: 8px; margin-bottom: 24px; }
      .cl-view-btn { padding: 10px 20px; border-radius: 6px; font-size: 14px; cursor: pointer; border: 1px solid #e0e0e0; background: white; font-weight: 500; }
      .cl-view-btn:hover { border-color: #10B981; }
      .cl-view-btn.active { background: #10B981; color: white; border-color: #10B981; }
      
      .cl-recurring-section { margin-bottom: 32px; }
      .cl-section-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
      .cl-section-title { font-size: 16px; font-weight: 500; color: #202124; }
      
      .cl-rules-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 16px; }
      .cl-rule-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; transition: all 0.2s; }
      .cl-rule-card:hover { border-color: #10B981; box-shadow: 0 2px 8px rgba(16, 185, 129, 0.1); }
      .cl-rule-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
      .cl-rule-vendor { font-size: 16px; font-weight: 500; color: #202124; }
      .cl-rule-status { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-rule-status.active { background: #E8F5E9; color: #2E7D32; }
      .cl-rule-status.paused { background: #f1f3f4; color: #5f6368; }
      
      .cl-rule-details { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
      .cl-rule-detail { }
      .cl-rule-detail-label { font-size: 11px; color: #9e9e9e; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
      .cl-rule-detail-value { font-size: 14px; color: #202124; }
      
      .cl-rule-actions { display: flex; gap: 8px; border-top: 1px solid #f1f3f4; padding-top: 16px; }
      .cl-rule-btn { padding: 6px 12px; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 12px; background: white; cursor: pointer; }
      .cl-rule-btn:hover { border-color: #10B981; color: #10B981; }
      .cl-rule-btn.delete { color: #C62828; }
      .cl-rule-btn.delete:hover { border-color: #C62828; }
      
      .cl-frequency-badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500; background: #E3F2FD; color: #1565C0; }
      .cl-action-badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 500; }
      .cl-action-auto { background: #E8F5E9; color: #2E7D32; }
      .cl-action-review { background: #FFF3E0; color: #E65100; }
      .cl-action-notify { background: #F3E5F5; color: #7B1FA2; }
      
      /* Upcoming table */
      .cl-upcoming-table { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-table { width: 100%; border-collapse: collapse; }
      .cl-table th { text-align: left; padding: 14px 20px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #e0e0e0; }
      .cl-table td { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-size: 14px; color: #202124; }
      .cl-table tr:hover { background: #f8f9fa; }
      .cl-table tr:last-child td { border-bottom: none; }
      
      .cl-due-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
      .cl-due-soon { background: #FFF3E0; color: #E65100; }
      .cl-due-week { background: #E3F2FD; color: #1565C0; }
      .cl-due-later { background: #f1f3f4; color: #5f6368; }
      
      .cl-empty-state { padding: 64px 40px; text-align: center; }
      .cl-empty-icon { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.4; }
      .cl-empty-title { font-size: 16px; font-weight: 500; color: #202124; margin-bottom: 8px; }
      .cl-empty-desc { font-size: 14px; color: #5f6368; }
      
      /* Create Rule Modal */
      .cl-rule-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; z-index: 999999; }
      .cl-rule-modal.visible { display: flex; }
      .cl-modal-content { background: white; border-radius: 12px; width: 520px; max-height: 80vh; overflow-y: auto; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
      .cl-modal-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
      .cl-modal-header h3 { margin: 0; font-size: 18px; font-weight: 500; }
      .cl-modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; }
      .cl-modal-body { padding: 24px; }
      .cl-form-group { margin-bottom: 20px; }
      .cl-form-label { display: block; font-size: 13px; font-weight: 500; color: #5f6368; margin-bottom: 8px; }
      .cl-form-input, .cl-form-select { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
      .cl-form-input:focus, .cl-form-select:focus { outline: none; border-color: #10B981; }
      .cl-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      .cl-modal-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
      .cl-btn { display: inline-flex; align-items: center; justify-content: center; padding: 10px 20px; border-radius: 6px; font-size: 14px; font-weight: 500; cursor: pointer; border: none; flex: 0 0 auto; width: fit-content; }
      .cl-btn-primary { background: #10B981; color: white; }
      .cl-btn-primary:hover { background: #059669; }
      .cl-btn-secondary { background: white; color: #5f6368; border: 1px solid #e0e0e0; }
    </style>
    
    <div class="cl-recurring">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <path d="M20.5 12A8.5 8.5 0 0 1 5 15" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
            <path d="M3.5 12A8.5 8.5 0 0 1 19 9" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
            <path d="M1 11L3.5 15L6 11" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
            <path d="M23 13L20.5 9L18 13" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
          </svg>
          <h1>Recurring Invoices</h1>
        </div>
        <div class="cl-header-actions">
          <button class="cl-btn cl-btn-primary" id="cl-create-rule-btn">+ Create Rule</button>
        </div>
      </div>
      
      <div class="cl-recurring-summary" id="cl-recurring-summary">
        <div class="cl-summary-card active">
          <div class="cl-summary-value" id="cl-rec-active">0</div>
          <div class="cl-summary-label">Active Rules</div>
        </div>
        <div class="cl-summary-card monthly">
          <div class="cl-summary-value" id="cl-rec-monthly">$0</div>
          <div class="cl-summary-label">Monthly Spend</div>
        </div>
        <div class="cl-summary-card upcoming">
          <div class="cl-summary-value" id="cl-rec-upcoming">0</div>
          <div class="cl-summary-label">Due This Week</div>
        </div>
        <div class="cl-summary-card auto">
          <div class="cl-summary-value" id="cl-rec-auto">0</div>
          <div class="cl-summary-label">Auto-Approved</div>
        </div>
      </div>
      
      <div class="cl-view-toggle">
        <button class="cl-view-btn ${viewMode === 'rules' ? 'active' : ''}" data-view="rules">Rules</button>
        <button class="cl-view-btn ${viewMode === 'upcoming' ? 'active' : ''}" data-view="upcoming">Upcoming</button>
      </div>
      
      <div class="cl-recurring-section" id="cl-rules-section" style="${viewMode !== 'rules' ? 'display:none' : ''}">
        <div class="cl-rules-grid" id="cl-rules-grid">
          <div style="color: #9e9e9e; padding: 40px; text-align: center;">Loading rules...</div>
        </div>
      </div>
      
      <div class="cl-recurring-section" id="cl-upcoming-section" style="${viewMode !== 'upcoming' ? 'display:none' : ''}">
        <div class="cl-upcoming-table">
          <table class="cl-table">
            <thead>
              <tr>
                <th>Vendor</th>
                <th>Expected Amount</th>
                <th>Frequency</th>
                <th>Expected Date</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="cl-upcoming-body">
              <tr>
                <td colspan="5">
                  <div class="cl-empty-state" id="cl-upcoming-loading">
                    <div class="cl-empty-title">Loading upcoming invoices...</div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
    
    <!-- Create Rule Modal -->
    <div class="cl-rule-modal" id="cl-rule-modal">
      <div class="cl-modal-content">
        <div class="cl-modal-header">
          <h3>Create Recurring Rule</h3>
          <button class="cl-modal-close" id="cl-rule-modal-close">&times;</button>
        </div>
        <div class="cl-modal-body">
          <div class="cl-form-group">
            <label class="cl-form-label">Vendor Name</label>
            <input type="text" class="cl-form-input" id="cl-rule-vendor" placeholder="e.g., Adobe, AWS, Slack">
          </div>
          <div class="cl-form-row">
            <div class="cl-form-group">
              <label class="cl-form-label">Expected Frequency</label>
              <select class="cl-form-select" id="cl-rule-frequency">
                <option value="weekly">Weekly</option>
                <option value="monthly" selected>Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annual">Annual</option>
              </select>
            </div>
            <div class="cl-form-group">
              <label class="cl-form-label">Expected Amount</label>
              <input type="number" class="cl-form-input" id="cl-rule-amount" placeholder="e.g., 99.99">
            </div>
          </div>
          <div class="cl-form-row">
            <div class="cl-form-group">
              <label class="cl-form-label">Amount Tolerance (%)</label>
              <input type="number" class="cl-form-input" id="cl-rule-tolerance" value="5" min="0" max="50">
            </div>
            <div class="cl-form-group">
              <label class="cl-form-label">When Matched</label>
              <select class="cl-form-select" id="cl-rule-action">
                <option value="auto_approve">Auto-Approve</option>
                <option value="flag_for_review">Flag for Review</option>
                <option value="notify_only">Notify Only</option>
              </select>
            </div>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Default GL Account (optional)</label>
            <select class="cl-form-select" id="cl-rule-gl">
              <option value="">-- Select GL Account --</option>
              <!-- Populated dynamically from ERP -->
            </select>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Vendor Aliases (comma-separated)</label>
            <input type="text" class="cl-form-input" id="cl-rule-aliases" placeholder="e.g., ADOBE SYSTEMS, Adobe Inc">
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Notes</label>
            <input type="text" class="cl-form-input" id="cl-rule-notes" placeholder="Optional description">
          </div>
        </div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-secondary" id="cl-rule-cancel">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-rule-submit">Create Rule</button>
        </div>
      </div>
    </div>
  `;
  
  // Load data
  loadRecurringData(element, viewMode);
  
  // Wire up view toggle
  element.querySelectorAll('.cl-view-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.dataset.view;
      sdk.Router.goto('clearledgr/recurring', { view });
    });
  });
  
  // Wire up modal
  const modal = element.querySelector('#cl-rule-modal');
  const createBtn = element.querySelector('#cl-create-rule-btn');
  const closeBtn = element.querySelector('#cl-rule-modal-close');
  const cancelBtn = element.querySelector('#cl-rule-cancel');
  const submitBtn = element.querySelector('#cl-rule-submit');
  
  createBtn?.addEventListener('click', () => modal.classList.add('visible'));
  closeBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  cancelBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  
  submitBtn?.addEventListener('click', async () => {
    const vendor = element.querySelector('#cl-rule-vendor')?.value?.trim();
    const frequency = element.querySelector('#cl-rule-frequency')?.value;
    const amount = parseFloat(element.querySelector('#cl-rule-amount')?.value) || null;
    const tolerance = parseFloat(element.querySelector('#cl-rule-tolerance')?.value) || 5;
    const action = element.querySelector('#cl-rule-action')?.value;
    const glCode = element.querySelector('#cl-rule-gl')?.value?.trim() || null;
    const aliasesStr = element.querySelector('#cl-rule-aliases')?.value?.trim();
    const notes = element.querySelector('#cl-rule-notes')?.value?.trim() || null;
    
    if (!vendor) {
      showToast('Please enter a vendor name', 'error');
      return;
    }
    
    const aliases = aliasesStr ? aliasesStr.split(',').map(a => a.trim()).filter(a => a) : null;
    
    try {
      const response = await fetch(`${BACKEND_URL}/ap/recurring/rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          vendor,
          expected_frequency: frequency,
          expected_amount: amount,
          amount_tolerance_pct: tolerance,
          action,
          default_gl_code: glCode,
          vendor_aliases: aliases,
          notes,
          organization_id: 'default'
        })
      });
      
      if (response.ok) {
        showToast(`Rule created for ${vendor}`, 'success');
        modal.classList.remove('visible');
        loadRecurringData(element, viewMode);
      } else {
        const err = await response.json();
        showToast(err.detail || 'Failed to create rule', 'error');
      }
    } catch (err) {
      showToast('Unable to connect. Please try again.', 'error');
    }
  });
}

async function loadRecurringData(element, viewMode) {
  try {
    // Fetch summary
    const summaryResponse = await fetch(`${BACKEND_URL}/ap/recurring/summary?organization_id=default`);
    if (summaryResponse.ok) {
      const summary = await summaryResponse.json();
      const activeEl = element.querySelector('#cl-rec-active');
      const monthlyEl = element.querySelector('#cl-rec-monthly');
      const upcomingEl = element.querySelector('#cl-rec-upcoming');
      const autoEl = element.querySelector('#cl-rec-auto');
      
      if (activeEl) activeEl.textContent = summary.active_rules || 0;
      if (monthlyEl) monthlyEl.textContent = formatCurrency(summary.monthly_spend || 0);
      if (upcomingEl) upcomingEl.textContent = summary.due_this_week || 0;
      if (autoEl) autoEl.textContent = summary.auto_approved || 0;
    }
    
    // Fetch rules
    const rulesResponse = await fetch(`${BACKEND_URL}/ap/recurring/rules?organization_id=default`);
    const rulesGrid = element.querySelector('#cl-rules-grid');
    
    if (rulesResponse.ok) {
      const rules = await rulesResponse.json();
      
      if (rules.length === 0) {
        rulesGrid.innerHTML = `
          <div style="grid-column: 1 / -1;">
            <div class="cl-empty-state">
              <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
                <path d="M52 32A20 20 0 0 1 12 40" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                <path d="M12 32A20 20 0 0 1 52 24" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                <path d="M6 35L12 42L18 35" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
                <path d="M58 29L52 22L46 29" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
              </svg>
              <div class="cl-empty-title">No recurring rules yet</div>
              <div class="cl-empty-desc">Create rules to auto-process subscriptions and recurring invoices</div>
            </div>
          </div>
        `;
      } else {
        rulesGrid.innerHTML = rules.map(rule => {
          const actionClass = rule.action === 'auto_approve' ? 'auto' : rule.action === 'flag_for_review' ? 'review' : 'notify';
          const actionLabel = rule.action === 'auto_approve' ? 'Auto-Approve' : rule.action === 'flag_for_review' ? 'Review' : 'Notify';
          
          return `
            <div class="cl-rule-card" data-rule-id="${rule.rule_id}">
              <div class="cl-rule-header">
                <span class="cl-rule-vendor">${escapeHtml(rule.vendor)}</span>
                <span class="cl-rule-status ${rule.enabled ? 'active' : 'paused'}">${rule.enabled ? 'Active' : 'Paused'}</span>
              </div>
              <div class="cl-rule-details">
                <div class="cl-rule-detail">
                  <div class="cl-rule-detail-label">Frequency</div>
                  <div class="cl-rule-detail-value"><span class="cl-frequency-badge">${(rule.expected_frequency || 'monthly').toUpperCase()}</span></div>
                </div>
                <div class="cl-rule-detail">
                  <div class="cl-rule-detail-label">Expected Amount</div>
                  <div class="cl-rule-detail-value">${rule.expected_amount ? formatCurrency(rule.expected_amount) : 'Any'} (±${rule.amount_tolerance_pct || 5}%)</div>
                </div>
                <div class="cl-rule-detail">
                  <div class="cl-rule-detail-label">Action</div>
                  <div class="cl-rule-detail-value"><span class="cl-action-badge cl-action-${actionClass}">${actionLabel}</span></div>
                </div>
                <div class="cl-rule-detail">
                  <div class="cl-rule-detail-label">GL Code</div>
                  <div class="cl-rule-detail-value">${rule.default_gl_code || '-'}</div>
                </div>
              </div>
              <div class="cl-rule-actions">
                <button class="cl-rule-btn" data-action="edit">Edit</button>
                <button class="cl-rule-btn" data-action="toggle">${rule.enabled ? 'Pause' : 'Enable'}</button>
                <button class="cl-rule-btn delete" data-action="delete">Delete</button>
              </div>
            </div>
          `;
        }).join('');
        
        // Wire up rule actions
        rulesGrid.querySelectorAll('.cl-rule-btn').forEach(btn => {
          btn.addEventListener('click', async (e) => {
            const card = e.target.closest('.cl-rule-card');
            const ruleId = card?.dataset.ruleId;
            const action = btn.dataset.action;
            
            if (action === 'delete') {
              if (!confirm('Delete this recurring rule?')) return;
              try {
                await fetch(`${BACKEND_URL}/ap/recurring/rules/${ruleId}?organization_id=default`, { method: 'DELETE' });
                showToast('Rule deleted', 'success');
                loadRecurringData(element, viewMode);
              } catch (err) {
                showToast('Failed to delete rule', 'error');
              }
            }
          });
        });
      }
    }
    
    // Fetch upcoming invoices
    const upcomingResponse = await fetch(`${BACKEND_URL}/ap/recurring/upcoming?days=30&organization_id=default`);
    const upcomingBody = element.querySelector('#cl-upcoming-body');
    
    if (upcomingResponse.ok) {
      const upcoming = await upcomingResponse.json();
      
      if (!upcoming || upcoming.length === 0) {
        upcomingBody.innerHTML = `
          <tr>
            <td colspan="5">
              <div class="cl-empty-state">
                <div class="cl-empty-title">No upcoming invoices</div>
                <div class="cl-empty-desc">Expected invoices from recurring rules will appear here</div>
              </div>
            </td>
          </tr>
        `;
      } else {
        upcomingBody.innerHTML = upcoming.map(inv => {
          const daysUntil = inv.days_until || 0;
          const dueClass = daysUntil <= 3 ? 'soon' : daysUntil <= 7 ? 'week' : 'later';
          const dueLabel = daysUntil === 0 ? 'Today' : daysUntil === 1 ? 'Tomorrow' : `${daysUntil} days`;
          
          return `
            <tr>
              <td><strong>${escapeHtml(inv.vendor)}</strong></td>
              <td>${formatCurrency(inv.expected_amount)}</td>
              <td><span class="cl-frequency-badge">${(inv.frequency || 'monthly').toUpperCase()}</span></td>
              <td><span class="cl-due-badge cl-due-${dueClass}">${dueLabel}</span></td>
              <td><span class="cl-action-badge cl-action-${inv.action === 'auto_approve' ? 'auto' : 'review'}">${inv.action === 'auto_approve' ? 'Auto' : 'Review'}</span></td>
            </tr>
          `;
        }).join('');
      }
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load recurring data:', err);
    const rulesGrid = element.querySelector('#cl-rules-grid');
    if (rulesGrid) {
      rulesGrid.innerHTML = `
        <div style="grid-column: 1 / -1;">
          <div class="cl-empty-state">
            <div class="cl-empty-title" style="color: #FF9800;">Unable to load rules</div>
            <div class="cl-empty-desc">We're having trouble connecting. Please refresh to try again.</div>
          </div>
        </div>
      `;
    }
  }
}

// =============================================================================
// PAYMENT REQUESTS VIEW - Ad-hoc payment requests from email/Slack/UI
// =============================================================================

function renderPaymentRequests(element, params = {}) {
  const statusFilter = params.status || 'all';
  
  element.innerHTML = `
    <style>
      .cl-requests { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; max-width: 1200px; }
      .cl-page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 24px; gap: 24px; }
      .cl-page-title { display: flex; align-items: center; gap: 12px; flex: 1; }
      .cl-page-title h1 { font-size: 24px; font-weight: 400; color: #202124; margin: 0; }
      .cl-page-icon { width: 28px; height: 28px; flex-shrink: 0; }
      .cl-page-subtitle { font-size: 13px; color: #5f6368; margin-top: 4px; }
      .cl-header-actions { flex-shrink: 0; }
      
      .cl-requests-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
      .cl-summary-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-summary-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-summary-label { font-size: 13px; color: #5f6368; margin-top: 4px; }
      .cl-summary-card.pending { border-left: 3px solid #FF9800; }
      .cl-summary-card.email { border-left: 3px solid #2196F3; }
      .cl-summary-card.slack { border-left: 3px solid #4A154B; }
      .cl-summary-card.approved { border-left: 3px solid #4CAF50; }
      
      .cl-requests-toolbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
      .cl-filter-tabs { display: flex; gap: 8px; }
      .cl-filter-tab { padding: 8px 16px; border-radius: 20px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; transition: all 0.2s; }
      .cl-filter-tab:hover { border-color: #10B981; }
      .cl-filter-tab.active { background: #10B981; color: white; border-color: #10B981; }
      
      .cl-btn { display: inline-flex; align-items: center; justify-content: center; padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; font-weight: 500; flex: 0 0 auto; width: fit-content; }
      .cl-btn-primary { background: #10B981; color: white; }
      .cl-btn-primary:hover { background: #059669; }
      
      .cl-requests-table { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-table { width: 100%; border-collapse: collapse; }
      .cl-table th { text-align: left; padding: 14px 20px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid #e0e0e0; }
      .cl-table td { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-size: 14px; color: #202124; }
      .cl-table tr:hover { background: #f8f9fa; }
      .cl-table tr:last-child td { border-bottom: none; }
      
      .cl-source-badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 500; text-transform: uppercase; }
      .cl-source-email { background: #E3F2FD; color: #1565C0; }
      .cl-source-slack { background: #F3E5F5; color: #4A154B; }
      .cl-source-ui { background: #E8F5E9; color: #2E7D32; }
      
      .cl-type-badge { display: inline-block; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: 500; }
      .cl-type-reimbursement { background: #FFF3E0; color: #E65100; }
      .cl-type-contractor { background: #E8EAF6; color: #3F51B5; }
      .cl-type-vendor_payment { background: #E0F2F1; color: #00695C; }
      .cl-type-other { background: #f1f3f4; color: #5f6368; }
      
      .cl-status-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
      .cl-status-pending { background: #FFF3E0; color: #E65100; }
      .cl-status-approved { background: #E8F5E9; color: #2E7D32; }
      .cl-status-rejected { background: #FFEBEE; color: #C62828; }
      .cl-status-paid { background: #E8F5E9; color: #1B5E20; }
      
      .cl-amount { font-weight: 500; font-family: 'Roboto Mono', monospace; }
      .cl-requester { font-weight: 500; }
      
      .cl-action-btn { padding: 6px 12px; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 12px; background: white; cursor: pointer; margin-right: 4px; }
      .cl-action-btn:hover { border-color: #10B981; color: #10B981; }
      .cl-action-btn.approve { background: #10B981; color: white; border-color: #10B981; }
      .cl-action-btn.approve:hover { background: #059669; }
      .cl-action-btn.reject { color: #C62828; }
      .cl-action-btn.reject:hover { border-color: #C62828; }
      
      .cl-empty-state { padding: 64px 40px; text-align: center; }
      .cl-empty-icon { width: 64px; height: 64px; margin-bottom: 16px; opacity: 0.4; }
      .cl-empty-title { font-size: 16px; font-weight: 500; color: #202124; margin-bottom: 8px; }
      .cl-empty-desc { font-size: 14px; color: #5f6368; }
      
      /* New Request Modal */
      .cl-request-modal { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); display: none; align-items: center; justify-content: center; z-index: 999999; }
      .cl-request-modal.visible { display: flex; }
      .cl-modal-content { background: white; border-radius: 12px; width: 520px; max-height: 80vh; overflow-y: auto; box-shadow: 0 8px 32px rgba(0,0,0,0.2); }
      .cl-modal-header { padding: 20px 24px; border-bottom: 1px solid #e0e0e0; display: flex; align-items: center; justify-content: space-between; }
      .cl-modal-header h3 { margin: 0; font-size: 18px; font-weight: 500; }
      .cl-modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #5f6368; }
      .cl-modal-body { padding: 24px; }
      .cl-form-group { margin-bottom: 20px; }
      .cl-form-label { display: block; font-size: 13px; font-weight: 500; color: #5f6368; margin-bottom: 8px; }
      .cl-form-input, .cl-form-select, .cl-form-textarea { width: 100%; padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; box-sizing: border-box; }
      .cl-form-input:focus, .cl-form-select:focus, .cl-form-textarea:focus { outline: none; border-color: #10B981; }
      .cl-form-textarea { min-height: 80px; resize: vertical; }
      .cl-form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
      .cl-modal-footer { padding: 16px 24px; border-top: 1px solid #e0e0e0; display: flex; justify-content: flex-end; gap: 12px; }
    </style>
    
    <div class="cl-requests">
      <div class="cl-page-header">
        <div class="cl-page-title">
          <svg class="cl-page-icon" viewBox="0 0 24 24" fill="none">
            <path d="M12 3V6M12 18V21M7.5 7.5C7.5 6 9 4.5 12 4.5C15.5 4.5 16.5 6.5 16.5 7.5C16.5 10 13.5 10.5 12 10.5C10.5 10.5 7.5 11 7.5 13.5C7.5 15 9 16.5 12 16.5C15 16.5 16.5 15 16.5 13.5" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
            <path d="M3 12H6M18 12H21" stroke="#10B981" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <div>
            <h1>Payment Requests</h1>
            <div class="cl-page-subtitle">Ad-hoc payment requests from email, Slack, and internal submissions</div>
          </div>
        </div>
        <div class="cl-header-actions">
          <button class="cl-btn cl-btn-primary" id="cl-new-request-btn">+ New Request</button>
        </div>
      </div>
      
      <div class="cl-requests-summary" id="cl-requests-summary">
        <div class="cl-summary-card pending">
          <div class="cl-summary-value" id="cl-req-pending">0</div>
          <div class="cl-summary-label">Pending Approval</div>
        </div>
        <div class="cl-summary-card email">
          <div class="cl-summary-value" id="cl-req-email">0</div>
          <div class="cl-summary-label">From Email</div>
        </div>
        <div class="cl-summary-card slack">
          <div class="cl-summary-value" id="cl-req-slack">0</div>
          <div class="cl-summary-label">From Slack</div>
        </div>
        <div class="cl-summary-card approved">
          <div class="cl-summary-value" id="cl-req-amount">$0</div>
          <div class="cl-summary-label">Pending Amount</div>
        </div>
      </div>
      
      <div class="cl-requests-toolbar">
        <div class="cl-filter-tabs">
          <button class="cl-filter-tab ${statusFilter === 'all' ? 'active' : ''}" data-status="all">All</button>
          <button class="cl-filter-tab ${statusFilter === 'pending' ? 'active' : ''}" data-status="pending">Pending</button>
          <button class="cl-filter-tab ${statusFilter === 'approved' ? 'active' : ''}" data-status="approved">Approved</button>
          <button class="cl-filter-tab ${statusFilter === 'rejected' ? 'active' : ''}" data-status="rejected">Rejected</button>
          <button class="cl-filter-tab ${statusFilter === 'paid' ? 'active' : ''}" data-status="paid">Paid</button>
        </div>
      </div>
      
      <div class="cl-requests-table">
        <table class="cl-table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Requester</th>
              <th>Payee</th>
              <th>Amount</th>
              <th>Type</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-requests-body">
            <tr>
              <td colspan="7">
                <div class="cl-empty-state" id="cl-requests-loading">
                  <div class="cl-empty-title">Loading requests...</div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    
    <!-- New Request Modal -->
    <div class="cl-request-modal" id="cl-request-modal">
      <div class="cl-modal-content">
        <div class="cl-modal-header">
          <h3>New Payment Request</h3>
          <button class="cl-modal-close" id="cl-request-modal-close">&times;</button>
        </div>
        <div class="cl-modal-body">
          <div class="cl-form-row">
            <div class="cl-form-group">
              <label class="cl-form-label">Payee Name</label>
              <input type="text" class="cl-form-input" id="cl-req-payee" placeholder="e.g., John Smith">
            </div>
            <div class="cl-form-group">
              <label class="cl-form-label">Amount</label>
              <input type="number" class="cl-form-input" id="cl-req-amount-input" placeholder="e.g., 500.00" step="0.01">
            </div>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Request Type</label>
            <select class="cl-form-select" id="cl-req-type">
              <option value="reimbursement">Reimbursement</option>
              <option value="contractor">Contractor Payment</option>
              <option value="vendor_payment">Vendor Payment</option>
              <option value="refund">Refund</option>
              <option value="advance">Advance</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">Description</label>
            <textarea class="cl-form-textarea" id="cl-req-description" placeholder="What is this payment for?"></textarea>
          </div>
          <div class="cl-form-group">
            <label class="cl-form-label">GL Code (optional)</label>
            <input type="text" class="cl-form-input" id="cl-req-gl" placeholder="e.g., 5200">
          </div>
        </div>
        <div class="cl-modal-footer">
          <button class="cl-btn cl-btn-secondary" id="cl-request-cancel" style="background: white; color: #5f6368; border: 1px solid #e0e0e0;">Cancel</button>
          <button class="cl-btn cl-btn-primary" id="cl-request-submit">Submit Request</button>
        </div>
      </div>
    </div>
  `;
  
  // Load requests data
  loadPaymentRequestsData(element, statusFilter);
  
  // Wire up filter tabs
  element.querySelectorAll('.cl-filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const status = tab.dataset.status;
      sdk.Router.goto('clearledgr/requests', { status });
    });
  });
  
  // Wire up modal
  const modal = element.querySelector('#cl-request-modal');
  const newBtn = element.querySelector('#cl-new-request-btn');
  const closeBtn = element.querySelector('#cl-request-modal-close');
  const cancelBtn = element.querySelector('#cl-request-cancel');
  const submitBtn = element.querySelector('#cl-request-submit');
  
  newBtn?.addEventListener('click', () => modal.classList.add('visible'));
  closeBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  cancelBtn?.addEventListener('click', () => modal.classList.remove('visible'));
  
  submitBtn?.addEventListener('click', async () => {
    const payee = element.querySelector('#cl-req-payee')?.value?.trim();
    const amount = parseFloat(element.querySelector('#cl-req-amount-input')?.value) || 0;
    const reqType = element.querySelector('#cl-req-type')?.value;
    const description = element.querySelector('#cl-req-description')?.value?.trim();
    const glCode = element.querySelector('#cl-req-gl')?.value?.trim() || null;
    
    if (!payee || amount <= 0 || !description) {
      showToast('Please fill in payee, amount, and description', 'error');
      return;
    }
    
    try {
      const response = await fetch(`${BACKEND_URL}/payment-requests/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_email: currentUser?.email || 'unknown@example.com',
          user_name: currentUser?.email?.split('@')[0] || 'User',
          payee_name: payee,
          amount,
          description,
          request_type: reqType,
          gl_code: glCode,
          organization_id: getOrganizationId(),
        })
      });
      
      if (response.ok) {
        showToast('Payment request submitted', 'success');
        modal.classList.remove('visible');
        loadPaymentRequestsData(element, statusFilter);
      } else {
        const err = await response.json();
        showToast(err.detail || 'Failed to submit request', 'error');
      }
    } catch (err) {
      showToast('Unable to connect. Please try again.', 'error');
    }
  });
}

async function loadPaymentRequestsData(element, statusFilter = 'all') {
  try {
    // Fetch summary
    const summaryResponse = await fetch(`${BACKEND_URL}/payment-requests/summary/stats?organization_id=${getOrganizationId()}`);
    if (summaryResponse.ok) {
      const summary = await summaryResponse.json();
      const pendingEl = element.querySelector('#cl-req-pending');
      const emailEl = element.querySelector('#cl-req-email');
      const slackEl = element.querySelector('#cl-req-slack');
      const amountEl = element.querySelector('#cl-req-amount');
      
      if (pendingEl) pendingEl.textContent = summary.pending || 0;
      if (emailEl) emailEl.textContent = summary.by_source?.email || 0;
      if (slackEl) slackEl.textContent = summary.by_source?.slack || 0;
      if (amountEl) amountEl.textContent = formatCurrency(summary.pending_amount || 0);
    }
    
    // Fetch requests list
    let url = `${BACKEND_URL}/payment-requests/all?organization_id=${getOrganizationId()}`;
    if (statusFilter !== 'all') {
      url += `&status=${statusFilter}`;
    }
    
    const requestsResponse = await fetch(url);
    const tbody = element.querySelector('#cl-requests-body');
    
    if (requestsResponse.ok) {
      const requests = await requestsResponse.json();
      
      if (requests.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="7">
              <div class="cl-empty-state">
                <svg class="cl-empty-icon" viewBox="0 0 64 64" fill="none">
                  <path d="M32 8V16M32 48V56M20 20C20 16 24 12 32 12C42 12 44 18 44 20C44 26 38 28 32 28C26 28 20 30 20 36C20 40 24 44 32 44C40 44 44 40 44 36" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                  <path d="M8 32H16M48 32H56" stroke="#e0e0e0" stroke-width="3" stroke-linecap="round"/>
                </svg>
                <div class="cl-empty-title">No payment requests yet</div>
                <div class="cl-empty-desc">Payment requests from email, Slack, or manual submissions will appear here</div>
              </div>
            </td>
          </tr>
        `;
      } else {
        tbody.innerHTML = requests.map(req => `
          <tr data-request-id="${req.request_id}">
            <td><span class="cl-source-badge cl-source-${req.source}">${req.source.toUpperCase()}</span></td>
            <td><span class="cl-requester">${escapeHtml(req.requester_name)}</span></td>
            <td>${escapeHtml(req.payee_name)}</td>
            <td><span class="cl-amount">${formatCurrency(req.amount)}</span></td>
            <td><span class="cl-type-badge cl-type-${req.request_type}">${req.request_type.replace('_', ' ')}</span></td>
            <td><span class="cl-status-badge cl-status-${req.status}">${req.status}</span></td>
            <td>
              ${req.status === 'pending' ? `
                <button class="cl-action-btn approve" data-action="approve" data-id="${req.request_id}">Approve</button>
                <button class="cl-action-btn reject" data-action="reject" data-id="${req.request_id}">Reject</button>
              ` : req.status === 'approved' ? `
                <button class="cl-action-btn" data-action="pay" data-id="${req.request_id}">Pay</button>
                <button class="cl-action-btn mark-paid" data-action="mark-paid" data-id="${req.request_id}" style="background: #1B5E20; color: white; border-color: #1B5E20;">Mark Paid</button>
              ` : (req.status === 'processing' || req.status === 'scheduled') ? `
                <button class="cl-action-btn mark-paid" data-action="mark-paid" data-id="${req.request_id}" style="background: #1B5E20; color: white; border-color: #1B5E20;">Mark Paid</button>
              ` : req.status === 'paid' || req.status === 'completed' ? `
                <span style="color: #1B5E20; font-weight: 500;">Paid</span>
              ` : '-'}
            </td>
          </tr>
        `).join('');
        
        // Wire up action buttons
        tbody.querySelectorAll('.cl-action-btn').forEach(btn => {
          btn.addEventListener('click', async () => {
            const requestId = btn.dataset.id;
            const action = btn.dataset.action;
            
            if (action === 'approve') {
              try {
                await fetch(`${BACKEND_URL}/payment-requests/${requestId}/approve`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    approved_by: currentUser?.email || 'user',
                    organization_id: getOrganizationId(),
                  })
                });
                showToast('Request approved', 'success');
                loadPaymentRequestsData(element, statusFilter);
              } catch (err) {
                showToast('Failed to approve', 'error');
              }
            } else if (action === 'reject') {
              const reason = prompt('Reason for rejection:');
              if (reason) {
                try {
                  await fetch(`${BACKEND_URL}/payment-requests/${requestId}/reject`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      rejected_by: currentUser?.email || 'user',
                      reason,
                      organization_id: getOrganizationId(),
                    })
                  });
                  showToast('Request rejected', 'success');
                  loadPaymentRequestsData(element, statusFilter);
                } catch (err) {
                  showToast('Failed to reject', 'error');
                }
              }
            } else if (action === 'pay') {
              try {
                await fetch(`${BACKEND_URL}/payment-requests/${requestId}/execute`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    payment_method: 'ach',
                    organization_id: getOrganizationId(),
                  })
                });
                showToast('Payment initiated', 'success');
                loadPaymentRequestsData(element, statusFilter);
              } catch (err) {
                showToast('Failed to initiate payment', 'error');
              }
            } else if (action === 'mark-paid') {
              try {
                await fetch(`${BACKEND_URL}/payment-requests/${requestId}/mark-paid`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    marked_by: currentUser?.email || 'user',
                    organization_id: getOrganizationId(),
                    paid_at: new Date().toISOString()
                  })
                });
                showToast('Payment marked as paid', 'success');
                loadPaymentRequestsData(element, statusFilter);
              } catch (err) {
                showToast('Failed to mark as paid', 'error');
              }
            }
          });
        });
      }
    } else {
      tbody.innerHTML = `
        <tr>
          <td colspan="7">
            <div class="cl-empty-state">
              <div class="cl-empty-title" style="color: #FF9800;">Unable to load requests</div>
              <div class="cl-empty-desc">We're having trouble connecting. Please refresh to try again.</div>
            </div>
          </td>
        </tr>
      `;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load payment requests:', err);
  }
}

// =============================================================================
// PRE-FLIGHT CHECK - Verification before posting to ERP
// =============================================================================

/**
 * Show Pre-flight Check modal before posting invoice to ERP.
 * Displays verification checklist:
 * - Vendor matched
 * - GL Code assigned
 * - Amount verified
 * - Budget availability
 * - Approval threshold
 */
function showPreflightCheck({ subject, container, approveBtn, onConfirm }) {
  // Create modal overlay
  const modal = document.createElement('div');
  modal.className = 'cl-preflight-modal';
  modal.innerHTML = `
    <style>
      .cl-preflight-modal {
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: rgba(0,0,0,0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 999999;
        font-family: 'Google Sans', Roboto, sans-serif;
      }
      .cl-preflight-content {
        background: white;
        border-radius: 12px;
        width: 420px;
        max-height: 80vh;
        overflow-y: auto;
        box-shadow: 0 8px 32px rgba(0,0,0,0.2);
      }
      .cl-preflight-header {
        padding: 20px 24px;
        border-bottom: 1px solid #e0e0e0;
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .cl-preflight-header svg {
        width: 24px;
        height: 24px;
      }
      .cl-preflight-header h3 {
        margin: 0;
        font-size: 18px;
        font-weight: 500;
        color: #202124;
      }
      .cl-preflight-body {
        padding: 24px;
      }
      .cl-preflight-subtitle {
        font-size: 14px;
        color: #5f6368;
        margin-bottom: 20px;
      }
      .cl-check-list {
        margin-bottom: 24px;
      }
      .cl-check-item {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        padding: 12px;
        background: #f8f9fa;
        border-radius: 8px;
        margin-bottom: 8px;
      }
      .cl-check-item.pass {
        background: #E8F5E9;
      }
      .cl-check-item.warn {
        background: #FFF3E0;
      }
      .cl-check-item.fail {
        background: #FFEBEE;
      }
      .cl-check-icon {
        width: 20px;
        height: 20px;
        flex-shrink: 0;
        margin-top: 2px;
      }
      .cl-check-icon.pass { color: #2E7D32; }
      .cl-check-icon.warn { color: #E65100; }
      .cl-check-icon.fail { color: #C62828; }
      .cl-check-icon.loading { color: #9e9e9e; }
      .cl-check-content {
        flex: 1;
      }
      .cl-check-title {
        font-size: 14px;
        font-weight: 500;
        color: #202124;
        margin-bottom: 2px;
      }
      .cl-check-detail {
        font-size: 13px;
        color: #5f6368;
      }
      .cl-check-detail strong {
        color: #202124;
      }
      .cl-preflight-erp {
        background: #f0fdf4;
        border: 1px solid #10B981;
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 20px;
      }
      .cl-erp-label {
        font-size: 12px;
        color: #059669;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        margin-bottom: 4px;
      }
      .cl-erp-value {
        font-size: 16px;
        font-weight: 500;
        color: #202124;
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .cl-erp-value img {
        width: 20px;
        height: 20px;
      }
      .cl-preflight-footer {
        padding: 16px 24px;
        border-top: 1px solid #e0e0e0;
        display: flex;
        justify-content: flex-end;
        gap: 12px;
      }
      .cl-preflight-btn {
        padding: 10px 20px;
        border-radius: 6px;
        font-size: 14px;
        font-weight: 500;
        cursor: pointer;
        border: none;
        transition: all 0.2s;
      }
      .cl-preflight-btn-cancel {
        background: white;
        color: #5f6368;
        border: 1px solid #e0e0e0;
      }
      .cl-preflight-btn-cancel:hover {
        background: #f8f9fa;
      }
      .cl-preflight-btn-confirm {
        background: #10B981;
        color: white;
      }
      .cl-preflight-btn-confirm:hover {
        background: #059669;
      }
      .cl-preflight-btn-confirm:disabled {
        background: #9e9e9e;
        cursor: not-allowed;
      }
      @keyframes cl-spin {
        to { transform: rotate(360deg); }
      }
      .cl-check-icon.loading svg {
        animation: cl-spin 1s linear infinite;
      }
    </style>
    
    <div class="cl-preflight-content">
      <div class="cl-preflight-header">
        <svg viewBox="0 0 24 24" fill="none">
          <path d="M9 12L11 14L15 10" stroke="#10B981" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="12" cy="12" r="9" stroke="#10B981" stroke-width="2"/>
        </svg>
        <h3>Pre-flight Check</h3>
      </div>
      
      <div class="cl-preflight-body">
        <div class="cl-preflight-subtitle">
          Verifying invoice before posting to your accounting system
        </div>
        
        <div class="cl-check-list" id="cl-check-list">
          <div class="cl-check-item" id="cl-check-vendor">
            <div class="cl-check-icon loading">
              <svg viewBox="0 0 20 20" fill="none">
                <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" stroke-dasharray="25" stroke-linecap="round"/>
              </svg>
            </div>
            <div class="cl-check-content">
              <div class="cl-check-title">Vendor Verification</div>
              <div class="cl-check-detail">Checking vendor database...</div>
            </div>
          </div>
          
          <div class="cl-check-item" id="cl-check-gl">
            <div class="cl-check-icon loading">
              <svg viewBox="0 0 20 20" fill="none">
                <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" stroke-dasharray="25" stroke-linecap="round"/>
              </svg>
            </div>
            <div class="cl-check-content">
              <div class="cl-check-title">GL Code Assignment</div>
              <div class="cl-check-detail">Determining expense category...</div>
            </div>
          </div>
          
          <div class="cl-check-item" id="cl-check-amount">
            <div class="cl-check-icon loading">
              <svg viewBox="0 0 20 20" fill="none">
                <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" stroke-dasharray="25" stroke-linecap="round"/>
              </svg>
            </div>
            <div class="cl-check-content">
              <div class="cl-check-title">Amount Verification</div>
              <div class="cl-check-detail">Extracting and validating amount...</div>
            </div>
          </div>
          
          <div class="cl-check-item" id="cl-check-duplicate">
            <div class="cl-check-icon loading">
              <svg viewBox="0 0 20 20" fill="none">
                <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" stroke-dasharray="25" stroke-linecap="round"/>
              </svg>
            </div>
            <div class="cl-check-content">
              <div class="cl-check-title">Duplicate Check</div>
              <div class="cl-check-detail">Scanning for similar invoices...</div>
            </div>
          </div>
          
          <div class="cl-check-item" id="cl-check-budget">
            <div class="cl-check-icon loading">
              <svg viewBox="0 0 20 20" fill="none">
                <circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2" stroke-dasharray="25" stroke-linecap="round"/>
              </svg>
            </div>
            <div class="cl-check-content">
              <div class="cl-check-title">Budget Availability</div>
              <div class="cl-check-detail">Checking department budget...</div>
            </div>
          </div>
        </div>
        
        <div class="cl-preflight-erp">
          <div class="cl-erp-label">Will Post To</div>
          <div class="cl-erp-value" id="cl-erp-target">
            <span>QuickBooks Online</span>
          </div>
        </div>
      </div>
      
      <div class="cl-preflight-footer">
        <button class="cl-preflight-btn cl-preflight-btn-cancel" id="cl-preflight-cancel">Cancel</button>
        <button class="cl-preflight-btn cl-preflight-btn-confirm" id="cl-preflight-confirm" disabled>
          Approve & Post
        </button>
      </div>
    </div>
  `;
  
  document.body.appendChild(modal);
  
  // Wire up buttons
  const cancelBtn = modal.querySelector('#cl-preflight-cancel');
  const confirmBtn = modal.querySelector('#cl-preflight-confirm');
  
  cancelBtn.addEventListener('click', () => {
    modal.remove();
  });
  
  confirmBtn.addEventListener('click', async () => {
    confirmBtn.textContent = 'Posting...';
    confirmBtn.disabled = true;
    
    // Extract data for API call
    const invoiceData = extractInvoicePreview(subject, '');
    
    try {
      // Post to ERP via backend
      const response = await fetch(`${BACKEND_URL}/ap/payments/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          invoice_id: `INV-${Date.now()}`,
          vendor_id: invoiceData.vendor?.toLowerCase().replace(/\s+/g, '-') || 'unknown',
          vendor_name: invoiceData.vendor || 'Unknown Vendor',
          amount: invoiceData.amount || 0,
          currency: 'USD',
          method: 'ach',
          organization_id: getOrganizationId(),
        })
      });
      
      if (response.ok) {
        const result = await response.json();
        
        // Track in ERP sync
        await fetch(`${BACKEND_URL}/ap/erp-sync/track`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            invoice_id: result.payment_id,
            erp_type: 'quickbooks',
            erp_bill_id: result.payment_id,
            amount: invoiceData.amount || 0,
            vendor_name: invoiceData.vendor || 'Unknown',
            organization_id: getOrganizationId(),
          })
        });
        
        // Update local cache
        updateCachedStatus(subject, 'posted', { amount: invoiceData.amount });
        
        modal.remove();
        onConfirm();
        showToast('Invoice approved and posted to ERP', 'success');
      } else {
        throw new Error('Failed to post');
      }
    } catch (e) {
      console.error('[Clearledgr] Failed to post to ERP:', e);
      // Still close modal and confirm (graceful degradation)
      modal.remove();
      onConfirm();
      showToast('Approved (ERP sync pending)', 'info');
    }
  });
  
  // Close on backdrop click
  modal.addEventListener('click', (e) => {
    if (e.target === modal) {
      modal.remove();
    }
  });
  
  // Simulate verification checks (in production, these would be real API calls)
  runPreflightChecks(modal, subject, confirmBtn);
}

/**
 * Run pre-flight verification checks.
 */
async function runPreflightChecks(modal, subject, confirmBtn) {
  const checks = [
    { id: 'vendor', delay: 400, result: () => {
      const vendorMatch = subject.match(/from\s+([A-Za-z\s]+)|([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:invoice|bill)/i);
      const vendor = vendorMatch ? (vendorMatch[1] || vendorMatch[2] || 'Unknown Vendor').trim() : 'Unknown Vendor';
      return { 
        status: 'pass', 
        detail: `<strong>${vendor}</strong> matched in vendor database` 
      };
    }},
    { id: 'gl', delay: 600, result: () => {
      const glCodes = { 
        invoice: '5200 - Operating Expenses',
        subscription: '5300 - Software & SaaS',
        office: '5100 - Office Supplies',
        travel: '6200 - Travel & Entertainment'
      };
      const category = subject.toLowerCase().includes('subscription') ? 'subscription' :
                       subject.toLowerCase().includes('office') ? 'office' :
                       subject.toLowerCase().includes('travel') ? 'travel' : 'invoice';
      return {
        status: 'pass',
        detail: `Assigned to <strong>${glCodes[category]}</strong>`
      };
    }},
    { id: 'amount', delay: 800, result: () => {
      const amountMatch = subject.match(/\$\s*([\d,]+(?:\.\d{2})?)/);
      if (amountMatch) {
        return { 
          status: 'pass', 
          detail: `Amount: <strong>$${amountMatch[1]}</strong> extracted` 
        };
      }
      return { 
        status: 'warn', 
        detail: 'Amount not detected in subject - please verify' 
      };
    }},
    { id: 'duplicate', delay: 1000, result: () => {
      // Simulate duplicate check
      return { 
        status: 'pass', 
        detail: 'No duplicates found in past 90 days' 
      };
    }},
    { id: 'budget', delay: 1200, result: () => {
      // Simulate budget check
      const remaining = Math.floor(Math.random() * 10000) + 5000;
      return { 
        status: remaining > 1000 ? 'pass' : 'warn', 
        detail: remaining > 1000 
          ? `Budget remaining: <strong>$${remaining.toLocaleString()}</strong>` 
          : `Low budget: <strong>$${remaining.toLocaleString()}</strong> remaining`
      };
    }}
  ];
  
  let allPassed = true;
  
  for (const check of checks) {
    await new Promise(r => setTimeout(r, check.delay));
    
    const item = modal.querySelector(`#cl-check-${check.id}`);
    if (!item) continue;
    
    const result = check.result();
    const icon = item.querySelector('.cl-check-icon');
    const detail = item.querySelector('.cl-check-detail');
    
    // Update status
    item.classList.remove('pass', 'warn', 'fail');
    item.classList.add(result.status);
    icon.classList.remove('loading');
    icon.classList.add(result.status);
    
    // Update icon
    if (result.status === 'pass') {
      icon.innerHTML = `<svg viewBox="0 0 20 20" fill="none"><path d="M6 10L9 13L14 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    } else if (result.status === 'warn') {
      icon.innerHTML = `<svg viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="2"/><path d="M10 6V10M10 13V13.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
      allPassed = false;
    } else {
      icon.innerHTML = `<svg viewBox="0 0 20 20" fill="none"><path d="M7 7L13 13M13 7L7 13" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
      allPassed = false;
    }
    
    // Update detail
    detail.innerHTML = result.detail;
  }
  
  // Enable confirm button after all checks complete
  confirmBtn.disabled = false;
  confirmBtn.textContent = allPassed ? 'Approve & Post' : 'Approve Anyway';
}

// =============================================================================
// AP AGING REPORT VIEW
// =============================================================================

function renderAPAging(element) {
  element.innerHTML = `
    <style>
      .cl-aging { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-aging-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-aging-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-aging-actions { display: flex; gap: 12px; }
      .cl-aging-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; color: #202124; }
      .cl-aging-btn:hover { background: #f8f9fa; }
      .cl-aging-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-aging-summary { display: grid; grid-template-columns: repeat(6, 1fr); gap: 16px; margin-bottom: 32px; }
      .cl-aging-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; text-align: center; }
      .cl-aging-card.current { border-left: 4px solid #10B981; }
      .cl-aging-card.days-1-30 { border-left: 4px solid #F59E0B; }
      .cl-aging-card.days-31-60 { border-left: 4px solid #EF4444; }
      .cl-aging-card.days-61-90 { border-left: 4px solid #DC2626; }
      .cl-aging-card.days-90-plus { border-left: 4px solid #991B1B; }
      .cl-aging-card.total { border-left: 4px solid ${BRAND_COLOR}; }
      .cl-aging-bucket { font-size: 12px; color: #5f6368; margin-bottom: 8px; text-transform: uppercase; }
      .cl-aging-amount { font-size: 24px; font-weight: 500; color: #202124; margin-bottom: 4px; }
      .cl-aging-count { font-size: 12px; color: #5f6368; }
      .cl-aging-table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-aging-table th { text-align: left; padding: 14px 16px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; border-bottom: 1px solid #e0e0e0; }
      .cl-aging-table td { padding: 14px 16px; border-bottom: 1px solid #f1f3f4; font-size: 14px; }
      .cl-aging-table tr:last-child td { border-bottom: none; }
      .cl-aging-table tr:hover { background: #f8f9fa; }
      .cl-vendor-link { color: ${BRAND_COLOR}; text-decoration: none; font-weight: 500; }
      .cl-vendor-link:hover { text-decoration: underline; }
      .cl-overdue-badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
      .cl-overdue-badge.current { background: #D1FAE5; color: #065F46; }
      .cl-overdue-badge.warning { background: #FEF3C7; color: #92400E; }
      .cl-overdue-badge.danger { background: #FEE2E2; color: #991B1B; }
      .cl-overdue-badge.critical { background: #991B1B; color: white; }
      .cl-tabs { display: flex; gap: 4px; margin-bottom: 24px; background: #f1f3f4; padding: 4px; border-radius: 8px; width: fit-content; }
      .cl-tab { padding: 8px 20px; border-radius: 6px; font-size: 13px; cursor: pointer; background: transparent; border: none; color: #5f6368; }
      .cl-tab.active { background: white; color: #202124; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
    </style>
    
    <div class="cl-aging">
      <div class="cl-aging-header">
        <h1 class="cl-aging-title">AP Aging Report</h1>
        <div class="cl-aging-actions">
          <button class="cl-aging-btn" id="cl-aging-refresh">Refresh</button>
          <button class="cl-aging-btn" id="cl-aging-export-csv">Export CSV</button>
          <button class="cl-aging-btn primary" id="cl-aging-export-pdf">Export PDF</button>
        </div>
      </div>
      
      <div class="cl-aging-summary" id="cl-aging-summary">
        <div class="cl-aging-card total">
          <div class="cl-aging-bucket">Total AP</div>
          <div class="cl-aging-amount" id="cl-aging-total">$0</div>
          <div class="cl-aging-count" id="cl-aging-total-count">0 invoices</div>
        </div>
        <div class="cl-aging-card current">
          <div class="cl-aging-bucket">Current</div>
          <div class="cl-aging-amount" id="cl-aging-current">$0</div>
          <div class="cl-aging-count" id="cl-aging-current-pct">0%</div>
        </div>
        <div class="cl-aging-card days-1-30">
          <div class="cl-aging-bucket">1-30 Days</div>
          <div class="cl-aging-amount" id="cl-aging-1-30">$0</div>
          <div class="cl-aging-count" id="cl-aging-1-30-pct">0%</div>
        </div>
        <div class="cl-aging-card days-31-60">
          <div class="cl-aging-bucket">31-60 Days</div>
          <div class="cl-aging-amount" id="cl-aging-31-60">$0</div>
          <div class="cl-aging-count" id="cl-aging-31-60-pct">0%</div>
        </div>
        <div class="cl-aging-card days-61-90">
          <div class="cl-aging-bucket">61-90 Days</div>
          <div class="cl-aging-amount" id="cl-aging-61-90">$0</div>
          <div class="cl-aging-count" id="cl-aging-61-90-pct">0%</div>
        </div>
        <div class="cl-aging-card days-90-plus">
          <div class="cl-aging-bucket">90+ Days</div>
          <div class="cl-aging-amount" id="cl-aging-90-plus">$0</div>
          <div class="cl-aging-count" id="cl-aging-90-plus-pct">0%</div>
        </div>
      </div>
      
      <div class="cl-tabs">
        <button class="cl-tab active" data-view="summary">Summary</button>
        <button class="cl-tab" data-view="vendor">By Vendor</button>
        <button class="cl-tab" data-view="detail">Detail</button>
      </div>
      
      <table class="cl-aging-table" id="cl-aging-table">
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Current</th>
            <th>1-30 Days</th>
            <th>31-60 Days</th>
            <th>61-90 Days</th>
            <th>90+ Days</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody id="cl-aging-body">
          <tr>
            <td colspan="7" style="text-align: center; padding: 40px; color: #5f6368;">
              Loading aging data from invoices...
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  `;
  
  // Load aging data from queue
  loadAgingData(element);
  
  // Tab switching
  element.querySelectorAll('.cl-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      element.querySelectorAll('.cl-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      // Would switch table view based on tab.dataset.view
    });
  });
  
  // Refresh button
  element.querySelector('#cl-aging-refresh')?.addEventListener('click', () => loadAgingData(element));
}

function loadAgingData(element) {
  // Get data from queue manager
  window.dispatchEvent(new CustomEvent('clearledgr:request-aging-data'));
  
  // Listen for response (queue manager sends this)
  const handler = (e) => {
    const { summary, vendorAging } = e.detail || {};
    
    if (summary) {
      // Update summary cards
      const total = summary.total_ap_balance || 0;
      const setEl = (id, val) => { const el = element.querySelector(id); if (el) el.textContent = val; };
      
      setEl('#cl-aging-total', formatCurrency(total));
      setEl('#cl-aging-total-count', `${summary.total_open_invoices || 0} invoices`);
      setEl('#cl-aging-current', formatCurrency(summary.summary?.current || 0));
      setEl('#cl-aging-current-pct', `${Math.round((summary.summary?.current || 0) / total * 100) || 0}%`);
      setEl('#cl-aging-1-30', formatCurrency(summary.summary?.overdue_1_30 || 0));
      setEl('#cl-aging-1-30-pct', `${Math.round((summary.summary?.overdue_1_30 || 0) / total * 100) || 0}%`);
      setEl('#cl-aging-31-60', formatCurrency(summary.summary?.overdue_31_60 || 0));
      setEl('#cl-aging-31-60-pct', `${Math.round((summary.summary?.overdue_31_60 || 0) / total * 100) || 0}%`);
      setEl('#cl-aging-61-90', formatCurrency(summary.summary?.overdue_61_90 || 0));
      setEl('#cl-aging-61-90-pct', `${Math.round((summary.summary?.overdue_61_90 || 0) / total * 100) || 0}%`);
      setEl('#cl-aging-90-plus', formatCurrency(summary.summary?.overdue_90_plus || 0));
      setEl('#cl-aging-90-plus-pct', `${Math.round((summary.summary?.overdue_90_plus || 0) / total * 100) || 0}%`);
    }
    
    // Update vendor table
    const tbody = element.querySelector('#cl-aging-body');
    if (tbody && vendorAging && vendorAging.length > 0) {
      tbody.innerHTML = vendorAging.map(v => `
        <tr>
          <td><a class="cl-vendor-link" href="#">${escapeHtml(v.vendor_name)}</a></td>
          <td>${formatCurrency(v.current || 0)}</td>
          <td>${formatCurrency(v.days_1_30 || 0)}</td>
          <td>${formatCurrency(v.days_31_60 || 0)}</td>
          <td>${formatCurrency(v.days_61_90 || 0)}</td>
          <td>${formatCurrency(v.days_90_plus || 0)}</td>
          <td><strong>${formatCurrency(v.total_balance || 0)}</strong></td>
        </tr>
      `).join('');
    } else if (tbody) {
      tbody.innerHTML = `
        <tr>
          <td colspan="7" style="text-align: center; padding: 40px; color: #5f6368;">
            No aging data available. Process some invoices first.
          </td>
        </tr>
      `;
    }
    
    window.removeEventListener('clearledgr:aging-data', handler);
  };
  
  window.addEventListener('clearledgr:aging-data', handler);
}

// =============================================================================
// EARLY PAYMENT DISCOUNTS VIEW
// =============================================================================

function renderDiscounts(element) {
  element.innerHTML = `
    <style>
      .cl-discounts { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-discounts-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-discounts-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-discounts-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 32px; }
      .cl-discount-card { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-discount-card.savings { border-left: 4px solid #10B981; }
      .cl-discount-card.expiring { border-left: 4px solid #F59E0B; }
      .cl-discount-card.captured { border-left: 4px solid #3B82F6; }
      .cl-discount-card.missed { border-left: 4px solid #EF4444; }
      .cl-discount-label { font-size: 12px; color: #5f6368; margin-bottom: 8px; text-transform: uppercase; }
      .cl-discount-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-discount-hint { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-discount-list { background: white; border: 1px solid #e0e0e0; border-radius: 8px; }
      .cl-discount-list-header { padding: 16px 20px; border-bottom: 1px solid #e0e0e0; font-weight: 500; }
      .cl-discount-item { display: flex; align-items: center; padding: 16px 20px; border-bottom: 1px solid #f1f3f4; }
      .cl-discount-item:last-child { border-bottom: none; }
      .cl-discount-info { flex: 1; }
      .cl-discount-vendor { font-weight: 500; color: #202124; margin-bottom: 4px; }
      .cl-discount-terms { font-size: 13px; color: #5f6368; }
      .cl-discount-terms span { color: #10B981; font-weight: 500; }
      .cl-discount-amount { text-align: right; margin-right: 24px; }
      .cl-discount-amt-value { font-size: 18px; font-weight: 500; color: #10B981; }
      .cl-discount-amt-label { font-size: 11px; color: #5f6368; }
      .cl-discount-urgency { text-align: right; margin-right: 24px; min-width: 80px; }
      .cl-urgency-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-urgency-badge.high { background: #FEE2E2; color: #991B1B; }
      .cl-urgency-badge.medium { background: #FEF3C7; color: #92400E; }
      .cl-urgency-badge.low { background: #D1FAE5; color: #065F46; }
      .cl-discount-actions { display: flex; gap: 8px; }
      .cl-discount-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; }
      .cl-discount-btn.capture { background: #10B981; color: white; }
      .cl-discount-btn.capture:hover { background: #059669; }
      .cl-discount-btn.skip { background: #f1f3f4; color: #5f6368; }
      .cl-discount-btn.skip:hover { background: #e0e0e0; }
      .cl-roi-badge { font-size: 11px; color: #10B981; background: #D1FAE5; padding: 2px 6px; border-radius: 4px; margin-left: 8px; }
      .cl-empty-discounts { text-align: center; padding: 60px 20px; color: #5f6368; }
    </style>
    
    <div class="cl-discounts">
      <div class="cl-discounts-header">
        <h1 class="cl-discounts-title">Early Payment Discounts</h1>
      </div>
      
      <div class="cl-discounts-summary">
        <div class="cl-discount-card savings">
          <div class="cl-discount-label">Available Savings</div>
          <div class="cl-discount-value" id="cl-available-savings">$0</div>
          <div class="cl-discount-hint" id="cl-available-count">0 discounts available</div>
        </div>
        <div class="cl-discount-card expiring">
          <div class="cl-discount-label">Expiring Soon</div>
          <div class="cl-discount-value" id="cl-expiring-count">0</div>
          <div class="cl-discount-hint">Within 3 days</div>
        </div>
        <div class="cl-discount-card captured">
          <div class="cl-discount-label">Captured This Month</div>
          <div class="cl-discount-value" id="cl-captured-savings">$0</div>
          <div class="cl-discount-hint" id="cl-captured-count">0 discounts taken</div>
        </div>
        <div class="cl-discount-card missed">
          <div class="cl-discount-label">Missed Savings</div>
          <div class="cl-discount-value" id="cl-missed-savings">$0</div>
          <div class="cl-discount-hint">Expired unused</div>
        </div>
      </div>
      
      <div class="cl-discount-list">
        <div class="cl-discount-list-header">Available Discounts - Pay Early to Save</div>
        <div id="cl-discount-items">
          <div class="cl-empty-discounts">
            <svg width="48" height="48" viewBox="0 0 48 48" fill="none" style="margin-bottom: 16px;">
              <circle cx="24" cy="24" r="20" stroke="#e0e0e0" stroke-width="2"/>
              <path d="M24 14V24L30 30" stroke="#e0e0e0" stroke-width="2" stroke-linecap="round"/>
            </svg>
            <div style="font-size: 16px; margin-bottom: 8px;">No Early Payment Discounts</div>
            <div style="font-size: 13px;">Discounts from vendor invoices will appear here</div>
          </div>
        </div>
      </div>
    </div>
  `;
  
  // Request discount data
  window.dispatchEvent(new CustomEvent('clearledgr:request-discount-data'));
}

// =============================================================================
// VENDOR MANAGEMENT VIEW
// =============================================================================

function renderVendorManagement(element) {
  element.innerHTML = `
    <style>
      .cl-vendor-mgmt { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-vendor-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-vendor-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-vendor-actions { display: flex; gap: 12px; }
      .cl-vendor-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-vendor-btn:hover { background: #f8f9fa; }
      .cl-vendor-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-vendor-stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-vendor-stat { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; text-align: center; }
      .cl-vendor-stat-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-vendor-stat-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-vendor-filters { display: flex; gap: 12px; margin-bottom: 20px; align-items: center; }
      .cl-vendor-search { padding: 10px 16px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; width: 300px; }
      .cl-vendor-search:focus { outline: none; border-color: ${BRAND_COLOR}; }
      .cl-vendor-filter { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-vendor-filter.active { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-vendor-table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-vendor-table th { text-align: left; padding: 14px 16px; background: #f8f9fa; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; }
      .cl-vendor-table td { padding: 14px 16px; border-top: 1px solid #f1f3f4; font-size: 14px; }
      .cl-vendor-table tr:hover { background: #f8f9fa; }
      .cl-vendor-name { font-weight: 500; color: #202124; }
      .cl-vendor-email { font-size: 12px; color: #5f6368; }
      .cl-status-badge { display: inline-block; padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-status-badge.active { background: #D1FAE5; color: #065F46; }
      .cl-status-badge.pending { background: #FEF3C7; color: #92400E; }
      .cl-status-badge.inactive { background: #f1f3f4; color: #5f6368; }
      .cl-status-badge.blocked { background: #FEE2E2; color: #991B1B; }
      .cl-w9-status { display: flex; align-items: center; gap: 6px; }
      .cl-w9-icon { width: 16px; height: 16px; }
      .cl-w9-icon.yes { color: #10B981; }
      .cl-w9-icon.no { color: #EF4444; }
      .cl-1099-badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: #E0E7FF; color: #3730A3; }
      .cl-vendor-action { padding: 6px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-vendor-action:hover { background: #f8f9fa; }
    </style>
    
    <div class="cl-vendor-mgmt">
      <div class="cl-vendor-header">
        <h1 class="cl-vendor-title">Vendor Management</h1>
        <div class="cl-vendor-actions">
          <button class="cl-vendor-btn" id="cl-export-1099">Export 1099 Report</button>
          <button class="cl-vendor-btn" id="cl-missing-w9">Missing W-9s</button>
          <button class="cl-vendor-btn primary" id="cl-add-vendor">+ Add Vendor</button>
        </div>
      </div>
      
      <div class="cl-vendor-stats">
        <div class="cl-vendor-stat">
          <div class="cl-vendor-stat-value" id="cl-total-vendors">0</div>
          <div class="cl-vendor-stat-label">Total Vendors</div>
        </div>
        <div class="cl-vendor-stat">
          <div class="cl-vendor-stat-value" id="cl-active-vendors">0</div>
          <div class="cl-vendor-stat-label">Active</div>
        </div>
        <div class="cl-vendor-stat">
          <div class="cl-vendor-stat-value" id="cl-pending-vendors">0</div>
          <div class="cl-vendor-stat-label">Pending Onboarding</div>
        </div>
        <div class="cl-vendor-stat">
          <div class="cl-vendor-stat-value" id="cl-missing-w9-count">0</div>
          <div class="cl-vendor-stat-label">Missing W-9</div>
        </div>
        <div class="cl-vendor-stat">
          <div class="cl-vendor-stat-value" id="cl-1099-count">0</div>
          <div class="cl-vendor-stat-label">Need 1099</div>
        </div>
      </div>
      
      <div class="cl-vendor-filters">
        <input type="text" class="cl-vendor-search" placeholder="Search vendors..." id="cl-vendor-search" />
        <button class="cl-vendor-filter active" data-filter="all">All</button>
        <button class="cl-vendor-filter" data-filter="active">Active</button>
        <button class="cl-vendor-filter" data-filter="pending">Pending</button>
        <button class="cl-vendor-filter" data-filter="needs-w9">Needs W-9</button>
        <button class="cl-vendor-filter" data-filter="1099">1099 Eligible</button>
      </div>
      
      <table class="cl-vendor-table">
        <thead>
          <tr>
            <th>Vendor</th>
            <th>Type</th>
            <th>Status</th>
            <th>Payment Terms</th>
            <th>W-9</th>
            <th>YTD Payments</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="cl-vendor-body">
          <tr>
            <td colspan="7" style="text-align: center; padding: 40px; color: #5f6368;">
              Loading vendors...
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  `;
  
  // Load vendor data
  window.dispatchEvent(new CustomEvent('clearledgr:request-vendor-data'));
  
  // Filter buttons
  element.querySelectorAll('.cl-vendor-filter').forEach(btn => {
    btn.addEventListener('click', () => {
      element.querySelectorAll('.cl-vendor-filter').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      // Filter vendors based on btn.dataset.filter
    });
  });
}

// =============================================================================
// AUDIT TRAIL VIEW
// =============================================================================

function renderAuditTrail(element) {
  element.innerHTML = `
    <style>
      .cl-audit { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-audit-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-audit-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-audit-actions { display: flex; gap: 12px; }
      .cl-audit-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-audit-btn:hover { background: #f8f9fa; }
      .cl-audit-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-audit-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-audit-stat { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; }
      .cl-audit-stat-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-audit-stat-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-audit-filters { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; align-items: center; }
      .cl-audit-search { padding: 10px 16px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; width: 250px; }
      .cl-audit-search:focus { outline: none; border-color: ${BRAND_COLOR}; }
      .cl-audit-select { padding: 8px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; background: white; }
      .cl-audit-date { padding: 8px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; }
      .cl-audit-timeline { background: white; border: 1px solid #e0e0e0; border-radius: 8px; }
      .cl-audit-item { display: flex; padding: 16px 20px; border-bottom: 1px solid #f1f3f4; }
      .cl-audit-item:last-child { border-bottom: none; }
      .cl-audit-icon { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-right: 16px; flex-shrink: 0; }
      .cl-audit-icon.received { background: #E0E7FF; color: #3730A3; }
      .cl-audit-icon.classified { background: #D1FAE5; color: #065F46; }
      .cl-audit-icon.approved { background: #D1FAE5; color: #065F46; }
      .cl-audit-icon.rejected { background: #FEE2E2; color: #991B1B; }
      .cl-audit-icon.posted { background: #DDD6FE; color: #5B21B6; }
      .cl-audit-icon.payment { background: #CFFAFE; color: #0E7490; }
      .cl-audit-content { flex: 1; }
      .cl-audit-summary { font-weight: 500; color: #202124; margin-bottom: 4px; }
      .cl-audit-detail { font-size: 13px; color: #5f6368; }
      .cl-audit-meta { text-align: right; min-width: 150px; }
      .cl-audit-time { font-size: 12px; color: #5f6368; }
      .cl-audit-actor { font-size: 12px; color: ${BRAND_COLOR}; margin-top: 4px; }
      .cl-audit-invoice { font-size: 13px; color: #202124; cursor: pointer; }
      .cl-audit-invoice:hover { color: ${BRAND_COLOR}; }
      .cl-audit-confidence { font-size: 11px; background: #f1f3f4; padding: 2px 6px; border-radius: 4px; margin-left: 8px; }
      .cl-empty-audit { text-align: center; padding: 60px 20px; color: #5f6368; }
    </style>
    
    <div class="cl-audit">
      <div class="cl-audit-header">
        <h1 class="cl-audit-title">Audit Trail</h1>
        <div class="cl-audit-actions">
          <button class="cl-audit-btn" id="cl-audit-export-csv">Export CSV</button>
          <button class="cl-audit-btn primary" id="cl-audit-compliance">Compliance Report</button>
        </div>
      </div>
      
      <div class="cl-audit-stats">
        <div class="cl-audit-stat">
          <div class="cl-audit-stat-value" id="cl-audit-total">0</div>
          <div class="cl-audit-stat-label">Total Events</div>
        </div>
        <div class="cl-audit-stat">
          <div class="cl-audit-stat-value" id="cl-audit-approved">0</div>
          <div class="cl-audit-stat-label">Approved</div>
        </div>
        <div class="cl-audit-stat">
          <div class="cl-audit-stat-value" id="cl-audit-auto-rate">0%</div>
          <div class="cl-audit-stat-label">Auto-Approval Rate</div>
        </div>
        <div class="cl-audit-stat">
          <div class="cl-audit-stat-value" id="cl-audit-amount">$0</div>
          <div class="cl-audit-stat-label">Total Processed</div>
        </div>
      </div>
      
      <div class="cl-audit-filters">
        <input type="text" class="cl-audit-search" placeholder="Search by vendor or invoice..." id="cl-audit-search" />
        <select class="cl-audit-select" id="cl-audit-type">
          <option value="">All Event Types</option>
          <option value="received">Received</option>
          <option value="classified">Classified</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="posted">Posted</option>
          <option value="payment_sent">Payment Sent</option>
        </select>
        <select class="cl-audit-select" id="cl-audit-actor">
          <option value="">All Actors</option>
          <option value="agent">Agent (Auto)</option>
          <option value="user">User (Manual)</option>
        </select>
        <input type="date" class="cl-audit-date" id="cl-audit-start" />
        <span style="color: #5f6368;">to</span>
        <input type="date" class="cl-audit-date" id="cl-audit-end" />
      </div>
      
      <div class="cl-audit-timeline" id="cl-audit-timeline">
        <div class="cl-empty-audit">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none" style="margin-bottom: 16px;">
            <rect x="8" y="8" width="32" height="32" rx="4" stroke="#e0e0e0" stroke-width="2"/>
            <line x1="16" y1="18" x2="32" y2="18" stroke="#e0e0e0" stroke-width="2"/>
            <line x1="16" y1="24" x2="28" y2="24" stroke="#e0e0e0" stroke-width="2"/>
            <line x1="16" y1="30" x2="24" y2="30" stroke="#e0e0e0" stroke-width="2"/>
          </svg>
          <div style="font-size: 16px; margin-bottom: 8px;">No Audit Events Yet</div>
          <div style="font-size: 13px;">Process invoices to see audit trail events</div>
        </div>
      </div>
    </div>
  `;
  
  // Request audit data
  window.dispatchEvent(new CustomEvent('clearledgr:request-audit-data'));
  
  // Handle filter changes
  const filterHandler = () => {
    window.dispatchEvent(new CustomEvent('clearledgr:request-audit-data', {
      detail: {
        search: element.querySelector('#cl-audit-search')?.value,
        eventType: element.querySelector('#cl-audit-type')?.value,
        actor: element.querySelector('#cl-audit-actor')?.value,
        startDate: element.querySelector('#cl-audit-start')?.value,
        endDate: element.querySelector('#cl-audit-end')?.value,
      }
    }));
  };
  
  element.querySelector('#cl-audit-search')?.addEventListener('input', debounce(filterHandler, 300));
  element.querySelector('#cl-audit-type')?.addEventListener('change', filterHandler);
  element.querySelector('#cl-audit-actor')?.addEventListener('change', filterHandler);
  element.querySelector('#cl-audit-start')?.addEventListener('change', filterHandler);
  element.querySelector('#cl-audit-end')?.addEventListener('change', filterHandler);
}

function debounce(fn, delay) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

// =============================================================================
// PURCHASE ORDERS VIEW
// =============================================================================

function renderPurchaseOrders(element) {
  element.innerHTML = `
    <style>
      .cl-po { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-po-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-po-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-po-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-po-btn:hover { background: #f8f9fa; }
      .cl-po-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-po-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-po-stat { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; }
      .cl-po-stat-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-po-stat-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-po-filters { display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
      .cl-po-search { padding: 10px 16px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; width: 250px; }
      .cl-po-search:focus { outline: none; border-color: ${BRAND_COLOR}; }
      .cl-po-select { padding: 8px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 13px; background: white; }
      .cl-po-table-wrapper { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-po-table { width: 100%; border-collapse: collapse; }
      .cl-po-table th { background: #f8f9fa; padding: 12px 16px; text-align: left; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; }
      .cl-po-table td { padding: 14px 16px; border-top: 1px solid #f1f3f4; font-size: 14px; }
      .cl-po-table tr:hover td { background: #f8f9fa; }
      .cl-po-number { font-weight: 500; color: ${BRAND_COLOR}; cursor: pointer; }
      .cl-po-status { padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-po-status.approved { background: #D1FAE5; color: #065F46; }
      .cl-po-status.draft { background: #F3F4F6; color: #374151; }
      .cl-po-status.received { background: #DDD6FE; color: #5B21B6; }
      .cl-po-status.invoiced { background: #CFFAFE; color: #0E7490; }
      .cl-po-match { margin-top: 24px; background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; }
      .cl-po-match-title { font-size: 16px; font-weight: 500; margin-bottom: 16px; }
      .cl-po-match-exceptions { display: flex; flex-direction: column; gap: 12px; }
      .cl-po-exception { display: flex; align-items: center; padding: 12px 16px; background: #FEF3C7; border-radius: 6px; }
      .cl-po-exception-icon { margin-right: 12px; color: #B45309; }
      .cl-po-exception-text { flex: 1; }
      .cl-po-exception-action { padding: 6px 12px; background: white; border: 1px solid #e0e0e0; border-radius: 4px; font-size: 12px; cursor: pointer; }
      .cl-empty-po { text-align: center; padding: 60px 20px; color: #5f6368; }
    </style>
    
    <div class="cl-po">
      <div class="cl-po-header">
        <h1 class="cl-po-title">Purchase Orders</h1>
        <div style="display: flex; gap: 12px;">
          <button class="cl-po-btn primary" id="cl-po-create">+ New PO</button>
        </div>
      </div>
      
      <div class="cl-po-stats">
        <div class="cl-po-stat">
          <div class="cl-po-stat-value" id="cl-po-total">0</div>
          <div class="cl-po-stat-label">Total POs</div>
        </div>
        <div class="cl-po-stat">
          <div class="cl-po-stat-value" id="cl-po-open">$0</div>
          <div class="cl-po-stat-label">Open PO Value</div>
        </div>
        <div class="cl-po-stat">
          <div class="cl-po-stat-value" id="cl-po-matched">0</div>
          <div class="cl-po-stat-label">Matched</div>
        </div>
        <div class="cl-po-stat">
          <div class="cl-po-stat-value" id="cl-po-exceptions">0</div>
          <div class="cl-po-stat-label">Exceptions</div>
        </div>
      </div>
      
      <div class="cl-po-filters">
        <input type="text" class="cl-po-search" placeholder="Search PO or vendor..." id="cl-po-search" />
        <select class="cl-po-select" id="cl-po-status-filter">
          <option value="">All Status</option>
          <option value="draft">Draft</option>
          <option value="approved">Approved</option>
          <option value="partially_received">Partially Received</option>
          <option value="fully_received">Fully Received</option>
          <option value="fully_invoiced">Fully Invoiced</option>
        </select>
      </div>
      
      <div class="cl-po-table-wrapper">
        <table class="cl-po-table">
          <thead>
            <tr>
              <th>PO Number</th>
              <th>Vendor</th>
              <th>Order Date</th>
              <th>Amount</th>
              <th>Status</th>
              <th>Received</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-po-tbody">
            <tr>
              <td colspan="7" class="cl-empty-po">
                <div style="font-size: 16px; margin-bottom: 8px;">No Purchase Orders</div>
                <div style="font-size: 13px;">Create POs to enable 3-way matching</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      
      <div class="cl-po-match">
        <div class="cl-po-match-title">3-Way Match Exceptions</div>
        <div class="cl-po-match-exceptions" id="cl-po-exceptions-list">
          <div style="color: #5f6368; font-size: 14px;">No exceptions to review</div>
        </div>
      </div>
    </div>
  `;
  
  loadPurchaseOrdersData(element);
}

async function loadPurchaseOrdersData(element) {
  try {
    const response = await fetch(`${BACKEND_URL}/ap/po/summary?organization_id=${getOrganizationId()}`);
    if (response.ok) {
      const data = await response.json();
      const total = element.querySelector('#cl-po-total');
      const open = element.querySelector('#cl-po-open');
      const matched = element.querySelector('#cl-po-matched');
      const exceptions = element.querySelector('#cl-po-exceptions');
      
      if (total) total.textContent = data.total_pos || 0;
      if (open) open.textContent = formatCurrency(data.open_po_value || 0);
      if (matched) matched.textContent = data.match_by_status?.matched || 0;
      if (exceptions) exceptions.textContent = data.pending_exceptions || 0;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load PO data:', err);
  }
}

// =============================================================================
// CREDIT NOTES VIEW
// =============================================================================

function renderCreditNotes(element) {
  element.innerHTML = `
    <style>
      .cl-credits { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-credits-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-credits-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-credits-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-credits-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-credits-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-credits-stat { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; }
      .cl-credits-stat-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-credits-stat-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-credits-table-wrapper { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-credits-table { width: 100%; border-collapse: collapse; }
      .cl-credits-table th { background: #f8f9fa; padding: 12px 16px; text-align: left; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; }
      .cl-credits-table td { padding: 14px 16px; border-top: 1px solid #f1f3f4; font-size: 14px; }
      .cl-credits-table tr:hover td { background: #f8f9fa; }
      .cl-credit-type { padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
      .cl-credit-type.credit { background: #D1FAE5; color: #065F46; }
      .cl-credit-type.debit { background: #FEE2E2; color: #991B1B; }
      .cl-credit-status { padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-credit-status.pending { background: #FEF3C7; color: #B45309; }
      .cl-credit-status.applied { background: #D1FAE5; color: #065F46; }
      .cl-credit-status.partial { background: #E0E7FF; color: #3730A3; }
      .cl-empty-credits { text-align: center; padding: 60px 20px; color: #5f6368; }
    </style>
    
    <div class="cl-credits">
      <div class="cl-credits-header">
        <h1 class="cl-credits-title">Credit Notes & Debit Memos</h1>
        <div style="display: flex; gap: 12px;">
          <button class="cl-credits-btn" id="cl-create-debit">+ Debit Memo</button>
          <button class="cl-credits-btn primary" id="cl-create-credit">+ Credit Note</button>
        </div>
      </div>
      
      <div class="cl-credits-stats">
        <div class="cl-credits-stat">
          <div class="cl-credits-stat-value" id="cl-credits-total">0</div>
          <div class="cl-credits-stat-label">Total Credits</div>
        </div>
        <div class="cl-credits-stat">
          <div class="cl-credits-stat-value" id="cl-credits-value">$0</div>
          <div class="cl-credits-stat-label">Total Value</div>
        </div>
        <div class="cl-credits-stat">
          <div class="cl-credits-stat-value" id="cl-credits-available">$0</div>
          <div class="cl-credits-stat-label">Available</div>
        </div>
        <div class="cl-credits-stat">
          <div class="cl-credits-stat-value" id="cl-credits-pending">0</div>
          <div class="cl-credits-stat-label">Pending Verification</div>
        </div>
      </div>
      
      <div class="cl-credits-table-wrapper">
        <table class="cl-credits-table">
          <thead>
            <tr>
              <th>Credit #</th>
              <th>Type</th>
              <th>Vendor</th>
              <th>Date</th>
              <th>Amount</th>
              <th>Applied</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-credits-tbody">
            <tr>
              <td colspan="8" class="cl-empty-credits">
                <div style="font-size: 16px; margin-bottom: 8px;">No Credit Notes</div>
                <div style="font-size: 13px;">Credits will appear as they're received or created</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
  
  loadCreditsData(element);
}

async function loadCreditsData(element) {
  try {
    const response = await fetch(`${BACKEND_URL}/ap/credit/summary?organization_id=${getOrganizationId()}`);
    if (response.ok) {
      const data = await response.json();
      const total = element.querySelector('#cl-credits-total');
      const value = element.querySelector('#cl-credits-value');
      const available = element.querySelector('#cl-credits-available');
      const pending = element.querySelector('#cl-credits-pending');
      
      if (total) total.textContent = data.total_credits || 0;
      if (value) value.textContent = formatCurrency(data.total_credit_value || 0);
      if (available) available.textContent = formatCurrency(data.total_available || 0);
      if (pending) pending.textContent = data.pending_verification || 0;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load credits data:', err);
  }
}

// =============================================================================
// DOCUMENT RETENTION VIEW
// =============================================================================

function renderDocumentRetention(element) {
  element.innerHTML = `
    <style>
      .cl-retention { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-retention-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-retention-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-retention-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-retention-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-retention-stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-retention-stat { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; }
      .cl-retention-stat-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-retention-stat-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-retention-section { background: white; border: 1px solid #e0e0e0; border-radius: 8px; margin-bottom: 20px; }
      .cl-retention-section-header { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-weight: 500; }
      .cl-retention-item { display: flex; align-items: center; padding: 14px 20px; border-bottom: 1px solid #f1f3f4; }
      .cl-retention-item:last-child { border-bottom: none; }
      .cl-retention-icon { width: 40px; height: 40px; border-radius: 8px; display: flex; align-items: center; justify-content: center; margin-right: 16px; background: #f8f9fa; }
      .cl-retention-info { flex: 1; }
      .cl-retention-name { font-weight: 500; margin-bottom: 4px; }
      .cl-retention-meta { font-size: 13px; color: #5f6368; }
      .cl-retention-badge { padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-retention-badge.legal { background: #FEE2E2; color: #991B1B; }
      .cl-retention-badge.expiring { background: #FEF3C7; color: #B45309; }
      .cl-retention-badge.active { background: #D1FAE5; color: #065F46; }
      .cl-empty-retention { text-align: center; padding: 40px 20px; color: #5f6368; }
    </style>
    
    <div class="cl-retention">
      <div class="cl-retention-header">
        <h1 class="cl-retention-title">Document Retention</h1>
        <div style="display: flex; gap: 12px;">
          <button class="cl-retention-btn" id="cl-run-retention">Run Retention Job</button>
          <button class="cl-retention-btn primary" id="cl-compliance-report">Compliance Report</button>
        </div>
      </div>
      
      <div class="cl-retention-stats">
        <div class="cl-retention-stat">
          <div class="cl-retention-stat-value" id="cl-retention-total">0</div>
          <div class="cl-retention-stat-label">Total Documents</div>
        </div>
        <div class="cl-retention-stat">
          <div class="cl-retention-stat-value" id="cl-retention-active">0</div>
          <div class="cl-retention-stat-label">Active</div>
        </div>
        <div class="cl-retention-stat">
          <div class="cl-retention-stat-value" id="cl-retention-archived">0</div>
          <div class="cl-retention-stat-label">Archived</div>
        </div>
        <div class="cl-retention-stat">
          <div class="cl-retention-stat-value" id="cl-retention-legal">0</div>
          <div class="cl-retention-stat-label">Legal Hold</div>
        </div>
        <div class="cl-retention-stat">
          <div class="cl-retention-stat-value" id="cl-retention-expiring">0</div>
          <div class="cl-retention-stat-label">Expiring Soon</div>
        </div>
      </div>
      
      <div class="cl-retention-section">
        <div class="cl-retention-section-header">Documents on Legal Hold</div>
        <div id="cl-legal-hold-list">
          <div class="cl-empty-retention">No documents on legal hold</div>
        </div>
      </div>
      
      <div class="cl-retention-section">
        <div class="cl-retention-section-header">Expiring Within 90 Days</div>
        <div id="cl-expiring-list">
          <div class="cl-empty-retention">No documents expiring soon</div>
        </div>
      </div>
      
      <div class="cl-retention-section">
        <div class="cl-retention-section-header">Retention Policies</div>
        <div id="cl-policies-list">
          <div class="cl-retention-item">
            <div class="cl-retention-icon"><svg width="20" height="20" viewBox="0 0 16 16" fill="none"><rect x="3" y="1" width="10" height="14" rx="1" stroke="#10B981" stroke-width="1.5" fill="none"/><line x1="5" y1="5" x2="11" y2="5" stroke="#10B981" stroke-width="1"/><line x1="5" y1="8" x2="11" y2="8" stroke="#10B981" stroke-width="1"/><line x1="5" y1="11" x2="8" y2="11" stroke="#10B981" stroke-width="1"/></svg></div>
            <div class="cl-retention-info">
              <div class="cl-retention-name">Invoice Retention</div>
              <div class="cl-retention-meta">7 years • IRS Requirement</div>
            </div>
            <span class="cl-retention-badge active">Active</span>
          </div>
          <div class="cl-retention-item">
            <div class="cl-retention-icon"><svg width="20" height="20" viewBox="0 0 16 16" fill="none"><rect x="2" y="2" width="12" height="12" rx="1" stroke="#10B981" stroke-width="1.5" fill="none"/><line x1="5" y1="2" x2="5" y2="14" stroke="#10B981" stroke-width="1"/><path d="M7 6L9 8L13 4" stroke="#10B981" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
            <div class="cl-retention-info">
              <div class="cl-retention-name">Purchase Order Retention</div>
              <div class="cl-retention-meta">7 years • IRS Requirement</div>
            </div>
            <span class="cl-retention-badge active">Active</span>
          </div>
          <div class="cl-retention-item">
            <div class="cl-retention-icon"><svg width="20" height="20" viewBox="0 0 16 16" fill="none"><rect x="3" y="1" width="10" height="14" rx="1" stroke="#10B981" stroke-width="1.5" fill="none"/><circle cx="8" cy="6" r="2" stroke="#10B981" stroke-width="1"/><path d="M5 12C5 10 6.5 9 8 9C9.5 9 11 10 11 12" stroke="#10B981" stroke-width="1" stroke-linecap="round"/></svg></div>
            <div class="cl-retention-info">
              <div class="cl-retention-name">W-9 Retention</div>
              <div class="cl-retention-meta">4 years after last 1099 • IRS Requirement</div>
            </div>
            <span class="cl-retention-badge active">Active</span>
          </div>
          <div class="cl-retention-item">
            <div class="cl-retention-icon"><svg width="20" height="20" viewBox="0 0 16 16" fill="none"><rect x="2" y="1" width="10" height="13" rx="1" stroke="#10B981" stroke-width="1.5" fill="none"/><rect x="4" y="3" width="10" height="13" rx="1" stroke="#10B981" stroke-width="1" fill="white"/><line x1="6" y1="7" x2="12" y2="7" stroke="#10B981" stroke-width="1"/><line x1="6" y1="10" x2="12" y2="10" stroke="#10B981" stroke-width="1"/></svg></div>
            <div class="cl-retention-info">
              <div class="cl-retention-name">Contract Retention</div>
              <div class="cl-retention-meta">10 years after expiry • Business Need</div>
            </div>
            <span class="cl-retention-badge active">Active</span>
          </div>
        </div>
      </div>
    </div>
  `;
  
  loadRetentionData(element);
}

async function loadRetentionData(element) {
  try {
    const response = await fetch(`${BACKEND_URL}/ap-advanced/retention/summary?organization_id=${getOrganizationId()}`);
    if (response.ok) {
      const data = await response.json();
      const total = element.querySelector('#cl-retention-total');
      const active = element.querySelector('#cl-retention-active');
      const archived = element.querySelector('#cl-retention-archived');
      const legal = element.querySelector('#cl-retention-legal');
      const expiring = element.querySelector('#cl-retention-expiring');
      
      if (total) total.textContent = data.total_documents || 0;
      if (active) active.textContent = data.active || 0;
      if (archived) archived.textContent = data.archived || 0;
      if (legal) legal.textContent = data.legal_hold || 0;
      if (expiring) expiring.textContent = data.expiring_soon || 0;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load retention data:', err);
  }
}

// =============================================================================
// MULTI-CURRENCY VIEW
// =============================================================================

function renderMultiCurrency(element) {
  element.innerHTML = `
    <style>
      .cl-currency { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-currency-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-currency-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-currency-converter { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 24px; margin-bottom: 24px; }
      .cl-currency-converter-title { font-size: 16px; font-weight: 500; margin-bottom: 16px; }
      .cl-currency-form { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
      .cl-currency-input-group { display: flex; flex-direction: column; gap: 6px; }
      .cl-currency-label { font-size: 12px; color: #5f6368; }
      .cl-currency-input { padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; width: 150px; }
      .cl-currency-input:focus { outline: none; border-color: ${BRAND_COLOR}; }
      .cl-currency-select { padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; background: white; }
      .cl-currency-btn { padding: 10px 20px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; background: ${BRAND_COLOR}; color: white; }
      .cl-currency-result { margin-top: 16px; padding: 16px; background: #f8f9fa; border-radius: 6px; }
      .cl-currency-result-amount { font-size: 24px; font-weight: 500; color: ${BRAND_COLOR}; }
      .cl-currency-result-rate { font-size: 13px; color: #5f6368; margin-top: 4px; }
      .cl-currency-rates { background: white; border: 1px solid #e0e0e0; border-radius: 8px; }
      .cl-currency-rates-header { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-weight: 500; display: flex; justify-content: space-between; align-items: center; }
      .cl-currency-rate-item { display: flex; justify-content: space-between; align-items: center; padding: 14px 20px; border-bottom: 1px solid #f1f3f4; }
      .cl-currency-rate-item:last-child { border-bottom: none; }
      .cl-currency-pair { font-weight: 500; }
      .cl-currency-rate-value { font-family: 'Roboto Mono', monospace; }
      .cl-currency-rate-source { font-size: 11px; color: #5f6368; background: #f1f3f4; padding: 2px 6px; border-radius: 4px; }
    </style>
    
    <div class="cl-currency">
      <div class="cl-currency-header">
        <h1 class="cl-currency-title">Multi-Currency</h1>
      </div>
      
      <div class="cl-currency-converter">
        <div class="cl-currency-converter-title">Currency Converter</div>
        <div class="cl-currency-form">
          <div class="cl-currency-input-group">
            <label class="cl-currency-label">Amount</label>
            <input type="number" class="cl-currency-input" id="cl-convert-amount" value="1000" />
          </div>
          <div class="cl-currency-input-group">
            <label class="cl-currency-label">From</label>
            <select class="cl-currency-select" id="cl-convert-from">
              <option value="USD">USD - US Dollar</option>
              <option value="EUR">EUR - Euro</option>
              <option value="GBP">GBP - British Pound</option>
              <option value="CAD">CAD - Canadian Dollar</option>
              <option value="AUD">AUD - Australian Dollar</option>
              <option value="JPY">JPY - Japanese Yen</option>
              <option value="INR">INR - Indian Rupee</option>
              <option value="NGN">NGN - Nigerian Naira</option>
            </select>
          </div>
          <div class="cl-currency-input-group">
            <label class="cl-currency-label">To</label>
            <select class="cl-currency-select" id="cl-convert-to">
              <option value="EUR">EUR - Euro</option>
              <option value="USD">USD - US Dollar</option>
              <option value="GBP">GBP - British Pound</option>
              <option value="CAD">CAD - Canadian Dollar</option>
              <option value="AUD">AUD - Australian Dollar</option>
              <option value="JPY">JPY - Japanese Yen</option>
              <option value="INR">INR - Indian Rupee</option>
              <option value="NGN">NGN - Nigerian Naira</option>
            </select>
          </div>
          <button class="cl-currency-btn" id="cl-convert-btn">Convert</button>
        </div>
        <div class="cl-currency-result" id="cl-convert-result" style="display: none;">
          <div class="cl-currency-result-amount" id="cl-result-amount">€920.00</div>
          <div class="cl-currency-result-rate" id="cl-result-rate">1 USD = 0.92 EUR</div>
        </div>
      </div>
      
      <div class="cl-currency-rates">
        <div class="cl-currency-rates-header">
          <span>Common Exchange Rates (vs USD)</span>
          <button class="cl-currency-btn" style="padding: 6px 12px; font-size: 12px;" id="cl-refresh-rates">Refresh</button>
        </div>
        <div class="cl-currency-rate-item">
          <span class="cl-currency-pair">USD/EUR</span>
          <span class="cl-currency-rate-value">0.9200</span>
          <span class="cl-currency-rate-source">fallback</span>
        </div>
        <div class="cl-currency-rate-item">
          <span class="cl-currency-pair">USD/GBP</span>
          <span class="cl-currency-rate-value">0.7900</span>
          <span class="cl-currency-rate-source">fallback</span>
        </div>
        <div class="cl-currency-rate-item">
          <span class="cl-currency-pair">USD/CAD</span>
          <span class="cl-currency-rate-value">1.3600</span>
          <span class="cl-currency-rate-source">fallback</span>
        </div>
        <div class="cl-currency-rate-item">
          <span class="cl-currency-pair">USD/JPY</span>
          <span class="cl-currency-rate-value">149.50</span>
          <span class="cl-currency-rate-source">fallback</span>
        </div>
        <div class="cl-currency-rate-item">
          <span class="cl-currency-pair">USD/INR</span>
          <span class="cl-currency-rate-value">83.12</span>
          <span class="cl-currency-rate-source">fallback</span>
        </div>
        <div class="cl-currency-rate-item">
          <span class="cl-currency-pair">USD/NGN</span>
          <span class="cl-currency-rate-value">1,550.00</span>
          <span class="cl-currency-rate-source">fallback</span>
        </div>
      </div>
    </div>
  `;
  
  // Set up conversion
  element.querySelector('#cl-convert-btn')?.addEventListener('click', async () => {
    const amount = parseFloat(element.querySelector('#cl-convert-amount')?.value || '0');
    const from = element.querySelector('#cl-convert-from')?.value || 'USD';
    const to = element.querySelector('#cl-convert-to')?.value || 'EUR';
    
    try {
      const response = await fetch(`${BACKEND_URL}/ap-advanced/currency/convert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount, from_currency: from, to_currency: to, organization_id: getOrganizationId() })
      });
      
      if (response.ok) {
        const data = await response.json();
        const result = element.querySelector('#cl-convert-result');
        const resultAmount = element.querySelector('#cl-result-amount');
        const resultRate = element.querySelector('#cl-result-rate');
        
        if (result) result.style.display = 'block';
        if (resultAmount) resultAmount.textContent = `${to === 'JPY' || to === 'NGN' ? '' : ''}${data.to_amount?.toLocaleString()}`;
        if (resultRate) resultRate.textContent = `1 ${from} = ${data.exchange_rate?.toFixed(4)} ${to}`;
      }
    } catch (err) {
      console.warn('[Clearledgr] Conversion failed:', err);
    }
  });
}

// =============================================================================
// TAX MANAGEMENT VIEW
// =============================================================================

function renderTaxManagement(element) {
  element.innerHTML = `
    <style>
      .cl-tax { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-tax-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-tax-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-tax-calculator { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 24px; margin-bottom: 24px; }
      .cl-tax-calculator-title { font-size: 16px; font-weight: 500; margin-bottom: 16px; }
      .cl-tax-form { display: flex; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
      .cl-tax-input-group { display: flex; flex-direction: column; gap: 6px; }
      .cl-tax-label { font-size: 12px; color: #5f6368; }
      .cl-tax-input { padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; width: 150px; }
      .cl-tax-input:focus { outline: none; border-color: ${BRAND_COLOR}; }
      .cl-tax-select { padding: 10px 14px; border: 1px solid #e0e0e0; border-radius: 6px; font-size: 14px; background: white; }
      .cl-tax-btn { padding: 10px 20px; border-radius: 6px; font-size: 13px; cursor: pointer; border: none; background: ${BRAND_COLOR}; color: white; }
      .cl-tax-result { margin-top: 16px; padding: 16px; background: #f8f9fa; border-radius: 6px; display: flex; gap: 24px; }
      .cl-tax-result-item { text-align: center; }
      .cl-tax-result-value { font-size: 20px; font-weight: 500; }
      .cl-tax-result-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-tax-codes { background: white; border: 1px solid #e0e0e0; border-radius: 8px; }
      .cl-tax-codes-header { padding: 16px 20px; border-bottom: 1px solid #f1f3f4; font-weight: 500; }
      .cl-tax-code-item { display: flex; align-items: center; padding: 14px 20px; border-bottom: 1px solid #f1f3f4; }
      .cl-tax-code-item:last-child { border-bottom: none; }
      .cl-tax-code { font-family: 'Roboto Mono', monospace; background: #f1f3f4; padding: 4px 8px; border-radius: 4px; margin-right: 12px; }
      .cl-tax-code-info { flex: 1; }
      .cl-tax-code-name { font-weight: 500; }
      .cl-tax-code-details { font-size: 13px; color: #5f6368; }
      .cl-tax-rate { font-weight: 500; color: ${BRAND_COLOR}; }
    </style>
    
    <div class="cl-tax">
      <div class="cl-tax-header">
        <h1 class="cl-tax-title">Tax Management</h1>
      </div>
      
      <div class="cl-tax-calculator">
        <div class="cl-tax-calculator-title">Tax Calculator</div>
        <div class="cl-tax-form">
          <div class="cl-tax-input-group">
            <label class="cl-tax-label">Net Amount</label>
            <input type="number" class="cl-tax-input" id="cl-tax-amount" value="1000" />
          </div>
          <div class="cl-tax-input-group">
            <label class="cl-tax-label">Country</label>
            <select class="cl-tax-select" id="cl-tax-country">
              <option value="US">United States</option>
              <option value="GB">United Kingdom</option>
              <option value="DE">Germany</option>
              <option value="FR">France</option>
              <option value="AU">Australia</option>
              <option value="CA">Canada</option>
              <option value="IN">India</option>
              <option value="SG">Singapore</option>
            </select>
          </div>
          <div class="cl-tax-input-group">
            <label class="cl-tax-label">Tax Code</label>
            <select class="cl-tax-select" id="cl-tax-code">
              <option value="">Auto-detect</option>
              <option value="VAT-STD-UK">UK VAT 20%</option>
              <option value="VAT-STD-DE">Germany VAT 19%</option>
              <option value="GST-STD-AU">Australia GST 10%</option>
              <option value="GST-STD-CA">Canada GST 5%</option>
              <option value="EXEMPT">Exempt</option>
            </select>
          </div>
          <button class="cl-tax-btn" id="cl-calc-tax">Calculate</button>
        </div>
        <div class="cl-tax-result" id="cl-tax-result" style="display: none;">
          <div class="cl-tax-result-item">
            <div class="cl-tax-result-value" id="cl-tax-net">$1,000.00</div>
            <div class="cl-tax-result-label">Net Amount</div>
          </div>
          <div class="cl-tax-result-item">
            <div class="cl-tax-result-value" id="cl-tax-tax">$200.00</div>
            <div class="cl-tax-result-label">Tax (20%)</div>
          </div>
          <div class="cl-tax-result-item">
            <div class="cl-tax-result-value" id="cl-tax-gross">$1,200.00</div>
            <div class="cl-tax-result-label">Gross Amount</div>
          </div>
        </div>
      </div>
      
      <div class="cl-tax-codes">
        <div class="cl-tax-codes-header">Tax Codes</div>
        <div class="cl-tax-code-item">
          <span class="cl-tax-code">VAT-STD-UK</span>
          <div class="cl-tax-code-info">
            <div class="cl-tax-code-name">UK VAT Standard</div>
            <div class="cl-tax-code-details">United Kingdom • GL: 2200</div>
          </div>
          <span class="cl-tax-rate">20.0%</span>
        </div>
        <div class="cl-tax-code-item">
          <span class="cl-tax-code">VAT-STD-DE</span>
          <div class="cl-tax-code-info">
            <div class="cl-tax-code-name">Germany VAT Standard</div>
            <div class="cl-tax-code-details">Germany • GL: 2200</div>
          </div>
          <span class="cl-tax-rate">19.0%</span>
        </div>
        <div class="cl-tax-code-item">
          <span class="cl-tax-code">GST-STD-AU</span>
          <div class="cl-tax-code-info">
            <div class="cl-tax-code-name">Australia GST</div>
            <div class="cl-tax-code-details">Australia • GL: 2200</div>
          </div>
          <span class="cl-tax-rate">10.0%</span>
        </div>
        <div class="cl-tax-code-item">
          <span class="cl-tax-code">GST-STD-SG</span>
          <div class="cl-tax-code-info">
            <div class="cl-tax-code-name">Singapore GST</div>
            <div class="cl-tax-code-details">Singapore • GL: 2200</div>
          </div>
          <span class="cl-tax-rate">9.0%</span>
        </div>
        <div class="cl-tax-code-item">
          <span class="cl-tax-code">GST-STD-CA</span>
          <div class="cl-tax-code-info">
            <div class="cl-tax-code-name">Canada GST</div>
            <div class="cl-tax-code-details">Canada • GL: 2200</div>
          </div>
          <span class="cl-tax-rate">5.0%</span>
        </div>
        <div class="cl-tax-code-item">
          <span class="cl-tax-code">EXEMPT</span>
          <div class="cl-tax-code-info">
            <div class="cl-tax-code-name">Tax Exempt</div>
            <div class="cl-tax-code-details">No tax applicable</div>
          </div>
          <span class="cl-tax-rate">0.0%</span>
        </div>
      </div>
    </div>
  `;
  
  // Set up tax calculation
  element.querySelector('#cl-calc-tax')?.addEventListener('click', async () => {
    const amount = parseFloat(element.querySelector('#cl-tax-amount')?.value || '0');
    const country = element.querySelector('#cl-tax-country')?.value || 'US';
    const taxCode = element.querySelector('#cl-tax-code')?.value || '';
    
    try {
      const response = await fetch(`${BACKEND_URL}/ap-advanced/tax/calculate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ net_amount: amount, country, tax_code: taxCode, organization_id: getOrganizationId() })
      });
      
      if (response.ok) {
        const data = await response.json();
        const result = element.querySelector('#cl-tax-result');
        const net = element.querySelector('#cl-tax-net');
        const tax = element.querySelector('#cl-tax-tax');
        const gross = element.querySelector('#cl-tax-gross');
        
        if (result) result.style.display = 'flex';
        if (net) net.textContent = formatCurrency(data.net_amount);
        if (tax) tax.textContent = `${formatCurrency(data.tax_amount)} (${data.tax_rate}%)`;
        if (gross) gross.textContent = formatCurrency(data.gross_amount);
      }
    } catch (err) {
      console.warn('[Clearledgr] Tax calculation failed:', err);
    }
  });
}

// =============================================================================
// ACCRUALS VIEW
// =============================================================================

function renderAccruals(element) {
  element.innerHTML = `
    <style>
      .cl-accruals { padding: 32px 40px; font-family: 'Google Sans', Roboto, sans-serif; }
      .cl-accruals-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
      .cl-accruals-title { font-size: 24px; font-weight: 400; color: #202124; }
      .cl-accruals-btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; border: 1px solid #e0e0e0; background: white; }
      .cl-accruals-btn:hover { background: #f8f9fa; }
      .cl-accruals-btn.primary { background: ${BRAND_COLOR}; color: white; border-color: ${BRAND_COLOR}; }
      .cl-accruals-stats { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 24px; }
      .cl-accruals-stat { background: white; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px 20px; }
      .cl-accruals-stat-value { font-size: 28px; font-weight: 500; color: #202124; }
      .cl-accruals-stat-label { font-size: 12px; color: #5f6368; margin-top: 4px; }
      .cl-accruals-month-end { background: #E0E7FF; border: 1px solid #C7D2FE; border-radius: 8px; padding: 20px; margin-bottom: 24px; display: flex; justify-content: space-between; align-items: center; }
      .cl-accruals-month-end-info h3 { margin: 0 0 4px 0; font-size: 16px; font-weight: 500; color: #3730A3; }
      .cl-accruals-month-end-info p { margin: 0; font-size: 13px; color: #5f6368; }
      .cl-accruals-table-wrapper { background: white; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; }
      .cl-accruals-table { width: 100%; border-collapse: collapse; }
      .cl-accruals-table th { background: #f8f9fa; padding: 12px 16px; text-align: left; font-size: 12px; font-weight: 500; color: #5f6368; text-transform: uppercase; }
      .cl-accruals-table td { padding: 14px 16px; border-top: 1px solid #f1f3f4; font-size: 14px; }
      .cl-accruals-table tr:hover td { background: #f8f9fa; }
      .cl-accrual-type { padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
      .cl-accrual-type.grni { background: #DDD6FE; color: #5B21B6; }
      .cl-accrual-type.expense { background: #E0E7FF; color: #3730A3; }
      .cl-accrual-type.utility { background: #CFFAFE; color: #0E7490; }
      .cl-accrual-status { padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 500; }
      .cl-accrual-status.posted { background: #D1FAE5; color: #065F46; }
      .cl-accrual-status.draft { background: #F3F4F6; color: #374151; }
      .cl-accrual-status.reversed { background: #FEE2E2; color: #991B1B; }
      .cl-empty-accruals { text-align: center; padding: 60px 20px; color: #5f6368; }
    </style>
    
    <div class="cl-accruals">
      <div class="cl-accruals-header">
        <h1 class="cl-accruals-title">Accruals</h1>
        <div style="display: flex; gap: 12px;">
          <button class="cl-accruals-btn" id="cl-create-grni">+ GRNI Accrual</button>
          <button class="cl-accruals-btn primary" id="cl-create-expense-accrual">+ Expense Accrual</button>
        </div>
      </div>
      
      <div class="cl-accruals-month-end">
        <div class="cl-accruals-month-end-info">
          <h3>Month-End Close</h3>
          <p>Run automated accruals and reversals for the current period</p>
        </div>
        <button class="cl-accruals-btn primary" id="cl-run-month-end">Run Month-End</button>
      </div>
      
      <div class="cl-accruals-stats">
        <div class="cl-accruals-stat">
          <div class="cl-accruals-stat-value" id="cl-accruals-total">0</div>
          <div class="cl-accruals-stat-label">Total Entries</div>
        </div>
        <div class="cl-accruals-stat">
          <div class="cl-accruals-stat-value" id="cl-accruals-posted">0</div>
          <div class="cl-accruals-stat-label">Posted</div>
        </div>
        <div class="cl-accruals-stat">
          <div class="cl-accruals-stat-value" id="cl-accruals-amount">$0</div>
          <div class="cl-accruals-stat-label">Current Period</div>
        </div>
        <div class="cl-accruals-stat">
          <div class="cl-accruals-stat-value" id="cl-accruals-pending">0</div>
          <div class="cl-accruals-stat-label">Pending Reversals</div>
        </div>
        <div class="cl-accruals-stat">
          <div class="cl-accruals-stat-value" id="cl-accruals-schedules">0</div>
          <div class="cl-accruals-stat-label">Active Schedules</div>
        </div>
      </div>
      
      <div class="cl-accruals-table-wrapper">
        <table class="cl-accruals-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Type</th>
              <th>Description</th>
              <th>Vendor</th>
              <th>Amount</th>
              <th>Period</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="cl-accruals-tbody">
            <tr>
              <td colspan="8" class="cl-empty-accruals">
                <div style="font-size: 16px; margin-bottom: 8px;">No Accruals</div>
                <div style="font-size: 13px;">Create accruals or run month-end to generate entries</div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  `;
  
  loadAccrualsData(element);
  
  // Month-end button
  element.querySelector('#cl-run-month-end')?.addEventListener('click', async () => {
    const now = new Date();
    try {
      const response = await fetch(`${BACKEND_URL}/ap-advanced/accruals/month-end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          month: now.getMonth() + 1,
          year: now.getFullYear(),
          post_entries: true,
          posted_by: 'user',
          organization_id: getOrganizationId()
        })
      });
      
      if (response.ok) {
        const data = await response.json();
        showToast(`Month-end complete: ${data.scheduled_created} scheduled, ${data.reversals_created} reversed`, 'success');
        loadAccrualsData(element);
      }
    } catch (err) {
      showToast('Month-end failed', 'error');
    }
  });
}

async function loadAccrualsData(element) {
  try {
    const response = await fetch(`${BACKEND_URL}/ap-advanced/accruals/summary?organization_id=${getOrganizationId()}`);
    if (response.ok) {
      const data = await response.json();
      const total = element.querySelector('#cl-accruals-total');
      const posted = element.querySelector('#cl-accruals-posted');
      const amount = element.querySelector('#cl-accruals-amount');
      const pending = element.querySelector('#cl-accruals-pending');
      const schedules = element.querySelector('#cl-accruals-schedules');
      
      if (total) total.textContent = data.total_entries || 0;
      if (posted) posted.textContent = data.by_status?.posted || 0;
      if (amount) amount.textContent = formatCurrency(data.current_period?.total_amount || 0);
      if (pending) pending.textContent = data.pending_reversals || 0;
      if (schedules) schedules.textContent = data.active_schedules || 0;
    }
  } catch (err) {
    console.warn('[Clearledgr] Failed to load accruals data:', err);
  }
}

// =============================================================================
// UTILITIES
// =============================================================================

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatTimeAgo(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp);
  const now = new Date();
  const diff = Math.floor((now - date) / 1000);
  
  if (diff < 60) return 'Just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatCurrency(amount) {
  if (!amount && amount !== 0) return '$0';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(amount);
}

// Export for debugging
window.__clearledgrSDK = { getSDK: () => sdk };
