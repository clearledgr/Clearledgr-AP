import { beforeEach, describe, expect, it, vi } from 'vitest';
import { h } from 'preact';
import { render, screen, waitFor } from '@testing-library/preact';
import { Router } from 'wouter-preact';
import { LoginPage } from './LoginPage.js';
import { refreshSession, useSession } from './useSession.js';

vi.mock('./useSession.js', () => ({
  useSession: vi.fn(),
  refreshSession: vi.fn(),
}));

vi.mock('../api/client.js', () => ({
  api: vi.fn(),
  ApiError: class ApiError extends Error {
    constructor(status, payload) {
      super(payload?.detail || payload?.error || `HTTP ${status}`);
      this.status = status;
      this.payload = payload;
    }
  },
}));

function mountLogin() {
  return render(h(Router, {}, h(LoginPage, {})));
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSession.mockReturnValue({
      isAuthenticated: false,
      isLoading: false,
    });
    window.history.replaceState({}, '', '/login');
  });

  it('renders legal fineprint with readable spacing', () => {
    mountLogin();
    expect(document.body.textContent).toContain('Terms and Privacy Policy');
    expect(document.body.textContent).not.toContain('Termsand Privacy Policy');
  });

  it('surfaces OAuth callback errors and cleans the URL', async () => {
    window.history.replaceState({}, '', '/login?auth_error=access_denied&next=%2Frecords');
    mountLogin();

    await waitFor(() => {
      expect(screen.getByText('Sign-in was cancelled. Choose Google or Microsoft to try again.')).toBeTruthy();
    });

    expect(window.location.search).toBe('?next=%2Frecords');
    expect(refreshSession).not.toHaveBeenCalled();
  });

  it('refreshes the session after post-OAuth redirects', async () => {
    window.history.replaceState({}, '', '/login?post_oauth=1');
    mountLogin();

    await waitFor(() => {
      expect(refreshSession).toHaveBeenCalledTimes(1);
    });
  });
});
