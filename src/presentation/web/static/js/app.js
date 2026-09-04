/**
 * Main application coordinator for ctxins Web Dashboard.
 */
class DashboardApp {
  constructor() {
    this.activeSessionId = null;
    this.sessions = [];
    this.turns = [];
    this.violations = [];
    this.summary = null;
    this.selectedTurnIndex = 0;

    this.wsClient = null;
    this.charts = null;

    // DOM Elements
    this.sessionSelect = document.getElementById('session-select');
    this.statusPill = document.getElementById('connection-status');
    this.statusText = document.getElementById('status-text');
    this.exportBtn = document.getElementById('export-btn');

    // KPI Elements
    this.kpiTokens = document.getElementById('kpi-tokens');
    this.kpiCacheHit = document.getElementById('kpi-cache-hit');
    this.kpiSpend = document.getElementById('kpi-spend');
    this.kpiAvoidable = document.getElementById('kpi-avoidable');
    this.kpiPollutionScore = document.getElementById('kpi-pollution-score');
    this.pollutionMeterFill = document.getElementById('pollution-meter-fill');
    this.pollutionLevelText = document.getElementById('pollution-level-text');

    // Feeds & Tables
    this.recommendationsFeed = document.getElementById('recommendations-feed');
    this.recommendationsCount = document.getElementById('recommendations-count');
    this.turnTitle = document.getElementById('selected-turn-title');
    this.turnMetaRibbon = document.getElementById('turn-meta-ribbon');
    this.blocksTableBody = document.getElementById('blocks-table-body');

    // Diff Elements
    this.diffT1 = document.getElementById('diff-t1');
    this.diffT2 = document.getElementById('diff-t2');
    this.diffBtn = document.getElementById('diff-btn');
    this.diffResults = document.getElementById('diff-results');

    // Modal & JSON Viewer Elements
    this.modalOverlay = document.getElementById('block-modal');
    this.modalTitle = document.getElementById('modal-title');
    this.modalTypeBadge = document.getElementById('modal-type-badge');
    this.modalCloseBtn = document.getElementById('modal-close-btn');
    this.modalBody = document.getElementById('modal-body');
    this.modalTreeContainer = document.getElementById('modal-tree-container');
    this.modalRawContainer = document.getElementById('modal-raw-container');
    this.modalExpandAllBtn = document.getElementById('modal-expand-all-btn');
    this.modalCollapseAllBtn = document.getElementById('modal-collapse-all-btn');
    this.modalViewTreeBtn = document.getElementById('modal-view-tree-btn');
    this.modalViewRawBtn = document.getElementById('modal-view-raw-btn');
    this.modalCopyBtn = document.getElementById('modal-copy-btn');
    this.modalSearchInput = document.getElementById('modal-search-input');
    this.modalSearchMatches = document.getElementById('modal-search-matches');
    this.modalToolbar = document.getElementById('modal-toolbar');

    this.jsonViewer = null;
  }

  async init() {
    this._bindEvents();

    // Initialize Charts
    this.charts = new DashboardCharts('token-chart', (turnIndex) => {
      this.selectTurn(turnIndex);
    });

    // Initialize JSON Viewer
    if (typeof window.JsonViewer !== 'undefined') {
      this.jsonViewer = new JsonViewer({
        treeContainer: this.modalTreeContainer,
        rawContainer: this.modalRawContainer,
        toolbar: this.modalToolbar,
        expandAllBtn: this.modalExpandAllBtn,
        collapseAllBtn: this.modalCollapseAllBtn,
        viewTreeBtn: this.modalViewTreeBtn,
        viewRawBtn: this.modalViewRawBtn,
        copyBtn: this.modalCopyBtn,
        searchInput: this.modalSearchInput,
        searchMatches: this.modalSearchMatches,
        typeBadge: this.modalTypeBadge,
      });
    }

    // Initialize WebSocket client
    this.wsClient = new WSClient({
      onEvent: (event) => this.handleEvent(event),
      onStatusChange: (status) => this.updateConnectionStatus(status),
    });

    // Initial fetch of active sessions
    await this.refreshSessions();

    // Start live WebSocket stream
    this.wsClient.connect(this.activeSessionId);
  }

  _bindEvents() {
    if (this.sessionSelect) {
      this.sessionSelect.addEventListener('change', (e) => {
        const sid = e.target.value;
        if (sid) {
          this.switchSession(sid);
        }
      });
    }

    if (this.exportBtn) {
      this.exportBtn.addEventListener('click', () => this.exportSession());
    }

    if (this.diffBtn) {
      this.diffBtn.addEventListener('click', () => this.computeDiff());
    }

    if (this.modalCloseBtn) {
      this.modalCloseBtn.addEventListener('click', () => this.closeModal());
    }

    if (this.modalOverlay) {
      this.modalOverlay.addEventListener('click', (e) => {
        if (e.target === this.modalOverlay) {
          this.closeModal();
        }
      });
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.modalOverlay && this.modalOverlay.classList.contains('active')) {
        this.closeModal();
      }
    });
  }

  async refreshSessions() {
    try {
      const res = await fetch('/api/v1/sessions');
      if (res.ok) {
        this.sessions = await res.json();
        this._populateSessionSelect();
      }
    } catch (err) {
      console.warn('[DashboardApp] Could not fetch sessions list:', err);
    }
  }

  _populateSessionSelect() {
    if (!this.sessionSelect) return;
    const currentVal = this.sessionSelect.value;
    this.sessionSelect.innerHTML = '';

    if (!this.sessions || this.sessions.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No active sessions';
      this.sessionSelect.appendChild(opt);
      return;
    }

    this.sessions.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.sessionId;
      const model = s.model && s.model !== 'unknown' ? ` (${s.model})` : '';
      opt.textContent = `${s.sessionId}${model}`;
      this.sessionSelect.appendChild(opt);
    });

    if (currentVal && this.sessions.some((s) => s.sessionId === currentVal)) {
      this.sessionSelect.value = currentVal;
      this.activeSessionId = currentVal;
    } else {
      this.activeSessionId = this.sessions[0].sessionId;
      this.sessionSelect.value = this.activeSessionId;
    }
  }

  switchSession(sessionId) {
    if (this.activeSessionId === sessionId) return;
    this.activeSessionId = sessionId;
    if (this.sessionSelect) {
      this.sessionSelect.value = sessionId;
    }
    if (this.wsClient) {
      this.wsClient.switchSession(sessionId);
    }
    this.loadSessionREST(sessionId);
  }

  async loadSessionREST(sessionId) {
    try {
      const res = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
      if (res.ok) {
        const data = await res.json();
        this.summary = data.summary;
        this.turns = data.turns || [];
        this.violations = data.violations || [];
        this.renderAll();
      }
    } catch (err) {
      console.error('[DashboardApp] Failed to load session via REST:', err);
    }
  }

  handleEvent(event) {
    const type = event.type || (event.payload && event.payload.type);
    const sid = event.sessionId || (event.payload && event.payload.sessionId);

    // If active session was not yet set, adopt incoming session
    if (!this.activeSessionId && sid) {
      this.activeSessionId = sid;
      this.refreshSessions();
    }

    // Ignore events for other sessions if activeSessionId is set, except session_created
    if (this.activeSessionId && sid && this.activeSessionId !== sid) {
      if (type === 'session_created' || type === 'SESSION_CREATED') {
        this.refreshSessions();
      }
      return;
    }

    if (type === 'SNAPSHOT' || type === 'snapshot') {
      const payload = event.payload || event;
      if (event.sessionId) {
        this.activeSessionId = event.sessionId;
      }
      this.summary = payload.summary || null;
      this.turns = payload.turns || [];
      this.violations = payload.violations || [];
      this.renderAll();
      this.refreshSessions();
    } else if (type === 'turn_started' || type === 'TURN_STARTED') {
      const tNum = event.payload?.turn_index ?? event.payload?.turnIndex;
      if (this.statusText) {
        this.statusText.textContent = tNum !== undefined ? `Turn #${tNum} in progress...` : 'Turn in progress...';
      }
    } else if (type === 'turn_streaming' || type === 'TURN_STREAMING') {
      const tNum = event.payload?.turn_index ?? event.payload?.turnIndex;
      if (this.statusText) {
        this.statusText.textContent = tNum !== undefined ? `Turn #${tNum} streaming...` : 'Streaming tokens...';
      }
    } else if (type === 'turn_completed' || type === 'TURN_COMPLETED') {
      const payload = event.payload || {};
      const turnData = payload.turn || payload;
      const tIdx = turnData.turn_index !== undefined ? turnData.turn_index : turnData.turnIndex;
      if (tIdx !== undefined) {
        // Normalize fields for uniform internal access
        if (turnData.turn_index === undefined) turnData.turn_index = tIdx;
        if (turnData.input_tokens === undefined && turnData.inputTokens !== undefined) turnData.input_tokens = turnData.inputTokens;
        if (turnData.output_tokens === undefined && turnData.outputTokens !== undefined) turnData.output_tokens = turnData.outputTokens;
        if (turnData.cached_read_tokens === undefined && turnData.cachedReadTokens !== undefined) turnData.cached_read_tokens = turnData.cachedReadTokens;
        if (!turnData.all_blocks && turnData.blocks) turnData.all_blocks = turnData.blocks;

        const existingIdx = this.turns.findIndex(
          (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === tIdx
        );
        if (existingIdx >= 0) {
          this.turns[existingIdx] = turnData;
        } else {
          this.turns.push(turnData);
        }
      }
      if (turnData.violations) {
        turnData.violations.forEach((v) => this.violations.push(v));
      }
      if (payload.summary) {
        this.summary = payload.summary;
      }
      this.renderAll();
      this.refreshSessions();
      this.updateConnectionStatus('connected');
    } else if (type === 'violation_detected' || type === 'VIOLATION_DETECTED') {
      const violation = event.payload ? event.payload.violation || event.payload : null;
      if (violation) {
        this.violations.push(violation);
        this.renderRecommendations();
      }
    } else if (type === 'session_summary_updated' || type === 'SESSION_SUMMARY_UPDATED') {
      if (event.payload && event.payload.summary) {
        this.summary = event.payload.summary;
        this.renderKPIs();
      }
    } else if (type === 'session_created' || type === 'SESSION_CREATED') {
      this.refreshSessions();
    }
  }

  updateConnectionStatus(status) {
    if (!this.statusPill || !this.statusText) return;
    this.statusPill.className = `status-pill ${status}`;
    if (status === 'connected') {
      this.statusText.textContent = 'Live Connected';
    } else if (status === 'reconnecting') {
      this.statusText.textContent = 'Reconnecting...';
    } else {
      this.statusText.textContent = 'Disconnected';
    }
  }

  renderAll() {
    this.renderKPIs();
    if (this.charts) {
      this.charts.updateData(this.turns);
    }
    this.renderRecommendations();
    this._populateDiffSelects();

    // Select latest turn if none or selected out of bounds
    if (this.turns.length > 0) {
      const lastTurn = this.turns[this.turns.length - 1];
      const defaultIdx =
        lastTurn.turn_index !== undefined
          ? lastTurn.turn_index
          : lastTurn.turnIndex !== undefined
          ? lastTurn.turnIndex
          : this.turns.length - 1;
      const valid = this.turns.some(
        (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
      );
      this.selectTurn(valid ? this.selectedTurnIndex : defaultIdx);
    } else {
      this.renderEmptyTurnInspector();
    }
  }

  renderKPIs() {
    const s = this.summary || {};

    const totalInput = s.totalInputTokens ?? s.total_input_tokens ?? 0;
    const totalOutput = s.totalOutputTokens ?? s.total_output_tokens ?? 0;
    const totalTokens = totalInput + totalOutput;
    if (this.kpiTokens) this.kpiTokens.textContent = totalTokens.toLocaleString();

    const hitRatio = s.cacheHitRatio ?? s.cache_hit_ratio ?? 0;
    const hitPct = Math.round(hitRatio * 1000) / 10;
    if (this.kpiCacheHit) this.kpiCacheHit.textContent = `${hitPct}%`;

    const spend = Number(s.estimatedCostUSD ?? s.estimated_cost_usd ?? 0).toFixed(4);
    if (this.kpiSpend) this.kpiSpend.textContent = `$${spend}`;

    const avoidable = Number(s.potentialSavingsUSD ?? s.potential_savings_usd ?? 0).toFixed(4);
    if (this.kpiAvoidable) this.kpiAvoidable.textContent = `$${avoidable}`;

    const score = Number(s.pollutionScore ?? s.pollution_score ?? 0);
    if (this.kpiPollutionScore) this.kpiPollutionScore.textContent = `${score.toFixed(1)} / 100`;

    if (this.pollutionMeterFill) {
      this.pollutionMeterFill.style.width = `${Math.min(100, Math.max(0, score))}%`;
      if (score < 20) {
        this.pollutionMeterFill.style.backgroundColor = 'var(--color-success)';
        if (this.pollutionLevelText) this.pollutionLevelText.textContent = 'Pristine Clean';
      } else if (score < 50) {
        this.pollutionMeterFill.style.backgroundColor = 'var(--color-warning)';
        if (this.pollutionLevelText) this.pollutionLevelText.textContent = 'Moderate Bloat';
      } else {
        this.pollutionMeterFill.style.backgroundColor = 'var(--color-critical)';
        if (this.pollutionLevelText) this.pollutionLevelText.textContent = 'Critical Pollution';
      }
    }
  }

  renderRecommendations() {
    if (!this.recommendationsFeed) return;
    this.recommendationsFeed.innerHTML = '';

    if (this.recommendationsCount) {
      this.recommendationsCount.textContent = `${this.violations.length} active`;
    }

    if (!this.violations || this.violations.length === 0) {
      this.recommendationsFeed.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">✨</div>
          <div style="font-weight: 600; color: var(--text-heading);">Zero Context Violations</div>
          <div style="font-size: 12px; margin-top: 4px;">Context cache boundaries and prompt sizing are optimal.</div>
        </div>
      `;
      return;
    }

    // Sort CRITICAL -> WARN -> INFO
    const priorityOrder = { CRITICAL: 0, WARN: 1, INFO: 2 };
    const sorted = [...this.violations].sort((a, b) => {
      const pA = priorityOrder[a.severity] ?? 3;
      const pB = priorityOrder[b.severity] ?? 3;
      return pA - pB;
    });

    sorted.forEach((v) => {
      const card = document.createElement('div');
      const sev = v.severity || 'INFO';
      card.className = `violation-card severity-${sev}`;

      const badgeClass = sev === 'CRITICAL' ? 'badge-critical' : sev === 'WARN' ? 'badge-warn' : 'badge-info';
      const wasteVal = v.estimated_waste_usd ?? v.estimatedWasteUSD;
      const wasteStr = wasteVal !== undefined && wasteVal !== null ? `$${Number(wasteVal).toFixed(4)} waste` : '';

      card.innerHTML = `
        <div class="violation-header">
          <div class="violation-title-group">
            <span class="badge ${badgeClass}">${sev}</span>
            <span class="violation-title">${v.title || v.rule_id || v.ruleId || 'Heuristic Alert'}</span>
          </div>
          ${wasteStr ? `<span class="violation-waste">${wasteStr}</span>` : ''}
        </div>
        <div class="violation-msg">${v.message || ''}</div>
        ${v.suggested_fix || v.suggestedFix ? `<div class="violation-fix">💡 Fix: ${v.suggested_fix || v.suggestedFix}</div>` : ''}
      `;
      this.recommendationsFeed.appendChild(card);
    });
  }

  selectTurn(turnIndex) {
    this.selectedTurnIndex = turnIndex;
    const turn = this.turns.find(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === turnIndex
    );
    if (!turn) return;

    const tIdx = turn.turn_index !== undefined ? turn.turn_index : turn.turnIndex;
    if (this.turnTitle) {
      this.turnTitle.textContent = `Turn #${tIdx} Inspector`;
    }

    if (this.turnMetaRibbon) {
      const inp = (turn.input_tokens ?? turn.inputTokens ?? 0).toLocaleString();
      const out = (turn.output_tokens ?? turn.outputTokens ?? 0).toLocaleString();
      const cached = (
        turn.cached_read_tokens ??
        turn.cachedReadTokens ??
        (turn.cache && (turn.cache.readTokens ?? turn.cache.read_tokens)) ??
        0
      ).toLocaleString();
      const durVal = turn.duration_ms ?? turn.durationMs;
      const dur = durVal !== undefined && durVal !== null ? `${Number(durVal).toFixed(0)}ms` : '—';
      const ttftVal = turn.ttft_ms ?? turn.ttftMs;
      const ttft = ttftVal !== undefined && ttftVal !== null ? `${Number(ttftVal).toFixed(0)}ms` : '—';
      const costVal = turn.turn_cost_usd ?? turn.turnCostUSD;
      const cost = costVal !== undefined && costVal !== null ? `$${Number(costVal).toFixed(4)}` : '$0.0000';

      this.turnMetaRibbon.innerHTML = `
        <div class="turn-meta-item"><span class="label">Input:</span><span class="val">${inp} tok</span></div>
        <div class="turn-meta-item"><span class="label">Output:</span><span class="val">${out} tok</span></div>
        <div class="turn-meta-item"><span class="label">Cached Read:</span><span class="val">${cached} tok</span></div>
        <div class="turn-meta-item"><span class="label">Duration:</span><span class="val">${dur}</span></div>
        <div class="turn-meta-item"><span class="label">TTFT:</span><span class="val">${ttft}</span></div>
        <div class="turn-meta-item"><span class="label">Turn Cost:</span><span class="val">${cost}</span></div>
      `;
    }

    this.renderBlocksTable(turn);
  }

  renderBlocksTable(turn) {
    if (!this.blocksTableBody) return;
    this.blocksTableBody.innerHTML = '';

    // Collect all context blocks
    let blocks = [];
    if (turn.all_blocks && turn.all_blocks.length > 0) {
      blocks = turn.all_blocks;
    } else if (turn.blocks && turn.blocks.length > 0) {
      blocks = turn.blocks;
    } else {
      blocks = [
        ...(turn.system_blocks || turn.systemBlocks || []),
        ...(turn.tool_defs || turn.toolDefs || []),
        ...(turn.conversation_history || turn.conversationHistory || []),
        ...(turn.tool_results || turn.toolResults || []),
        ...(turn.assistant_blocks || turn.assistantBlocks || []),
      ];
    }

    if (blocks.length === 0) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">
            No context blocks found for this turn.
          </td>
        </tr>
      `;
      return;
    }

    blocks.forEach((b) => {
      const row = document.createElement('tr');
      const bId = b.block_id || b.blockId || '—';
      const bType = b.block_type || b.blockType || 'block';
      const tokCount = b.token_count ?? b.tokenCount ?? 0;
      const hash = b.content_hash || b.contentHash || '';
      const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
      const survived = b.turns_survived ?? b.turnsSurvived;
      const survivedText = survived !== undefined ? `${survived} turns` : '—';

      row.innerHTML = `
        <td class="code-cell">${bId}</td>
        <td><span class="badge badge-info">${bType}</span></td>
        <td style="font-family: var(--font-mono);">${tokCount.toLocaleString()}</td>
        <td>${survivedText}</td>
        <td class="hash-cell">${hashShort}</td>
        <td>
          <button class="btn" style="padding: 2px 8px; font-size: 11px;">View Content</button>
        </td>
      `;

      const viewBtn = row.querySelector('button');
      if (viewBtn) {
        viewBtn.addEventListener('click', () => {
          this.openModal(`Block: ${bId} (${bType})`, b.content || JSON.stringify(b, null, 2));
        });
      }

      this.blocksTableBody.appendChild(row);
    });
  }

  renderEmptyTurnInspector() {
    if (this.turnTitle) this.turnTitle.textContent = 'Turn Inspector';
    if (this.turnMetaRibbon) this.turnMetaRibbon.innerHTML = '<span>No turn selected</span>';
    if (this.blocksTableBody) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">
            No turns available in this session.
          </td>
        </tr>
      `;
    }
  }

  _populateDiffSelects() {
    if (!this.diffT1 || !this.diffT2) return;
    const currentT1 = this.diffT1.value;
    const currentT2 = this.diffT2.value;

    this.diffT1.innerHTML = '';
    this.diffT2.innerHTML = '';

    this.turns.forEach((t, i) => {
      const idx =
        t.turn_index !== undefined ? t.turn_index : t.turnIndex !== undefined ? t.turnIndex : i;
      const opt1 = document.createElement('option');
      opt1.value = idx;
      opt1.textContent = `Turn #${idx}`;
      this.diffT1.appendChild(opt1);

      const opt2 = document.createElement('option');
      opt2.value = idx;
      opt2.textContent = `Turn #${idx}`;
      this.diffT2.appendChild(opt2);
    });

    if (this.turns.length >= 2) {
      const prevTurn = this.turns[this.turns.length - 2];
      const lastTurn = this.turns[this.turns.length - 1];
      const prevIdx = prevTurn.turn_index ?? prevTurn.turnIndex ?? 0;
      const lastIdx = lastTurn.turn_index ?? lastTurn.turnIndex ?? 1;
      this.diffT1.value = currentT1 || prevIdx;
      this.diffT2.value = currentT2 || lastIdx;
    }
  }

  async computeDiff() {
    if (!this.activeSessionId || !this.diffT1 || !this.diffT2 || !this.diffResults) return;
    const t1 = this.diffT1.value;
    const t2 = this.diffT2.value;

    if (t1 === '' || t2 === '') return;

    try {
      const res = await fetch(`/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/diff/${t1}/${t2}`);
      if (res.ok) {
        const data = await res.json();
        this.renderDiffResults(data);
      } else {
        const err = await res.json();
        this.diffResults.innerHTML = `<div style="color: var(--color-critical); padding: 8px;">Error: ${err.detail || 'Diff failed'}</div>`;
      }
    } catch (err) {
      console.error('[DashboardApp] Diff calculation failed:', err);
    }
  }

  renderDiffResults(data) {
    if (!this.diffResults) return;
    const growth = data.tokenGrowth || data.token_growth || 0;
    const growthColor = growth > 0 ? 'var(--color-critical)' : 'var(--color-success)';
    const growthPrefix = growth > 0 ? '+' : '';

    const added = data.addedBlockIds || data.added_block_ids || [];
    const removed = data.removedBlockIds || data.removed_block_ids || [];
    const persisted = data.persistedBlockIds || data.persisted_block_ids || [];

    const renderBadges = (arr, badgeClass) => {
      if (arr.length === 0) return '<span style="color: var(--text-secondary); font-size: 11px;">None</span>';
      return arr.map((id) => `<span class="badge ${badgeClass}">${id}</span>`).join(' ');
    };

    this.diffResults.innerHTML = `
      <div class="diff-card">
        <div class="diff-card-title">Token Growth</div>
        <div class="diff-card-value" style="color: ${growthColor};">${growthPrefix}${growth.toLocaleString()} tok</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Added Blocks (${added.length})</div>
        <div class="diff-badge-list">${renderBadges(added, 'badge-critical')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Removed Blocks (${removed.length})</div>
        <div class="diff-badge-list">${renderBadges(removed, 'badge-warn')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Persisted Blocks (${persisted.length})</div>
        <div class="diff-badge-list">${renderBadges(persisted, 'badge-info')}</div>
      </div>
    `;
  }

  exportSession() {
    if (!this.activeSessionId) {
      alert('No active session to export.');
      return;
    }
    const exportUrl = `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/export?format=jsonc`;
    window.location.href = exportUrl;
  }

  openModal(title, content) {
    if (this.modalTitle) this.modalTitle.textContent = title;
    const textRepresentation =
      typeof content === 'object' && content !== null
        ? JSON.stringify(content, null, 2)
        : String(content || '');
    if (this.modalBody) this.modalBody.textContent = textRepresentation;

    if (this.jsonViewer) {
      this.jsonViewer.render(content);
    }
    if (this.modalOverlay) this.modalOverlay.classList.add('active');
  }

  closeModal() {
    if (this.modalOverlay) this.modalOverlay.classList.remove('active');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new DashboardApp();
  app.init();
  window.dashboardApp = app;
});
