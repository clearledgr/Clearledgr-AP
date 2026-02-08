/**
 * Clearledgr Retry Utility
 * 
 * Provides exponential backoff retry mechanism for API calls
 * with configurable options.
 */

const RetryConfig = {
  maxRetries: 3,
  baseDelay: 1000,  // 1 second
  maxDelay: 30000,  // 30 seconds
  backoffMultiplier: 2,
  retryableStatusCodes: [408, 429, 500, 502, 503, 504],
};

/**
 * Calculate delay with exponential backoff and jitter
 * @param {number} attempt - Current attempt number (0-indexed)
 * @param {number} baseDelay - Base delay in ms
 * @param {number} maxDelay - Maximum delay in ms
 * @returns {number} Delay in milliseconds
 */
function calculateBackoff(attempt, baseDelay = RetryConfig.baseDelay, maxDelay = RetryConfig.maxDelay) {
  const exponentialDelay = baseDelay * Math.pow(RetryConfig.backoffMultiplier, attempt);
  const jitter = Math.random() * 0.3 * exponentialDelay; // 0-30% jitter
  return Math.min(exponentialDelay + jitter, maxDelay);
}

/**
 * Check if an error is retryable
 * @param {Error|Response} error - Error or response to check
 * @returns {boolean}
 */
function isRetryable(error) {
  // Network errors are retryable
  if (error instanceof TypeError && error.message.includes('fetch')) {
    return true;
  }
  
  // Check status codes
  if (error.status && RetryConfig.retryableStatusCodes.includes(error.status)) {
    return true;
  }
  
  // Connection reset, timeout errors
  if (error.code === 'ECONNRESET' || error.code === 'ETIMEDOUT' || error.code === 'ENOTFOUND') {
    return true;
  }
  
  return false;
}

/**
 * Sleep for a specified duration
 * @param {number} ms - Duration in milliseconds
 * @returns {Promise<void>}
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Fetch with retry and exponential backoff
 * @param {string} url - URL to fetch
 * @param {RequestInit} options - Fetch options
 * @param {Object} retryOptions - Retry configuration overrides
 * @returns {Promise<Response>}
 */
async function fetchWithRetry(url, options = {}, retryOptions = {}) {
  const config = { ...RetryConfig, ...retryOptions };
  let lastError;

  for (let attempt = 0; attempt <= config.maxRetries; attempt++) {
    try {
      const response = await fetch(url, {
        ...options,
        signal: options.signal || AbortSignal.timeout(30000), // 30s default timeout
      });

      // Check if response indicates a retryable error
      if (!response.ok && config.retryableStatusCodes.includes(response.status)) {
        if (attempt < config.maxRetries) {
          const delay = calculateBackoff(attempt, config.baseDelay, config.maxDelay);
          console.log(`[Retry] Attempt ${attempt + 1} failed with status ${response.status}, retrying in ${Math.round(delay)}ms`);
          await sleep(delay);
          continue;
        }
      }

      return response;
    } catch (error) {
      lastError = error;
      
      // Check if we should retry
      if (attempt < config.maxRetries && isRetryable(error)) {
        const delay = calculateBackoff(attempt, config.baseDelay, config.maxDelay);
        console.log(`[Retry] Attempt ${attempt + 1} failed with error: ${error.message}, retrying in ${Math.round(delay)}ms`);
        await sleep(delay);
        continue;
      }

      // Don't retry for non-retryable errors
      if (!isRetryable(error)) {
        throw error;
      }
    }
  }

  // All retries exhausted
  throw lastError || new Error(`Request failed after ${config.maxRetries} retries`);
}

/**
 * Generic async function retry wrapper
 * @param {Function} fn - Async function to retry
 * @param {Object} retryOptions - Retry configuration
 * @returns {Promise<any>}
 */
async function withRetry(fn, retryOptions = {}) {
  const config = { ...RetryConfig, ...retryOptions };
  let lastError;

  for (let attempt = 0; attempt <= config.maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error) {
      lastError = error;
      
      if (attempt < config.maxRetries) {
        const delay = calculateBackoff(attempt, config.baseDelay, config.maxDelay);
        console.log(`[Retry] Attempt ${attempt + 1} failed, retrying in ${Math.round(delay)}ms`);
        await sleep(delay);
        continue;
      }
    }
  }

  throw lastError || new Error(`Function failed after ${config.maxRetries} retries`);
}

/**
 * Create a retry-enabled API client
 * @param {string} baseUrl - Base URL for API
 * @param {Object} defaultHeaders - Default headers for all requests
 * @returns {Object} API client with retry-enabled methods
 */
function createRetryClient(baseUrl, defaultHeaders = {}) {
  const makeRequest = async (method, path, body = null, options = {}) => {
    const url = `${baseUrl}${path}`;
    const requestOptions = {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...defaultHeaders,
        ...options.headers,
      },
    };

    if (body && method !== 'GET') {
      requestOptions.body = JSON.stringify(body);
    }

    const response = await fetchWithRetry(url, requestOptions, options.retry);
    
    if (!response.ok) {
      const errorBody = await response.text().catch(() => '');
      const error = new Error(`HTTP ${response.status}: ${errorBody || response.statusText}`);
      error.status = response.status;
      error.response = response;
      throw error;
    }

    const contentType = response.headers.get('content-type');
    if (contentType && contentType.includes('application/json')) {
      return response.json();
    }
    return response.text();
  };

  return {
    get: (path, options) => makeRequest('GET', path, null, options),
    post: (path, body, options) => makeRequest('POST', path, body, options),
    put: (path, body, options) => makeRequest('PUT', path, body, options),
    patch: (path, body, options) => makeRequest('PATCH', path, body, options),
    delete: (path, options) => makeRequest('DELETE', path, null, options),
  };
}

// Export for use in extension
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { fetchWithRetry, withRetry, createRetryClient, RetryConfig };
} else if (typeof window !== 'undefined') {
  window.ClearledgrRetry = { fetchWithRetry, withRetry, createRetryClient, RetryConfig };
}
