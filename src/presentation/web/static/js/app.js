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
    this.exportMdBtn = document.getElementById('export-md-btn');
    this.navDemoBtn = document.getElementById('nav-demo-btn');

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
    this.autoDiffRibbon = document.getElementById('auto-diff-ribbon');
    this.blocksTableBody = document.getElementById('blocks-table-body');
    this.contextProportionBar = document.getElementById('context-proportion-bar');
    this.filterChipsContainer = document.getElementById('blocks-filter-chips');
    this.currentBlockFilter = 'ALL';

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

    if (this.exportMdBtn) {
      this.exportMdBtn.addEventListener('click', () => this.exportMarkdownReport());
    }

    if (this.navDemoBtn) {
      this.navDemoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.loadDemoSession();
      });
    }

    if (this.diffBtn) {
      this.diffBtn.addEventListener('click', () => this.computeDiff());
    }

    if (this.filterChipsContainer) {
      this.filterChipsContainer.addEventListener('click', (e) => {
        const chip = e.target.closest('.filter-chip');
        if (!chip) return;
        const filter = chip.dataset.filter || chip.getAttribute('data-filter');
        if (filter) {
          this.setBlockFilter(filter);
        }
      });
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
      opt.textContent = 'Waiting for agent traffic on proxy...';
      this.sessionSelect.appendChild(opt);
      return;
    }

    this.sessions.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.sessionId;
      const harness = s.agentHarness || s.harness || '';
      const harnessTag = harness && harness !== 'unknown' ? ` [${harness}]` : '';
      const model = s.model && s.model !== 'unknown' && s.model !== 'auto-detect' ? ` (${s.model})` : '';
      opt.textContent = `${s.sessionId}${harnessTag}${model}`;
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

    // Ignore events for other sessions if activeSessionId is set, except session_created and session_erased
    if (this.activeSessionId && sid && this.activeSessionId !== sid) {
      if (
        type === 'session_created' ||
        type === 'SESSION_CREATED' ||
        type === 'session_erased' ||
        type === 'SESSION_ERASED'
      ) {
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
        turnData.violations.forEach((v) => {
          if (v.turn_index === undefined && v.turnIndex === undefined && tIdx !== undefined) {
            v.turn_index = tIdx;
          }
          this.violations.push(v);
        });
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
    } else if (type === 'session_erased' || type === 'SESSION_ERASED') {
      if (sid === this.activeSessionId) {
        this.turns = [];
        this.violations = [];
        this.summary = null;
        if (this.statusText) {
          this.statusText.textContent = 'Erased (Unexported)';
        }
        this.renderAll();
      }
      this.refreshSessions();
    } else if (type === 'session_disconnected' || type === 'SESSION_DISCONNECTED') {
      if (sid === this.activeSessionId) {
        if (this.statusText) {
          this.statusText.textContent = 'Disconnected (Preserved)';
        }
      }
      this.refreshSessions();
    }
  }

  updateConnectionStatus(status) {
    if (!this.statusPill || !this.statusText) return;
    this.statusPill.className = `status-pill ${status}`;
    if (status === 'connected') {
      if (!this.turns || this.turns.length === 0) {
        this.statusText.textContent = 'Listening on 127.0.0.1:8080 - Waiting for agent traffic';
      } else {
        this.statusText.textContent = 'Live Connected';
      }
    } else if (status === 'reconnecting') {
      this.statusText.textContent = 'Reconnecting...';
    } else {
      this.statusText.textContent = 'Listening on 127.0.0.1:8080 - Waiting for agent traffic';
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

    // Determine current turn index
    const currentTurn =
      this.selectedTurnIndex !== null && this.selectedTurnIndex !== undefined
        ? this.selectedTurnIndex
        : (this.turns.length > 0
            ? (this.turns[this.turns.length - 1].turn_index ?? this.turns[this.turns.length - 1].turnIndex ?? 0)
            : 0);

    // Group violations by ruleId or suggestedFix
    const groupsMap = new Map();
    this.violations.forEach((v) => {
      const ruleId = v.rule_id || v.ruleId || '';
      const fix = v.suggested_fix || v.suggestedFix || '';
      const title = v.title || ruleId || 'Alert';
      const groupKey = ruleId || fix || title;
      if (!groupsMap.has(groupKey)) {
        groupsMap.set(groupKey, []);
      }
      groupsMap.get(groupKey).push(v);
    });

    const priorityOrder = { CRITICAL: 0, WARN: 1, INFO: 2 };
    const sortedGroups = Array.from(groupsMap.values()).map((viols) => {
      const sortedViols = [...viols].sort((a, b) => {
        const tA = a.turn_index ?? a.turnIndex ?? 0;
        const tB = b.turn_index ?? b.turnIndex ?? 0;
        return tA - tB;
      });
      const first = sortedViols[0];
      const ruleId = first.rule_id || first.ruleId || 'RULE';
      const title = first.title || ruleId;
      const fix =
        sortedViols.find((x) => x.suggested_fix || x.suggestedFix)?.suggested_fix ||
        sortedViols.find((x) => x.suggested_fix || x.suggestedFix)?.suggestedFix ||
        '';

      let maxSev = 'INFO';
      for (const x of sortedViols) {
        const s = (x.severity || 'INFO').toUpperCase();
        if (s === 'CRITICAL') {
          maxSev = 'CRITICAL';
          break;
        } else if (s === 'WARN') {
          maxSev = 'WARN';
        }
      }

      const earlierViolations = sortedViols.filter(
        (x) => (x.turn_index ?? x.turnIndex ?? 0) < currentTurn
      );
      const currentViolations = sortedViols.filter(
        (x) => (x.turn_index ?? x.turnIndex ?? 0) === currentTurn
      );
      const currentViolation = currentViolations.length > 0 ? currentViolations[0] : null;

      const totalWaste = sortedViols.reduce(
        (sum, x) => sum + Number(x.estimated_waste_usd ?? x.estimatedWasteUSD ?? 0),
        0
      );
      const earlierWaste = earlierViolations.reduce(
        (sum, x) => sum + Number(x.estimated_waste_usd ?? x.estimatedWasteUSD ?? 0),
        0
      );
      const currentWaste = currentViolation
        ? Number(currentViolation.estimated_waste_usd ?? currentViolation.estimatedWasteUSD ?? 0)
        : 0;

      const uniqueTurns = Array.from(
        new Set(sortedViols.map((x) => x.turn_index ?? x.turnIndex ?? 0))
      ).sort((a, b) => a - b);
      const earlierTurns = Array.from(
        new Set(earlierViolations.map((x) => x.turn_index ?? x.turnIndex ?? 0))
      ).sort((a, b) => a - b);

      return {
        ruleId,
        title,
        fix,
        severity: maxSev,
        totalOccurrences: sortedViols.length,
        uniqueTurns,
        earlierViolations,
        earlierTurns,
        currentViolation,
        totalWaste,
        earlierWaste,
        currentWaste,
        violations: sortedViols,
        primaryViolation: currentViolation || sortedViols[sortedViols.length - 1],
      };
    });

    sortedGroups.sort((a, b) => {
      const pA = priorityOrder[a.severity] ?? 3;
      const pB = priorityOrder[b.severity] ?? 3;
      if (pA !== pB) return pA - pB;
      return b.totalWaste - a.totalWaste;
    });

    if (this.recommendationsCount) {
      this.recommendationsCount.textContent = `${sortedGroups.length} active (${this.violations.length} total)`;
    }

    sortedGroups.forEach((group) => {
      const v = group.primaryViolation;
      const card = document.createElement('div');
      const sev = group.severity;
      card.className = `violation-card severity-${sev}`;

      const badgeClass =
        sev === 'CRITICAL' ? 'badge-critical' : sev === 'WARN' ? 'badge-warn' : 'badge-info';
      const wasteStr =
        group.totalWaste > 0 ? `$${group.totalWaste.toFixed(4)} total waste` : '';

      const count = group.totalOccurrences;
      const turnCount = group.uniqueTurns.length;
      const occBadge = `<span class="badge badge-occurrence" style="margin-left: 6px; font-size: 11px; background: rgba(110, 118, 129, 0.2); color: var(--text-secondary); border-radius: 12px; padding: 2px 8px;">${count} violation${count !== 1 ? 's' : ''}${turnCount > 1 ? ` across ${turnCount} turns` : ''}</span>`;

      let currentTurnHtml = '';
      if (group.currentViolation) {
        const curWaste =
          group.currentWaste > 0 ? ` ($${group.currentWaste.toFixed(4)} waste)` : '';
        currentTurnHtml = `
          <div style="margin-bottom: 3px;">
            <strong style="color: var(--text-primary);">Current Turn (#${currentTurn}):</strong> 
            <span style="color: var(--color-warn, #d29922); font-weight: 500;">Active${curWaste}</span>
            ${group.currentViolation.message ? ` — <span style="color: var(--text-secondary);">${group.currentViolation.message}</span>` : ''}
          </div>
        `;
      } else {
        currentTurnHtml = `
          <div style="margin-bottom: 3px;">
            <strong style="color: var(--text-primary);">Current Turn (#${currentTurn}):</strong> 
            <span style="color: var(--color-success, #3fb950); font-weight: 500;">Clean / Not triggered</span>
          </div>
        `;
      }

      let earlierTurnsHtml = '';
      if (group.earlierViolations.length > 0) {
        const earlierWasteStr =
          group.earlierWaste > 0 ? ` ($${group.earlierWaste.toFixed(4)} waste)` : '';
        const turnsList = group.earlierTurns.map((t) => `#${t}`).join(', ');
        earlierTurnsHtml = `
          <div style="color: var(--text-secondary); font-size: 11px;">
            <strong style="color: var(--text-primary);">Earlier Turns (${turnsList}):</strong> 
            ${group.earlierViolations.length} violation${group.earlierViolations.length !== 1 ? 's' : ''}${earlierWasteStr}
          </div>
        `;
      } else {
        earlierTurnsHtml = `
          <div style="color: var(--text-muted); font-size: 11px;">
            <strong style="color: var(--text-primary);">Earlier Turns:</strong> None (first occurrence)
          </div>
        `;
      }

      card.innerHTML = `
        <div class="violation-header">
          <div class="violation-title-group">
            <span class="badge ${badgeClass}">${sev}</span>
            <span class="violation-title">${group.title || group.ruleId || 'Heuristic Alert'}</span>
            ${occBadge}
          </div>
          ${wasteStr ? `<span class="violation-waste">${wasteStr}</span>` : ''}
        </div>
        <div class="violation-breakdown" style="margin: 8px 0 6px 0; font-size: 12px; background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 6px 10px; border: 1px solid rgba(255, 255, 255, 0.06);">
          ${currentTurnHtml}
          ${earlierTurnsHtml}
        </div>
        ${group.fix ? `<div class="violation-fix">💡 Fix: ${group.fix}</div>` : ''}
        <div class="violation-actions">
          <button class="violation-action-btn inspect-culprit-btn" type="button">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: -2px; margin-right: 4px;">
              <path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z"/>
            </svg>
            Inspect Culprit
          </button>
          <button class="violation-action-btn copy-directive-btn" type="button">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: -2px; margin-right: 4px;">
              <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Z"/>
              <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"/>
            </svg>
            Copy Directive
          </button>
        </div>
      `;

      const inspectBtn = card.querySelector('.inspect-culprit-btn');
      if (inspectBtn) {
        inspectBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const targetBlockId =
            v.block_id ||
            v.blockId ||
            (Array.isArray(v.block_ids) && v.block_ids.length > 0 ? v.block_ids[0] : null) ||
            (Array.isArray(v.blockIds) && v.blockIds.length > 0 ? v.blockIds[0] : null);

          let targetTurn =
            v.turn_index !== undefined
              ? v.turn_index
              : v.turnIndex !== undefined
              ? v.turnIndex
              : null;

          if ((targetTurn === null || targetTurn === undefined) && targetBlockId && this.turns) {
            for (const t of this.turns) {
              const blocks =
                t.all_blocks ||
                t.blocks || [
                  ...(t.system_blocks || t.systemBlocks || []),
                  ...(t.tool_defs || t.toolDefs || []),
                  ...(t.conversation_history || t.conversationHistory || []),
                  ...(t.tool_results || t.toolResults || []),
                  ...(t.assistant_blocks || t.assistantBlocks || []),
                ];
              if (blocks.some((b) => (b.block_id || b.blockId) === targetBlockId)) {
                targetTurn = t.turn_index !== undefined ? t.turn_index : t.turnIndex;
                break;
              }
            }
          }

          if (targetTurn !== null && targetTurn !== undefined) {
            this.selectTurn(targetTurn);
          }

          document.getElementById('blocks-table-body')?.scrollIntoView({ behavior: 'smooth', block: 'center' });

          if (targetBlockId && this.blocksTableBody) {
            const rows = Array.from(this.blocksTableBody.querySelectorAll('tr'));
            const matchingRow = rows.find(
              (tr) =>
                tr.dataset.blockId === targetBlockId ||
                tr.querySelector('.code-cell')?.textContent.includes(targetBlockId)
            );

            if (matchingRow) {
              matchingRow.classList.remove('highlight-culprit-row');
              void matchingRow.offsetWidth;
              matchingRow.classList.add('highlight-culprit-row');
              setTimeout(() => {
                matchingRow.classList.remove('highlight-culprit-row');
              }, 2500);
            } else {
              this.showToast(`Turn #${targetTurn ?? '?'}: Culprit block ${targetBlockId} not found in active blocks`);
            }
          } else {
            const turnLabel = targetTurn !== null && targetTurn !== undefined ? `Turn #${targetTurn}` : 'Current turn';
            this.showToast(`⚠️ Turn-level alert: Violation applies across ${turnLabel}`);
            const subtitle = document.querySelector('.inspector-panel .panel-subtitle');
            if (subtitle) {
              const originalText = subtitle.textContent;
              subtitle.textContent = `⚠️ Turn-level alert: ${v.title || v.rule_id || 'Alert'} applies across ${turnLabel}`;
              subtitle.style.color = 'var(--color-warning)';
              setTimeout(() => {
                subtitle.textContent = originalText;
                subtitle.style.color = '';
              }, 2500);
            }
          }
        });
      }

      const copyBtn = card.querySelector('.copy-directive-btn');
      if (copyBtn) {
        copyBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const directive = this.generateDirective(v);
          const originalHTML = copyBtn.innerHTML;

          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(directive);
            } else {
              this.fallbackCopyText(directive);
            }
          } catch (err) {
            console.warn('Clipboard write failed, using fallback:', err);
            this.fallbackCopyText(directive);
          }

          copyBtn.textContent = '✓ Copied!';
          copyBtn.classList.add('copied');
          setTimeout(() => {
            copyBtn.innerHTML = originalHTML;
            copyBtn.classList.remove('copied');
          }, 2000);
        });
      }

      this.recommendationsFeed.appendChild(card);
    });
  }

  setBlockFilter(filter) {
    this.currentBlockFilter = filter;
    if (this.filterChipsContainer) {
      const chips = this.filterChipsContainer.querySelectorAll('.filter-chip');
      chips.forEach((chip) => {
        chip.classList.toggle('active', (chip.dataset.filter || chip.getAttribute('data-filter')) === filter);
      });
    }
    const currentTurn = this.getSelectedTurn();
    if (currentTurn) {
      this.renderBlocksTable(currentTurn);
    }
  }

  getSelectedTurn() {
    return this.turns.find(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
    );
  }

  renderProportionBar(turn) {
    if (!this.contextProportionBar) return;
    this.contextProportionBar.innerHTML = '';

    // Calculate category token aggregates (system_blocks, tool_defs, conversation_history, tool_results)
    let sysTokens = 0;
    let toolTokens = 0;
    let histTokens = 0;
    let resTokens = 0;

    const sysBlocks = turn.system_blocks || turn.systemBlocks;
    const toolBlocks = turn.tool_defs || turn.toolDefs;
    const histBlocks = turn.conversation_history || turn.conversationHistory;
    const resBlocks = turn.tool_results || turn.toolResults;
    const asstBlocks = turn.assistant_blocks || turn.assistantBlocks;

    if (sysBlocks || toolBlocks || histBlocks || resBlocks || asstBlocks) {
      if (sysBlocks) sysTokens = sysBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (toolBlocks) toolTokens = toolBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (histBlocks) histTokens += histBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (asstBlocks) histTokens += asstBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (resBlocks) resTokens = resBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
    }

    // If all are 0, check all_blocks or blocks
    if (sysTokens === 0 && toolTokens === 0 && histTokens === 0 && resTokens === 0) {
      const allBlocks = turn.all_blocks || turn.blocks || [];
      allBlocks.forEach((b) => {
        const type = (b.block_type || b.blockType || '').toLowerCase();
        const count = b.token_count ?? b.tokenCount ?? 0;
        if (type.includes('system')) {
          sysTokens += count;
        } else if (type.includes('tool_def') || type.includes('tool_declaration')) {
          toolTokens += count;
        } else if (type.includes('tool_result')) {
          resTokens += count;
        } else {
          histTokens += count;
        }
      });
    }

    // Fallback to category_breakdown if still 0
    if (sysTokens === 0 && toolTokens === 0 && histTokens === 0 && resTokens === 0) {
      const cb = turn.category_breakdown || turn.categoryBreakdown || turn.token_breakdown || turn.tokenBreakdown;
      if (cb) {
        sysTokens = cb.system || 0;
        toolTokens = cb.tools || cb.tool_defs || 0;
        histTokens = cb.history || cb.conversation_history || cb.conversation || 0;
        resTokens = cb.tool_results || cb.toolResults || cb.results || 0;
      }
    }

    const totalTokens = sysTokens + toolTokens + histTokens + resTokens;

    if (totalTokens === 0) {
      this.contextProportionBar.innerHTML = `
        <div class="proportion-segment empty-bar" style="width: 100%; justify-content: center;">
          No context blocks for this turn
        </div>
      `;
      return;
    }

    const categories = [
      { key: 'system', name: 'System', tokens: sysTokens, class: 'segment-system', filter: 'SYSTEM' },
      { key: 'tools', name: 'Tool Defs', tokens: toolTokens, class: 'segment-tools', filter: 'TOOLS' },
      { key: 'messages', name: 'Messages / History', tokens: histTokens, class: 'segment-messages', filter: 'MESSAGES' },
      { key: 'results', name: 'Tool Results', tokens: resTokens, class: 'segment-results', filter: 'TOOL_RESULTS' },
    ];

    categories.forEach((cat) => {
      if (cat.tokens <= 0) return;
      const pct = (cat.tokens / totalTokens) * 100;
      const segment = document.createElement('div');
      segment.className = `proportion-segment ${cat.class}`;
      segment.style.width = `${pct}%`;
      segment.title = `${cat.name}: ${cat.tokens.toLocaleString()} tok (${pct.toFixed(1)}%) — Click to filter`;

      const label = document.createElement('span');
      label.textContent = `${cat.name}: ${cat.tokens.toLocaleString()} (${pct.toFixed(0)}%)`;
      segment.appendChild(label);

      segment.addEventListener('click', () => {
        this.setBlockFilter(cat.filter);
      });

      this.contextProportionBar.appendChild(segment);
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

    this.renderProportionBar(turn);
    this.renderBlocksTable(turn);
    this.renderRecommendations();

    // Auto-diff (N vs N-1) handling
    if (tIdx === 0 || this.turns.length <= 1) {
      if (this.autoDiffRibbon) {
        this.autoDiffRibbon.innerHTML = `
          <span class="badge badge-info">Turn #0: Initial Prompt Baseline (All blocks initial load)</span>
        `;
      }
    } else {
      this.fetchAutoDiff(tIdx);
    }
  }

  async fetchAutoDiff(turnIndex) {
    if (!this.autoDiffRibbon || !this.activeSessionId) return;

    const prevIndex = turnIndex - 1;
    this._activeDiffTurnIndex = turnIndex;
    this.autoDiffRibbon.innerHTML = `
      <span style="color: var(--text-secondary); font-size: 11px;">
        Comparing Turn #${prevIndex} → Turn #${turnIndex}...
      </span>
    `;

    try {
      const res = await fetch(
        `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/diff/${prevIndex}/${turnIndex}`
      );
      if (this._activeDiffTurnIndex !== turnIndex || this.selectedTurnIndex !== turnIndex) {
        return;
      }
      if (res.ok) {
        const data = await res.json();
        this.renderAutoDiffRibbon(data, prevIndex, turnIndex);
      } else {
        const err = await res.json().catch(() => ({}));
        this.autoDiffRibbon.innerHTML = `
          <span style="color: var(--color-critical); font-size: 11px;">
            Auto-diff unavailable: ${err.detail || "Diff request failed"}
          </span>
        `;
      }
    } catch (err) {
      if (this._activeDiffTurnIndex !== turnIndex || this.selectedTurnIndex !== turnIndex) {
        return;
      }
      console.error("[DashboardApp] Auto-diff failed:", err);
      this.autoDiffRibbon.innerHTML = `
        <span style="color: var(--color-critical); font-size: 11px;">
          Auto-diff calculation failed
        </span>
      `;
    }
  }

  renderAutoDiffRibbon(data, prevIndex, turnIndex) {
    if (!this.autoDiffRibbon) return;

    const growth = data.tokenGrowth ?? data.token_growth ?? 0;
    let deltaFormatted = "";
    let deltaClass = "";

    if (growth > 0) {
      deltaFormatted = `▲ +${growth.toLocaleString()} tokens`;
      deltaClass = "delta-positive";
    } else if (growth < 0) {
      deltaFormatted = `▼ ${growth.toLocaleString()} tokens`;
      deltaClass = "delta-negative";
    } else {
      deltaFormatted = `±0 tokens`;
      deltaClass = "delta-neutral";
    }

    const added = data.addedBlockIds || data.added_block_ids || [];
    const mutated = data.mutatedBlockIds || data.mutated_block_ids || [];
    const evicted = data.removedBlockIds || data.removed_block_ids || [];
    const persisted = data.persistedBlockIds || data.persisted_block_ids || [];
    const breakpoint = data.cacheBreakpointBlockId || data.cache_breakpoint_block_id;

    const breakpointHtml = breakpoint
      ? `<span class="breakpoint-callout">⚡ Cache Breakpoint at [${breakpoint}]: Subsequent blocks re-tokenized</span>`
      : `<span class="badge badge-success">✓ Prefix Cache Intact</span>`;

    this.autoDiffRibbon.innerHTML = `
      <span class="auto-diff-title">Turn #${prevIndex} → #${turnIndex} Delta:</span>
      <span class="delta-pill ${deltaClass}">${deltaFormatted}</span>
      <span class="badge badge-added" title="${added.length ? added.join(", ") : "None"}">${added.length} Added</span>
      <span class="badge badge-mutated" title="${mutated.length ? mutated.join(", ") : "None"}">${mutated.length} Mutated</span>
      <span class="badge badge-evicted" title="${evicted.length ? evicted.join(", ") : "None"}">${evicted.length} Evicted</span>
      <span class="badge badge-persisted" title="${persisted.length ? persisted.join(", ") : "None"}">${persisted.length} Persisted</span>
      ${breakpointHtml}
    `;
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

    // Calculate total turn tokens across all blocks for relative share
    const totalTurnTokens = blocks.reduce(
      (acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0),
      0
    ) || (turn.input_tokens ?? turn.inputTokens ?? 0);

    // Filter rows based on this.currentBlockFilter
    const filter = this.currentBlockFilter || 'ALL';
    const filteredBlocks = blocks.filter((b) => {
      const bType = (b.block_type || b.blockType || '').toLowerCase();
      const status = (b.lifecycle_status || b.status || '').toLowerCase();

      switch (filter) {
        case 'SYSTEM':
          return bType.includes('system');
        case 'TOOLS':
          return bType.includes('tool_def') || bType.includes('tool_declaration');
        case 'MESSAGES':
          return bType.includes('user') || bType.includes('assistant') || bType.includes('conversation');
        case 'TOOL_RESULTS':
          return bType.includes('tool_result');
        case 'ADDED':
          return status === 'added';
        case 'MUTATED':
          return status === 'mutated';
        case 'ALL':
        default:
          return true;
      }
    });

    if (filteredBlocks.length === 0) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">
            No context blocks matching filter "${filter}".
          </td>
        </tr>
      `;
      return;
    }

    filteredBlocks.forEach((b) => {
      const row = document.createElement('tr');
      const bId = b.block_id || b.blockId || '—';
      row.dataset.blockId = bId;
      const bType = b.block_type || b.blockType || 'block';
      const identityKey = b.identity_key || b.identityKey || '';
      const status = b.lifecycle_status || b.status || '';
      const tokCount = b.token_count ?? b.tokenCount ?? 0;
      const hash = b.content_hash || b.contentHash || '';
      const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
      const survived = b.turns_survived ?? b.turnsSurvived;
      const survivedText = survived !== undefined ? `${survived} turns` : '—';

      const pctNum = totalTurnTokens > 0 ? (tokCount / totalTurnTokens) * 100 : 0;
      let pctStr = '0%';
      if (pctNum >= 1) {
        pctStr = `${Math.round(pctNum)}%`;
      } else if (pctNum > 0) {
        pctStr = '<1%';
      }
      const tokenDisplay = `${tokCount.toLocaleString()} tok (${pctStr})`;

      let statusBadge = '';
      if (status === 'added') {
        statusBadge = '<span class="badge badge-added" style="margin-left: 6px;">[+] Added</span>';
      } else if (status === 'mutated') {
        statusBadge = '<span class="badge badge-mutated" style="margin-left: 6px;">[~] Mutated</span>';
      } else if (status === 'evicted') {
        statusBadge = '<span class="badge badge-evicted" style="margin-left: 6px;">[-] Evicted</span>';
      } else if (status === 'persisted') {
        statusBadge = '<span class="badge badge-persisted" style="margin-left: 6px;">[=] Persisted</span>';
      }

      row.innerHTML = `
        <td class="code-cell">
          <div style="font-weight: 600;">${bId}</div>
          ${identityKey ? `<div style="font-size: 11px; color: var(--text-secondary); font-family: var(--font-mono);">${identityKey}</div>` : ''}
        </td>
        <td>
          <span class="badge badge-info">${bType}</span>
          ${statusBadge}
        </td>
        <td style="font-family: var(--font-mono); white-space: nowrap;">${tokenDisplay}</td>
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
    if (this.turnMetaRibbon) this.turnMetaRibbon.innerHTML = '<span>Waiting for proxied agent traffic...</span>';
    if (this.autoDiffRibbon) this.autoDiffRibbon.innerHTML = '';
    if (this.contextProportionBar) this.contextProportionBar.innerHTML = '';
    if (this.blocksTableBody) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 36px 20px; line-height: 1.6;">
            <div style="font-weight: 600; font-size: 15px; color: #e3b341; margin-bottom: 8px;">
              Notice: Unproxied requests are not detected
            </div>
            <div style="font-size: 12px; max-width: 600px; margin: 0 auto; color: var(--text-muted);">
              Automatic OS-level packet proxying without root/VPN is not supported.
              Sessions and turns appear dynamically as soon as an agent routes traffic through ctxins:

              <div class="empty-state-cmd-box">
                <div style="color: #8b949e; margin-bottom: 4px; font-size: 11px;"># Launch agent directly through ctxins proxy:</div>
                <div class="empty-state-cmd-row">
                  <code class="empty-state-cmd-text">ctxins run -- &lt;agent&gt;</code>
                  <button class="empty-state-copy-btn" data-copy="ctxins run -- <agent>" title="Copy command">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="copy-icon">
                      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5z"></path>
                      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5z"></path>
                    </svg>
                    <span class="copy-text">Copy</span>
                    <span class="copy-feedback">Copied!</span>
                  </button>
                </div>

                <div style="color: #8b949e; margin-top: 10px; margin-bottom: 4px; font-size: 11px;"># Or export proxy environment in your agent terminal:</div>
                <div class="empty-state-cmd-row">
                  <code class="empty-state-cmd-text">eval $(ctxins env)</code>
                  <button class="empty-state-copy-btn" data-copy="eval $(ctxins env)" title="Copy command">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="copy-icon">
                      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5z"></path>
                      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5z"></path>
                    </svg>
                    <span class="copy-text">Copy</span>
                    <span class="copy-feedback">Copied!</span>
                  </button>
                </div>

                <div style="color: #8b949e; margin-top: 10px; margin-bottom: 4px; font-size: 11px;"># To unset proxy environment variables when finished:</div>
                <div class="empty-state-cmd-row">
                  <code class="empty-state-cmd-text" style="color: #e3b341;">eval $(ctxins env --unset)</code>
                  <button class="empty-state-copy-btn" data-copy="eval $(ctxins env --unset)" title="Copy command">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="copy-icon">
                      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5z"></path>
                      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5z"></path>
                    </svg>
                    <span class="copy-text">Copy</span>
                    <span class="copy-feedback">Copied!</span>
                  </button>
                </div>
              </div>

              <div style="margin-top: 20px;">
                <button id="load-demo-btn" class="btn btn-demo">
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0zM6.5 5v6l5-3-5-3z"/>
                  </svg>
                  Explore Demo Session
                </button>
              </div>
            </div>
          </td>
        </tr>
      `;
      this._bindEmptyStateActions();
    }
  }

  _bindEmptyStateActions() {
    if (!this.blocksTableBody) return;
    const copyBtns = this.blocksTableBody.querySelectorAll('.empty-state-copy-btn');
    copyBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const text = btn.getAttribute('data-copy');
        if (!text) return;
        this._copyToClipboard(text, btn);
      });
    });

    const demoBtn = document.getElementById('load-demo-btn');
    if (demoBtn) {
      demoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.loadDemoSession();
      });
    }
  }

  _copyToClipboard(text, btnElement) {
    const doFeedback = () => {
      if (!btnElement) return;
      btnElement.classList.add('copied');
      setTimeout(() => {
        btnElement.classList.remove('copied');
      }, 2000);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(doFeedback).catch(() => {
        this._fallbackCopy(text);
        doFeedback();
      });
    } else {
      this._fallbackCopy(text);
      doFeedback();
    }
  }

  _fallbackCopy(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.opacity = '0';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
      document.execCommand('copy');
    } catch (_) {}
    document.body.removeChild(textArea);
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

    if (this.activeSessionId && this.activeSessionId.startsWith('demo-')) {
      const turn1 = this.turns.find((t) => (t.turn_index ?? t.turnIndex ?? 0) == t1);
      const turn2 = this.turns.find((t) => (t.turn_index ?? t.turnIndex ?? 0) == t2);
      if (turn1 && turn2) {
        const b1 = (turn1.all_blocks || turn1.blocks || []).map((b) => b.block_id || b.blockId);
        const b2 = (turn2.all_blocks || turn2.blocks || []).map((b) => b.block_id || b.blockId);
        const b1Set = new Set(b1);
        const b2Set = new Set(b2);

        const added = b2.filter((id) => !b1Set.has(id));
        const removed = b1.filter((id) => !b2Set.has(id));
        const persisted = b2.filter((id) => b1Set.has(id));
        const mutated = (turn2.all_blocks || turn2.blocks || [])
          .filter((b) => (b.lifecycle_status || b.status) === 'mutated')
          .map((b) => b.block_id || b.blockId);
        const tokenGrowth = (turn2.input_tokens ?? turn2.inputTokens ?? 0) - (turn1.input_tokens ?? turn1.inputTokens ?? 0);

        this.renderDiffResults({
          tokenGrowth,
          addedBlockIds: added,
          mutatedBlockIds: mutated,
          removedBlockIds: removed,
          persistedBlockIds: persisted,
          cacheBreakpointBlockId: added.length > 0 ? added[0] : null,
        });
        return;
      }
    }

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
    const mutated = data.mutatedBlockIds || data.mutated_block_ids || [];
    const removed = data.removedBlockIds || data.removed_block_ids || [];
    const persisted = data.persistedBlockIds || data.persisted_block_ids || [];
    const breakpoint = data.cacheBreakpointBlockId || data.cache_breakpoint_block_id || null;

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
        <div class="diff-badge-list">${renderBadges(added, 'badge-added')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Mutated Blocks (${mutated.length})</div>
        <div class="diff-badge-list">${renderBadges(mutated, 'badge-mutated')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Evicted Blocks (${removed.length})</div>
        <div class="diff-badge-list">${renderBadges(removed, 'badge-evicted')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Persisted Blocks (${persisted.length})</div>
        <div class="diff-badge-list">${renderBadges(persisted, 'badge-persisted')}</div>
      </div>
      ${breakpoint ? `
      <div class="diff-card">
        <div class="diff-card-title">Prefix Cache Breakpoint</div>
        <div class="diff-card-value" style="font-size: 12px; color: var(--color-warning); font-family: var(--font-mono);">${breakpoint}</div>
      </div>` : ''}
    `;
  }

  exportSession() {
    if (!this.activeSessionId) {
      alert('No active session to export.');
      return;
    }
    if (this.activeSessionId && this.activeSessionId.startsWith('demo-')) {
      const exportData = {
        sessionId: this.activeSessionId,
        summary: this.summary,
        turns: this.turns,
        violations: this.violations,
        exported_at: new Date().toISOString(),
      };
      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${this.activeSessionId}-export.jsonc`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }
    const exportUrl = `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/export?format=jsonc`;
    window.location.href = exportUrl;
  }

  exportMarkdownReport() {
    if (!this.activeSessionId) {
      alert('No active session to export.');
      return;
    }
    if (this.activeSessionId === 'demo-claude-session') {
      const report = this.generateMarkdownAudit();
      const blob = new Blob([report], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${this.activeSessionId}_optimization_report.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }
    const exportUrl = `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/export?format=markdown`;
    window.location.href = exportUrl;
  }

  generateMarkdownAudit() {
    const s = this.summary || {};
    const totalInput = s.totalInputTokens ?? 0;
    const totalOutput = s.totalOutputTokens ?? 0;
    const totalTokens = totalInput + totalOutput;
    const hitRatio = Math.round((s.cacheHitRatio ?? 0) * 1000) / 10;
    const spend = Number(s.estimatedCostUSD ?? 0).toFixed(4);
    const waste = Number(s.potentialSavingsUSD ?? 0).toFixed(4);
    const score = Number(s.pollutionScore ?? 0).toFixed(1);

    let doc = `# 🔍 ctxins Context Optimization Report: \`${this.activeSessionId}\`\n\n`;
    doc += `- **Generated:** ${new Date().toISOString()}\n`;
    doc += `- **Agent Harness:** Claude Code (Demo)\n`;
    doc += `- **Total Turns:** ${this.turns.length}\n\n`;
    doc += `---\n\n## 📊 Executive Summary & Financial Audit\n\n`;
    doc += `| Metric | Value | Assessment |\n| :--- | :--- | :--- |\n`;
    doc += `| **Total Tokens** | ${totalTokens.toLocaleString()} (${totalInput.toLocaleString()} in / ${totalOutput.toLocaleString()} out) | Combined cumulative context |\n`;
    doc += `| **Prompt Cache Hit %** | ${hitRatio}% | Cached read ratio |\n`;
    doc += `| **Estimated Total Spend** | $${spend} USD | Total model API cost |\n`;
    doc += `| **Avoidable Waste** | **$${waste} USD** | Recoverable financial waste |\n`;
    doc += `| **Context Pollution Score** | **${score} / 100** | Diagnostic score |\n\n`;
    doc += `---\n\n## 🚨 Triggered Context Health Violations\n\n`;

    if (this.violations.length === 0) {
      doc += `> ✨ **Zero Context Violations Detected**\n\n`;
    } else {
      this.violations.forEach((v, i) => {
        const title = v.title || v.rule_id || 'Alert';
        const sev = v.severity || 'INFO';
        const msg = v.message || '';
        const fix = v.suggested_fix || '';
        const wasteVal = v.estimated_waste_usd ? ` ($${Number(v.estimated_waste_usd).toFixed(4)} waste)` : '';
        doc += `### ${i + 1}. [${sev}] ${title}${wasteVal}\n- **Rule ID:** \`${v.rule_id || 'RULE'}\`\n- **Diagnostic:** ${msg}\n- **Remediation:** ${fix}\n\n`;
      });
    }

    doc += `---\n\n## 🛠️ Recommended Directives for AGENTS.md / .cursorrules\n\n`;
    doc += `\`\`\`markdown\n# Context Optimization Directives (ctxins)\n`;
    this.violations.forEach((v) => {
      doc += `${this.generateDirective(v)}\n\n`;
    });
    doc += `\`\`\`\n\n---\n*Report generated by [ctxins](https://github.com/arnabkaycee/ctxins)*\n`;
    return doc;
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

  generateDirective(v) {
    const ruleId = (v.rule_id || v.ruleId || '').toUpperCase();
    if (ruleId.includes('CTX001')) {
      return `# Context Directive: Compact Stale Tool Results
- Summarize tool outputs older than 3 turns into key findings; omit raw stdout/stderr.`;
    } else if (ruleId.includes('CTX002')) {
      return `# Context Directive: Prune Unused Tool Schemas
- Do not include tool schemas in system prompt until invoked or explicitly required.`;
    } else if (ruleId.includes('CACHE001')) {
      return `# Context Directive: Cache Stability
- Keep system prompts and tool declarations deterministic and static at the start of context.`;
    } else {
      const title = v.title || v.rule_id || v.ruleId || 'Context Directive';
      const fix = v.suggested_fix || v.suggestedFix || v.message || 'Optimize context efficiency.';
      return `# Context Directive: ${title}
- ${fix}`;
    }
  }

  showToast(message, duration = 3000) {
    let toastContainer = document.getElementById('ctxins-toast-container');
    if (!toastContainer) {
      toastContainer = document.createElement('div');
      toastContainer.id = 'ctxins-toast-container';
      toastContainer.className = 'ctxins-toast-container';
      document.body.appendChild(toastContainer);
    }
    const toast = document.createElement('div');
    toast.className = 'ctxins-toast';
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('fade-out');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  fallbackCopyText(text) {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.top = '-9999px';
      textarea.style.left = '-9999px';
      textarea.setAttribute('readonly', '');
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    } catch (err) {
      console.warn('[DashboardApp] Fallback copy failed:', err);
    }
  }

  loadDemoSession() {
    const demoData = this._getDemoSessionData();
    const demoSession = {
      sessionId: 'demo-claude-session',
      agentHarness: 'Claude Code',
      model: 'claude-3-7-sonnet',
      turnCount: 4,
      createdAt: new Date().toISOString(),
    };

    if (!this.sessions.some((s) => s.sessionId === demoSession.sessionId)) {
      this.sessions.unshift(demoSession);
    }
    this.activeSessionId = demoSession.sessionId;
    this._populateSessionSelect();

    this.turns = demoData.turns;
    this.violations = demoData.violations;
    this.summary = demoData.summary;

    if (this.statusPill) {
      this.statusPill.className = 'status-pill connected';
    }
    if (this.statusText) {
      this.statusText.textContent = 'Demo Sandbox (claude-3-7-sonnet)';
    }

    this.renderAll();
  }

  _getDemoSessionData() {
    const generateTestOutput = () => {
      const lines = [
        '============================= test session starts =============================',
        'platform darwin -- Python 3.11.8, pytest-8.1.1, pluggy-1.4.0',
        'rootdir: /workspace/ctxins',
        'configfile: pyproject.toml',
        'collected 250 items',
        '',
      ];
      for (let i = 1; i <= 242; i++) {
        const padded = String(i).padStart(3, '0');
        const pct = Math.floor((i / 250) * 100);
        lines.push(`tests/unit/test_module_${padded}.py::test_worker_spec_${padded} PASSED [${pct}%]`);
      }
      lines.push('tests/unit/test_auth.py::test_token_generation PASSED [97%]');
      lines.push('tests/unit/test_auth.py::test_token_refresh PASSED [98%]');
      lines.push('tests/unit/test_auth.py::test_jwt_token_expiry FAILED [99%]');
      lines.push('tests/unit/test_auth.py::test_user_permissions PASSED [100%]');
      lines.push('');
      lines.push('=================================== FAILURES ===================================');
      lines.push('_____________________________ test_jwt_token_expiry _____________________________');
      lines.push('def test_jwt_token_expiry():');
      lines.push('        auth_svc = AuthService(secret="test_secret_k8s")');
      lines.push('        token = auth_svc.issue_jwt(sub="usr_481", ttl_seconds=300)');
      lines.push('>       assert auth_svc.validate_jwt(token, current_time=now + 301) is False');
      lines.push('E       AssertionError: assert True is False');
      lines.push('E       + where True = validate_jwt("eyJhbGciOi...", current_time=1710931501)');
      lines.push('');
      lines.push('tests/unit/test_auth.py:84: AssertionError');
      lines.push('=========================== short test summary info ============================');
      lines.push('FAILED tests/unit/test_auth.py::test_jwt_token_expiry - AssertionError: assert True is False');
      lines.push('======================= 1 failed, 249 passed in 4.12s ==========================');
      return lines.join('\n');
    };

    const turns = [
      {
        turn_index: 0,
        input_tokens: 4500,
        output_tokens: 350,
        cached_read_tokens: 0,
        turn_cost_usd: 0.0135,
        duration_ms: 1850,
        ttft_ms: 320,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 650,
          tool_results: 0,
          thoughts: 150,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 3,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'added',
            content: {
              role: 'system',
              instructions: 'You are Claude Code, an expert agentic software engineer.\nOperate carefully in user workspaces. Read code before editing. Run tests to verify all changes.\nAvoid context bloat and stale result survival.',
            },
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 3,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'added',
            content: [
              {
                name: 'execute_bash',
                description: 'Run commands in isolated bash sandbox environment',
                parameters: { type: 'object', properties: { command: { type: 'string' } }, required: ['command'] },
              },
              {
                name: 'edit_file',
                description: 'Perform precise search-and-replace text modifications to workspace files',
                parameters: { type: 'object', properties: { target_file: { type: 'string' }, old_content: { type: 'string' }, new_content: { type: 'string' } }, required: ['target_file', 'old_content', 'new_content'] },
              },
              {
                name: 'database_query',
                description: 'Execute analytical SQL queries against telemetry data warehouse',
                parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
              },
              {
                name: 'web_search',
                description: 'Perform targeted technical documentation searches',
                parameters: { type: 'object', properties: { q: { type: 'string' } }, required: ['q'] },
              },
            ],
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 3,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'added',
            content: '# Skill: Pytest Testing Standards\nAlways run pytest with concise output and focus on the first failure traceback.',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 3,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'added',
            content: {
              role: 'user',
              content: 'Fix the intermittent authentication test failure in test_auth.py and make sure all tests pass cleanly.',
            },
          },
          {
            block_id: 'blk-tht-turn-0',
            block_type: 'thought',
            identity_key: 'assistant:plan_init',
            token_count: 150,
            turns_survived: 0,
            content_hash: '1c2b4a6f',
            lifecycle_status: 'added',
            content: 'I will first execute the test suite via execute_bash to locate the failing assertion in test_auth.py.',
          },
          {
            block_id: 'blk-call-bash-0',
            block_type: 'assistant',
            identity_key: 'assistant:tool_call:execute_bash',
            token_count: 200,
            turns_survived: 0,
            content_hash: '2d4e6f8a',
            lifecycle_status: 'added',
            content: {
              action: 'call',
              tool: 'execute_bash',
              arguments: { command: 'pytest tests/ -v' },
            },
          },
        ],
      },
      {
        turn_index: 1,
        input_tokens: 6200,
        output_tokens: 420,
        cached_read_tokens: 3800,
        turn_cost_usd: 0.0098,
        duration_ms: 2450,
        ttft_ms: 280,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 920,
          tool_results: 1400,
          thoughts: 180,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 2,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 2,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 2,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 2,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-hist-asst-0',
            block_type: 'conversation_history',
            identity_key: 'assistant:turn_0_response',
            token_count: 270,
            turns_survived: 2,
            content_hash: '4a5b6c7d',
            lifecycle_status: 'added',
            content: 'Running pytest to inspect all test results across 250 unit test cases...',
          },
          {
            block_id: 'blk-result-pytest-250',
            block_type: 'tool_results',
            identity_key: 'tool_result:pytest_250_lines',
            token_count: 1400,
            turns_survived: 1,
            content_hash: '8f9e0a1b',
            lifecycle_status: 'added',
            content: generateTestOutput(),
          },
          {
            block_id: 'blk-tht-turn-1',
            block_type: 'thought',
            identity_key: 'assistant:diagnose_jwt_failure',
            token_count: 180,
            turns_survived: 0,
            content_hash: '6e7f8a9b',
            lifecycle_status: 'added',
            content: 'The failure occurred in test_jwt_token_expiry because token leeway is 0s while clock skew is 1s. Let me edit src/auth/service.py to allow 5s grace leeway.',
          },
          {
            block_id: 'blk-call-edit-1',
            block_type: 'assistant',
            identity_key: 'assistant:tool_call:edit_file',
            token_count: 240,
            turns_survived: 0,
            content_hash: '5c6d7e8f',
            lifecycle_status: 'added',
            content: {
              action: 'call',
              tool: 'edit_file',
              arguments: {
                target_file: 'src/auth/service.py',
                old_content: 'leeway_seconds = 0',
                new_content: 'leeway_seconds = 5',
              },
            },
          },
        ],
      },
      {
        turn_index: 2,
        input_tokens: 7800,
        output_tokens: 510,
        cached_read_tokens: 5600,
        turn_cost_usd: 0.0142,
        duration_ms: 2950,
        ttft_ms: 310,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 2480,
          tool_results: 1400,
          thoughts: 220,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 1,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 1,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 1,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 1,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-result-pytest-250',
            block_type: 'tool_results',
            identity_key: 'tool_result:pytest_250_lines',
            token_count: 1400,
            turns_survived: 1,
            content_hash: '8f9e0a1b',
            lifecycle_status: 'persisted',
            content: generateTestOutput(),
          },
          {
            block_id: 'blk-result-edit-file',
            block_type: 'tool_results',
            identity_key: 'tool_result:edit_file_ack',
            token_count: 120,
            turns_survived: 1,
            content_hash: '3b4c5d6e',
            lifecycle_status: 'added',
            content: { success: true, target_file: 'src/auth/service.py', patch_applied: true },
          },
          {
            block_id: 'blk-result-git-status',
            block_type: 'tool_results',
            identity_key: 'tool_result:git_status',
            token_count: 180,
            turns_survived: 1,
            content_hash: '2a3b4c5d',
            lifecycle_status: 'added',
            content: 'On branch main\nChanges not staged for commit:\n\tmodified:   src/auth/service.py\n\nno changes added to commit (use "git add" to track)',
          },
          {
            block_id: 'blk-tht-turn-2',
            block_type: 'thought',
            identity_key: 'assistant:check_retest',
            token_count: 220,
            turns_survived: 0,
            content_hash: '9e0a1b2c',
            lifecycle_status: 'added',
            content: 'The patch was cleanly applied to src/auth/service.py. Now executing git status and preparing to run the verification test.',
          },
          {
            block_id: 'blk-call-bash-2',
            block_type: 'assistant',
            identity_key: 'assistant:tool_call:retest',
            token_count: 210,
            turns_survived: 0,
            content_hash: '8d9e0a1b',
            lifecycle_status: 'added',
            content: {
              action: 'call',
              tool: 'execute_bash',
              arguments: { command: 'pytest tests/unit/test_auth.py::test_jwt_token_expiry' },
            },
          },
        ],
      },
      {
        turn_index: 3,
        input_tokens: 8000,
        output_tokens: 720,
        cached_read_tokens: 6800,
        turn_cost_usd: 0.0110,
        duration_ms: 1650,
        ttft_ms: 260,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 4180,
          tool_results: 0,
          thoughts: 120,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 0,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 0,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 0,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 0,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-history-consolidated',
            block_type: 'conversation_history',
            identity_key: 'history:turns_1_2_context',
            token_count: 3530,
            turns_survived: 0,
            content_hash: '4d5e6f7a',
            lifecycle_status: 'added',
            content: 'History: Identified test failure in test_jwt_token_expiry, modified src/auth/service.py leeway to 5s, verified git working directory status.',
          },
          {
            block_id: 'blk-tht-turn-3',
            block_type: 'thought',
            identity_key: 'assistant:final_wrapup',
            token_count: 120,
            turns_survived: 0,
            content_hash: '1a2b3c4d',
            lifecycle_status: 'added',
            content: 'Verification test passed with 1 passed in 0.08s. Task completed successfully.',
          },
          {
            block_id: 'blk-asst-success',
            block_type: 'assistant',
            identity_key: 'assistant:final_message',
            token_count: 720,
            turns_survived: 0,
            content_hash: '9b8a7c6d',
            lifecycle_status: 'added',
            content: 'I have investigated the flaky authentication test and identified that `test_jwt_token_expiry` was failing due to 0-second clock skew tolerance. By updating `leeway_seconds = 5` in `src/auth/service.py`, token expiry validation now accounts for realistic cluster clock jitter. All test suites pass successfully!',
          },
        ],
      },
    ];

    const violations = [
      {
        rule_id: 'CTX001',
        ruleId: 'CTX001',
        title: 'Stale Tool Output in Turn 2',
        severity: 'WARN',
        message: 'Tool result from Turn 1 (250 lines of pytest output, 1,400 tokens) survived into Turn 2 unreferenced, creating context drag.',
        suggested_fix: 'Truncate tool outputs or evict unreferenced execution results after subsequent tool invocations to recover context bandwidth.',
        estimated_waste_usd: 0.0042,
        estimatedWasteUSD: 0.0042,
        affected_turns: [2],
      },
      {
        rule_id: 'CTX002',
        ruleId: 'CTX002',
        title: 'Unused Tool Schema Bloat',
        severity: 'WARN',
        message: '3 tool definitions (database_query, web_search, deploy_preview totaling 1,650 tokens) were declared in system prompt but never invoked across the session.',
        suggested_fix: 'Use dynamic tool provisioning or defer unneeded tool schemas to specialized subagents to conserve prompt cache tokens.',
        estimated_waste_usd: 0.0085,
        estimatedWasteUSD: 0.0085,
        affected_turns: [0, 1, 2, 3],
      },
    ];

    const summary = {
      totalInputTokens: 26500,
      totalOutputTokens: 2000,
      totalTokens: 28500,
      cacheHitRatio: 0.685,
      estimatedCostUSD: 0.0485,
      potentialSavingsUSD: 0.0127,
      pollutionScore: 34.2,
    };

    return { turns, violations, summary };
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new DashboardApp();
  app.init();
  window.dashboardApp = app;
});
