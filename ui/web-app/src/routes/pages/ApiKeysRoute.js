import { h } from 'preact';
import htm from 'htm';
import ApiKeysPage from './ApiKeysPage.js';
import { usePageProps } from '../../shell/usePageProps.js';

const html = htm.bind(h);

export function ApiKeysRoute() {
  return html`<${ApiKeysPage} ...${usePageProps()} />`;
}
