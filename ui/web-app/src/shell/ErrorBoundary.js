import { Component } from 'preact';
import { html } from '../utils/htm.js';

export class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    if (typeof window !== 'undefined' && window.console) {
      console.error('[ErrorBoundary]', error, info);
    }
  }

  render() {
    if (this.state.error) {
      return html`
        <div class="cl-error-boundary">
          <h2>Something went wrong on this page.</h2>
          <p class="cl-error-detail">${String(this.state.error?.message || this.state.error)}</p>
          <button onClick=${() => this.setState({ error: null })}>Try again</button>
        </div>
      `;
    }
    return this.props.children;
  }
}
