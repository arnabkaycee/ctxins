/**
 * WebSocket client for real-time presentation event streaming.
 * Features auto-reconnection with exponential backoff (1s, 2s, 5s).
 */
class WSClient {
  /**
   * @param {Object} options
   * @param {function(Object): void} options.onEvent - Callback when a UIEvent or SNAPSHOT arrives.
   * @param {function(string): void} options.onStatusChange - Callback when connection status changes ('connected' | 'reconnecting' | 'disconnected').
   */
  constructor({ onEvent, onStatusChange }) {
    this.onEvent = onEvent || (() => {});
    this.onStatusChange = onStatusChange || (() => {});
    this.ws = null;
    this.sessionId = null;
    this.reconnectTimer = null;
    this.backoffMs = 1000;
    this.maxBackoffMs = 5000;
    this.isExplicitlyClosed = false;
  }

  /**
   * Connect to WebSocket endpoint with optional session ID.
   * @param {string|null} sessionId
   */
  connect(sessionId = null) {
    this.isExplicitlyClosed = false;
    this.sessionId = sessionId;

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.ws) {
      try {
        this.ws.close();
      } catch (_) {}
      this.ws = null;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host || 'localhost:8484';
    const queryParam = this.sessionId ? `?session_id=${encodeURIComponent(this.sessionId)}` : '';
    const url = `${protocol}//${host}/ws/live${queryParam}`;

    console.log(`[WSClient] Connecting to ${url}...`);

    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.error('[WSClient] Connection initialization failed:', err);
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log('[WSClient] Connected.');
      this.backoffMs = 1000;
      this.onStatusChange('connected');
    };

    this.ws.onmessage = (messageEvent) => {
      try {
        const data = JSON.parse(messageEvent.data);
        this.onEvent(data);
      } catch (err) {
        console.error('[WSClient] Failed to parse message JSON:', err, messageEvent.data);
      }
    };

    this.ws.onclose = () => {
      console.warn('[WSClient] Connection closed.');
      if (!this.isExplicitlyClosed) {
        this.onStatusChange('disconnected');
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = (err) => {
      console.error('[WSClient] Error occurred:', err);
      try {
        this.ws.close();
      } catch (_) {}
    };
  }

  /**
   * Schedule automatic reconnection with exponential backoff capped at 5s.
   * @private
   */
  _scheduleReconnect() {
    if (this.isExplicitlyClosed || this.reconnectTimer) {
      return;
    }
    const delay = Math.min(this.backoffMs, this.maxBackoffMs);
    console.log(`[WSClient] Reconnecting in ${delay}ms...`);
    this.onStatusChange('reconnecting');

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      // Exponential backoff capped at 5000ms: 1000 -> 2000 -> 5000 -> 5000
      this.backoffMs = Math.min(this.backoffMs * 2, this.maxBackoffMs);
      this.connect(this.sessionId);
    }, delay);
  }

  /**
   * Switch the targeted session without dropping unhandled reconnect logic.
   * @param {string} sessionId
   */
  switchSession(sessionId) {
    this.sessionId = sessionId;
    this.connect(sessionId);
  }

  /**
   * Close connection and prevent further reconnect attempts.
   */
  close() {
    this.isExplicitlyClosed = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch (_) {}
      this.ws = null;
    }
    this.onStatusChange('disconnected');
  }
}

window.WSClient = WSClient;
